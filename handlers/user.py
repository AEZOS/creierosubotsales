from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from utils.qr_gen import generate_ltc_qr
from database import add_user, DB_PATH, get_and_create_sale, is_silent_mode, get_item_stats, get_user_total_sales, is_blackmagic_on
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, InputMediaPhoto, BufferedInputFile
from aiogram.fsm.context import FSMContext
from datetime import datetime, timedelta
from utils.keyboards import main_menu
from utils.black_magic import generate_black_magic_image
from config import DEPOSIT_TIMEOUT_MINUTES, ADMIN_IDS
from handlers.states import ReviewState, SupportTicketState
import os
from utils.tatum import check_ltc_transaction
from utils.ltc_price import get_ltc_ron_price, ron_to_ltc
import aiosqlite
import logging
import asyncio
import time
# Localization removed for root bot

router = Router()

# --- INVENTORY CACHE ---
# This ensures that even if DB is busy or internet is slow, 
# navigation remains 'live' and snappy.
inventory_cache = {
    "categories": [],
    "items": {},
    "last_update": 0
}

async def update_inventory_cache():
    """Periodically refresh the inventory cache from DB."""
    global inventory_cache
    now = time.time()
    if now - inventory_cache["last_update"] < 300: # 5 minute TTL
        return
        
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM categories WHERE is_hidden = 0") as cursor:
                inventory_cache["categories"] = [dict(r) for r in await cursor.fetchall()]
            async with db.execute("SELECT * FROM items WHERE is_hidden = 0") as cursor:
                items_list = [dict(r) for r in await cursor.fetchall()]
                inventory_cache["items"] = {i['id']: i for i in items_list}
            inventory_cache["last_update"] = now
            logging.info("Inventory cache updated.")
    except Exception as e:
        logging.error(f"Failed to update inventory cache: {e}")

# Cooldown for buttons (Anti-spam)
button_cooldowns = {}  # (user_id, callback_data) -> last_press_time
BOT_START_TIME = time.time()
active_verifications = set()  # sale_id
verification_attempts = {}  # user_id -> {'count': int, 'block_until': float}
admin_intention_messages = {} # sale_id -> [(admin_id, message_id, original_text)]

async def check_and_show_pending(event: CallbackQuery | Message) -> bool:
    """Check if user has a pending order and show it if they do. Returns True if pending was found."""
    user_tg_id = event.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT sales.id, items.name, sales.amount_expected, sales.address_used, sales.created_at, items.price_ron, sales.status
            FROM sales 
            JOIN items ON sales.item_id = items.id 
            JOIN users ON sales.user_id = users.id
            WHERE users.telegram_id = ? AND sales.status IN ('pending', 'confirming')
        """, (user_tg_id,)) as cursor:
            pending = await cursor.fetchone()

    if pending:
        sale_id, item_name, amount_ltc, address, created_at, price_ron, status = pending
        
        # Calculate time left
        created_dt = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S')
        expiry_dt = created_dt + timedelta(minutes=DEPOSIT_TIMEOUT_MINUTES)
        now = datetime.now()
        
        # Don't auto-cancel if it's already confirming
        if now > expiry_dt and status == 'pending':
            # Silent auto-cancel if they try to access an expired order
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE sales SET status = 'cancelled' WHERE id = ? AND status = 'pending'", (sale_id,))
                await db.execute("UPDATE addresses SET in_use_by_sale_id = NULL, locked_until = NULL WHERE in_use_by_sale_id = ?", (sale_id,))
                await db.commit()

            if isinstance(event, CallbackQuery):
                await event.answer("⚠️ Comanda ta a expirat și a fost anulată.", show_alert=True)
                try:
                    await event.message.delete()
                except: pass
                # Redirect user to start to get a fresh menu
                await event.message.answer("Comanda a expirat. Te rugăm să folosești /start pentru o nouă comandă.")
            else:
                await event.answer("⚠️ Comanda anterioară a expirat. Folosește /start pentru a începe una nouă.")
            return False 
            
        time_left = expiry_dt - now
        minutes_left = max(0, int(time_left.total_seconds() // 60))
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Verifică Plata", callback_data=f"verify_pay_{sale_id}")],
            [InlineKeyboardButton(text="❌ Anulează Comanda", callback_data=f"cancel_order_{sale_id}")]
        ])
        
        text = (
            f"⏳ <b>COMANDĂ ACTIVĂ</b>\n"
            f"🆔 <b>ID Comandă:</b> <code>#{sale_id}</code>\n"
            f"Status: <code>{status.upper()}</code>\n\n"
            f"Ai o comandă activă pentru: <b>{item_name}</b>\n\n"
            f"💰 <b>Sumă MINIMĂ:</b> <code>{amount_ltc}</code> LTC\n"
            f"📍 <b>Adresă LTC:</b> <code>{address}</code>\n\n"
            f"📊 <b>Confirmări necesare:</b> <code>1</code>\n"
            f"⏰ <b>Expiră în:</b> <code>{minutes_left} minute</code>\n\n"
            f"<i>Botul verifică automat rețeaua. Livrarea se face INSTANT după prima confirmare.</i>"
        )
        
        if isinstance(event, CallbackQuery):
            try:
                qr_file = generate_ltc_qr(address, amount_ltc)
                if event.message.photo:
                    await event.message.edit_media(
                        media=InputMediaPhoto(media=qr_file, caption=text),
                        reply_markup=kb
                    )
                else:
                    await event.message.answer_photo(photo=qr_file, caption=text, reply_markup=kb)
                    await event.message.delete()
            except Exception as e:
                # Catch the TelegramBadRequest if message is not modified
                if "is not modified" not in str(e):
                    logging.error(f"Error showing pending with QR: {e}")
                if event.message.photo: 
                    try:
                        await event.message.edit_caption(caption=text, reply_markup=kb)
                    except Exception: pass
                else: 
                    try:
                        await event.message.edit_text(text, reply_markup=kb)
                    except Exception: pass
            await event.answer()
        else:
            qr_file = generate_ltc_qr(address, amount_ltc)
            await event.answer_photo(photo=qr_file, caption=text, reply_markup=kb)
        return True
    return False

async def check_cooldown(callback: CallbackQuery) -> bool:
    """Returns True if user is on cooldown for THIS specific button, False otherwise."""
    user_id = callback.from_user.id
    btn_data = callback.data
    now = time.time()
    
    key = (user_id, btn_data)
    # Per-button cooldown to prevent double taps/spam (1s)
    if key in button_cooldowns:
        if now - button_cooldowns[key] < 1.0: 
            await callback.answer("⏳ Ai răbdare...", show_alert=False)
            return True
            
    # Global cooldown (0.3s) - helps with DB concurrency but allows fast navigation
    global_key = (user_id, "global_cooldown")
    # Exempt navigation buttons from global cooldown for better UX
    is_nav = btn_data.startswith(("nav_", "menu_", "shop_cat_"))
    if not is_nav and global_key in button_cooldowns:
        if now - button_cooldowns[global_key] < 0.3:
            return True 
            
    button_cooldowns[key] = now
    button_cooldowns[global_key] = now
    return False

@router.message(Command("pending", prefix="!/"))
async def cmd_pending(message: Message):
    if not await check_and_show_pending(message):
        await message.answer("ℹ️ Nu ai nicio comandă activă în acest moment.")

@router.message(CommandStart())
async def cmd_start(message: Message):
    if await check_and_show_pending(message): return

    user_id = message.from_user.id
    username = message.from_user.username
    await add_user(user_id, username)
    

    welcome_text = (
        "🏙 <b>Seiful Digital Premium</b>\n\n"
        "Bun venit în cel mai securizat magazin digital. "
        "Plăți LTC verificate cu livrare instantanee.\n\n"
        "🛒 <b>Alege o categorie de mai jos pentru a începe.</b>"
    )
    
    kb = main_menu()
    if user_id in ADMIN_IDS:
        kb.inline_keyboard.append([InlineKeyboardButton(text="🛠 Panou Admin", callback_data="admin_main")])
    
    banner_path = "assets/2creier.jpg"
    if os.path.exists(banner_path):
        photo = FSInputFile(banner_path)
        await message.answer_photo(photo, caption=welcome_text, reply_markup=kb)
    else:
        await message.answer(welcome_text, reply_markup=kb)

@router.message(Command("pending"))
async def cmd_pending(message: Message):
    if message.from_user.id in ADMIN_IDS:
        from handlers.admin import cmd_pending_orders
        await cmd_pending_orders(message)

@router.callback_query(F.data == "menu_profile")
async def cb_menu_profile(callback: CallbackQuery):
    if await check_cooldown(callback): return
    if await check_and_show_pending(callback): return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT items.name, sales.amount_expected, sales.created_at, sales.id, items.price_ron, sales.status
            FROM sales 
            JOIN items ON sales.item_id = items.id 
            JOIN users ON sales.user_id = users.id
            WHERE users.telegram_id = ?
            ORDER BY sales.created_at DESC
            LIMIT 10
        """, (callback.from_user.id,)) as cursor:
            orders = await cursor.fetchall()
            
    user = callback.from_user
    full_name = f"{user.first_name} {user.last_name or ''}".strip()
    username = f" (@{user.username})" if user.username else ""
    
    text = (
        f"👤 <b>Profil Utilizator</b>\n\n"
        f"🆔 <b>ID:</b> <code>{user.id}</code>\n"
        f"👤 <b>Nume:</b> {full_name}{username}\n\n"
        f"📦 <b>Istoric Comenzi (Ultimele 10):</b>\n"
    )
    
    kb_buttons = []
    if not orders:
        text += "<i>Momentan nu ai nicio comandă.</i>"
    else:
        for o in orders:
            status_map = {
                'paid': '✅ Finalizată',
                'cancelled': '❌ Anulată',
                'pending': '⏳ În așteptare',
                'confirming': '🔄 Verificare'
            }
            s_label = status_map.get(o[5], o[5])
            text += f"🔹 #{o[3]} | <b>{o[0]}</b>\nPreț: {int(o[4])} RON | {s_label}\n\n"
            if o[5] == 'paid':
                kb_buttons.append([InlineKeyboardButton(text=f"👁 Vezi Conținut #{o[3]}", callback_data=f"view_secret_{o[3]}")])
            elif o[5] in ('pending', 'confirming'):
                kb_buttons.append([InlineKeyboardButton(text=f"🛍 Vezi Comandă Activă #{o[3]}", callback_data="check_pending_manual")])
        
    # Add review buttons for completed orders
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT s.id, i.name, r.id
            FROM sales s
            JOIN items i ON s.item_id = i.id
            LEFT JOIN reviews r ON s.id = r.sale_id
            WHERE s.user_id = (SELECT id FROM users WHERE telegram_id = ?)
              AND s.status = 'paid'
            ORDER BY s.id DESC LIMIT 10
        """, (callback.from_user.id,)) as cursor:
            recent_paid = await cursor.fetchall()

    if recent_paid:
        text += "\n⭐ <b>Lasă o Recenzie:</b>\n"
        for s_id, s_iname, s_rev_id in recent_paid:
            if s_rev_id:
                kb_buttons.append([InlineKeyboardButton(text=f"✅ {s_iname} (Recenzat)", callback_data="noop")])
            else:
                kb_buttons.append([InlineKeyboardButton(text=f"⭐ Recenzie - {s_iname}", callback_data=f"write_rev_{s_id}")])

    kb_buttons.append([InlineKeyboardButton(text="🔙 Înapoi", callback_data="menu_start")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)
    
    img_path = "assets/welcome_banner.png"
    
    if os.path.exists(img_path):
        photo = FSInputFile(img_path)
        if callback.message.photo:
            try:
                await callback.message.edit_media(media=InputMediaPhoto(media=photo, caption=text), reply_markup=kb)
            except Exception:
                await callback.message.edit_caption(caption=text, reply_markup=kb)
        else:
            await callback.message.answer_photo(photo, caption=text, reply_markup=kb)
            await callback.message.delete()
    else:
        if callback.message.photo:
            await callback.message.delete()
            await callback.message.answer(text, reply_markup=kb)
        else:
            await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()

@router.callback_query(F.data.startswith("view_secret_"))
async def cb_view_order_secret(callback: CallbackQuery):
    if await check_cooldown(callback): return
    if await check_and_show_pending(callback): return
    sale_id = int(callback.data.split("_")[2])
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT items.name, users.telegram_id, img.secret_group, img.id
            FROM sales
            JOIN items ON sales.item_id = items.id
            JOIN users ON sales.user_id = users.id
            JOIN item_images img ON sales.image_id = img.id
            WHERE sales.id = ? AND sales.status = 'paid'
        """, (sale_id,)) as cursor:
            data = await cursor.fetchone()
            
    if not data or data[1] != callback.from_user.id:
        await callback.answer("Comandă neautorizată sau inexistentă.", show_alert=True)
        return
        
    name, user_tg_id, group_id, first_img_id = data
    
    async with aiosqlite.connect(DB_PATH) as db:
        if group_id:
            async with db.execute("SELECT image_url, media_type, caption FROM item_images WHERE secret_group = ?", (group_id,)) as cursor:
                contents = await cursor.fetchall()
        else:
            async with db.execute("SELECT image_url, media_type, caption FROM item_images WHERE id = ?", (first_img_id,)) as cursor:
                contents = await cursor.fetchall()

    msg_text = f"📦 <b>Conținut Comandă #{sale_id}</b>\nProdus: <b>{name}</b>"
    await callback.bot.send_message(user_tg_id, msg_text)

    for val, m_type, capt in contents:
        try:
            # Special check for 'encrypted' (stale) data from migration
            if isinstance(val, str) and "🔐 [ENCRYPTED-DATA" in val:
                enc_banner = (
                    "🔐 <b>ENCRYPTED PACKAGE DETECTED</b>\n\n"
                    f"<code>{val}</code>\n\n"
                    "<i>Acest pachet a fost securizat în timpul migrării botului. Toate datele noi (stocul proaspăt) vor fi livrate direct ca imagini sau videoclipuri premium conform instrucțiunilor vânzătorului.</i>"
                )
                await callback.bot.send_message(user_tg_id, enc_banner)
                continue

            if m_type == 'photo':
                await callback.bot.send_photo(user_tg_id, photo=val, caption=capt)
            elif m_type == 'video':
                await callback.bot.send_video(user_tg_id, video=val, caption=capt)
            else:
                await callback.bot.send_message(user_tg_id, f"<code>{val}</code>")
        except Exception as e:
            logging.error(f"Error sending secret to user: {e}")
            await callback.bot.send_message(user_tg_id, f"⚠️ <i>Conținut indisponibil (File Reference Stale)</i>\n\n<code>{val}</code>")
        
    await callback.answer("Ți-am retrimis mesajele cu stocul!", show_alert=True)

@router.callback_query(F.data == "menu_support")
async def cb_menu_support(callback: CallbackQuery):
    if await check_cooldown(callback): return
    if await check_and_show_pending(callback): return
    text = (
        "💬 <b>Centru de Suport</b>\n\n"
        "Ai nevoie de ajutor cu o comandă sau ai întrebări despre produse?\n\n"
        "👤 Contact Admin: @creierosuz\n"
        "🕒 Program: NON-STOP (24/7)\n\n"
        "Te rugăm să incluzi ID-ul comenzii dacă ai o problemă cu o plată."
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Înapoi", callback_data="menu_start")]])
    
    img_path = "assets/support.png"
    if os.path.exists(img_path):
        photo = FSInputFile(img_path)
        if callback.message.photo:
            try:
                await callback.message.edit_media(media=InputMediaPhoto(media=photo, caption=text), reply_markup=kb)
            except Exception:
                await callback.message.edit_caption(caption=text, reply_markup=kb)
        else:
            await callback.message.answer_photo(photo, caption=text, reply_markup=kb)
            await callback.message.delete()
    else:
        if callback.message.photo:
            await callback.message.delete()
            await callback.message.answer(text, reply_markup=kb)
        else:
            await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()

@router.callback_query(F.data == "menu_shop")
async def cb_menu_shop(callback: CallbackQuery):
    if await check_cooldown(callback): return
    if await check_and_show_pending(callback): return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT c.id, c.name,
                (
                    SELECT (COUNT(DISTINCT secret_group) + COUNT(CASE WHEN secret_group IS NULL THEN 1 END))
                    FROM item_images im
                    JOIN items i ON im.item_id = i.id
                    WHERE i.category_id = c.id AND im.is_sold = 0
                ) -
                (SELECT COUNT(*) FROM items i JOIN sales s ON i.id = s.item_id WHERE i.category_id = c.id AND s.status = 'confirming') as stock_count
            FROM categories c
            WHERE c.is_hidden = 0
        """) as cursor:
            cats = await cursor.fetchall()
            
    if not cats:
        await callback.message.edit_text("Momentan nu există categorii disponibile.")
        await callback.answer()
        return

    kb_rows = []
    current_row = []
    for cat in cats:
        cat_id, cat_name, stock = cat
        btn_text = f"{cat_name}"
        style = "success" if stock and stock > 0 else "danger"
        current_row.append(InlineKeyboardButton(text=btn_text, callback_data=f"shop_cat_{cat_id}", **{"style": style}))
            
        if len(current_row) == 3:
            kb_rows.append(current_row)
            current_row = []
    if current_row:
        kb_rows.append(current_row)
    
    kb_rows.append([InlineKeyboardButton(text="🔙 Înapoi", callback_data="menu_start")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    
    label = "💎 <b>Alege o Categorie:</b>"
    img_path = "assets/shop.png"
    if os.path.exists(img_path):
        photo = FSInputFile(img_path)
        if callback.message.photo:
            try:
                await callback.message.edit_media(media=InputMediaPhoto(media=photo, caption=label), reply_markup=kb)
            except Exception:
                await callback.message.edit_caption(caption=label, reply_markup=kb)
        else:
            await callback.message.answer_photo(photo, caption=label, reply_markup=kb)
            await callback.message.delete()
    else:
        if callback.message.photo:
            await callback.message.delete()
            await callback.message.answer(label, reply_markup=kb)
        else:
            await callback.message.edit_text(label, reply_markup=kb)
    await callback.answer()

@router.callback_query(F.data == "menu_start")
async def cb_menu_start(callback: CallbackQuery):
    if await check_cooldown(callback): return
    if await check_and_show_pending(callback): return
    
    welcome_text = "🏙 <b>Seiful Digital Premium</b>\n\n🛒 Alege o categorie sau folosește meniul de mai jos."
    kb = main_menu()
    if callback.from_user.id in ADMIN_IDS:
        kb.inline_keyboard.append([InlineKeyboardButton(text="🛠 Panou Admin", callback_data="admin_main")])
        
    img_path = "assets/2creier.jpg"

    if callback.message.photo and os.path.exists(img_path):
        await callback.message.edit_media(
            media=InputMediaPhoto(media=FSInputFile(img_path), caption=welcome_text),
            reply_markup=kb
        )
    else:
        if os.path.exists(img_path):
            await callback.message.answer_photo(FSInputFile(img_path), caption=welcome_text, reply_markup=kb)
            await callback.message.delete()
        else:
            await callback.message.edit_text(welcome_text, reply_markup=kb)
    await callback.answer()

@router.callback_query(F.data.startswith("shop_cat_"))
async def cb_shop_cat(callback: CallbackQuery):
    if await check_cooldown(callback): return
    if await check_and_show_pending(callback): return
    
    parts = callback.data.split("_")
    if len(parts) >= 3 and parts[2].isdigit():
        await show_category_logic(callback, int(parts[2]))
    else:
        await callback.answer("Eroare categorie", show_alert=True)

async def show_category_logic(callback: CallbackQuery, cat_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT name, display_image, description FROM categories WHERE id = ?", (cat_id,)) as cursor:
            cat_info = await cursor.fetchone()
            
        if not cat_info:
            await callback.answer("Categoria nu a fost găsită.", show_alert=True)
            return
            
        cat_name, cat_img, cat_desc = cat_info

        # Get average rating for this category
        async with db.execute("""
            SELECT AVG(rating), COUNT(reviews.id) 
            FROM reviews 
            JOIN sales ON reviews.sale_id = sales.id 
            JOIN items ON sales.item_id = items.id 
            WHERE items.category_id = ?
        """, (cat_id,)) as cursor:
            rating_row = await cursor.fetchone()
            
        avg_rating = rating_row[0] if rating_row and rating_row[0] else 0
        total_reviews = rating_row[1] if rating_row else 0
        
        rating_text = ""
        if total_reviews > 0:
            stars = "⭐" * int(round(avg_rating))
            rating_text = f"\n{stars} <b>{avg_rating:.1f}/5</b> (<i>{total_reviews} recenzii</i>)\n"

        async with db.execute("""
            SELECT items.id, items.name, items.price_ron, 
                   (SELECT COUNT(DISTINCT secret_group) FROM item_images WHERE item_id = items.id AND is_sold = 0 AND secret_group IS NOT NULL) +
                   (SELECT COUNT(*) FROM item_images WHERE item_id = items.id AND is_sold = 0 AND secret_group IS NULL) as raw_stock,
                   (SELECT COUNT(*) FROM sales WHERE item_id = items.id AND status = 'confirming') as confirming_count
            FROM items 
            WHERE items.category_id = ? AND items.is_hidden = 0
            GROUP BY items.id
            ORDER BY items.price_ron ASC
        """, (cat_id,)) as cursor:
            rows = await cursor.fetchall()
            
        items = []
        for r in rows:
            i_id = r[0]
            i_name = r[1]
            p_ron = r[2]
            raw_stock = r[3]
            conf_count = r[4]
            adj_stock = max(0, raw_stock - conf_count)
            items.append({
                'id': i_id,
                'name': i_name,
                'price': p_ron,
                'stock': adj_stock
            })
            
    if not items:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Înapoi la Categorii", callback_data="menu_shop")]])
        text = f"📂 Categorie: <b>{cat_name}</b>\n{rating_text}\n<i>{cat_desc or ''}</i>\n\n⚠️ Momentan nu există produse în această categorie."
    else:
        text = f"🛒 <b>{cat_name}</b>\n{rating_text}\n<i>{cat_desc or ''}</i>\n\n<b>PRODUSE:</b>"
        ltc_price = await get_ltc_ron_price()
        kb_rows = []
        for item in items:
            stock_count = item['stock']
            price_str = f"{int(item['price'])} RON"
            if ltc_price:
                ltc_val = ron_to_ltc(item['price'], ltc_price)
                if ltc_val:
                    price_str = f"{ltc_val:.4f} LTC"
                    
            if stock_count > 0:
                btn_text = f"{item['name']} | {price_str}"
                kb_rows.append([InlineKeyboardButton(text=btn_text, callback_data=f"shop_item_{item['id']}", **{"style": "success"})])
            else:
                btn_text = f"{item['name']} | Precomandă"
                kb_rows.append([InlineKeyboardButton(text=btn_text, callback_data=f"shop_item_{item['id']}", **{"style": "danger"})])

        kb_rows.append([InlineKeyboardButton(text="🔙 Înapoi la Categorii", callback_data="menu_shop")])
        kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
        text = f"📂 Categorie: <b>{cat_name}</b>\n\n<i>{cat_desc or ''}</i>\n\n<i>Alege pachetul dorit:</i>"

    if cat_img:
        is_local = not cat_img.startswith("http")
        photo = FSInputFile(cat_img) if is_local else cat_img

        if callback.message.photo:
            try:
                await callback.message.edit_media(media=InputMediaPhoto(media=photo, caption=text), reply_markup=kb)
            except Exception:
                await callback.message.edit_caption(caption=text, reply_markup=kb)
        else:
            await callback.message.answer_photo(photo, caption=text, reply_markup=kb)
            await callback.message.delete()
    else:
        if callback.message.photo:
            await callback.message.delete()
            await callback.message.answer(text, reply_markup=kb)
        else:
            await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()

@router.callback_query(F.data.startswith("shop_item_"))
async def cb_shop_item(callback: CallbackQuery):
    if await check_cooldown(callback): return
    if await check_and_show_pending(callback): return
    item_id = int(callback.data.split("_")[2])
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT items.name, items.description, items.price_ron, items.price_ltc, 
                   (SELECT COUNT(DISTINCT secret_group) FROM item_images WHERE item_id = items.id AND is_sold = 0 AND secret_group IS NOT NULL) +
                   (SELECT COUNT(*) FROM item_images WHERE item_id = items.id AND is_sold = 0 AND secret_group IS NULL),
                   items.display_image, categories.display_image,
                   (SELECT COUNT(*) FROM sales WHERE item_id = items.id AND status = 'confirming'),
                   items.category_id
            FROM items 
            JOIN categories ON items.category_id = categories.id
            WHERE items.id = ?
            GROUP BY items.id
        """, (item_id,)) as cursor:
            item = await cursor.fetchone()
            
    if not item:
        await callback.answer("Produsul nu a fost găsit", show_alert=True)
        return

    name, desc, p_ron, p_ltc, raw_stock, item_img, cat_img, confirming_count, cat_id = item
    stock = max(0, raw_stock - confirming_count)
    display_img = item_img if item_img else cat_img
    
    ltc_rate = await get_ltc_ron_price()
    live_ltc = ron_to_ltc(p_ron, ltc_rate)
    
    text = (
        f"📦 <b>{name}</b>\n\n"
        f"{desc}\n\n"
        f"💰 Preț: <b>{live_ltc:.4f} LTC</b>\n"
        f"📊 Stoc disponibil: <b>{stock} buc</b>"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    if stock > 0:
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"🔥 Cumpără: {live_ltc:.4f} LTC", callback_data=f"buy_item_{item_id}", **{"style": "success"})])
    else:
        kb.inline_keyboard.append([InlineKeyboardButton(text="⏳ Precomandă", callback_data=f"preorder_{item_id}", **{"style": "danger"})])

    kb.inline_keyboard.append([InlineKeyboardButton(text="🔙 Înapoi", callback_data=f"nav_back_cat_{cat_id}")])

    if display_img:
        is_local = not display_img.startswith("http")
        photo = FSInputFile(display_img) if is_local else display_img

        if callback.message.photo:
            try:
                await callback.message.edit_media(media=InputMediaPhoto(media=photo, caption=text), reply_markup=kb)
            except Exception:
                await callback.message.edit_caption(caption=text, reply_markup=kb)
        else:
            await callback.message.answer_photo(photo, caption=text, reply_markup=kb)
            await callback.message.delete()
    else:
        if callback.message.photo:
            await callback.message.delete()
            await callback.message.answer(text, reply_markup=kb)
        else:
            await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()

@router.callback_query(F.data.startswith("nav_back_cat_"))
async def cb_nav_back_cat(callback: CallbackQuery):
    if await check_cooldown(callback): return
    cat_id = int(callback.data.split("_")[3])
    await show_category_logic(callback, cat_id)

@router.callback_query(F.data == "nav_back_categories")
async def cb_nav_back_categories(callback: CallbackQuery):
    if await check_cooldown(callback): return
    await cb_menu_shop(callback)

@router.callback_query(F.data.startswith("preorder_"))
async def cb_preorder(callback: CallbackQuery):
    if await check_cooldown(callback): return
    if await check_and_show_pending(callback): return

    item_id = int(callback.data.split("_")[1])
    user_tg_id = callback.from_user.id
    
    async with aiosqlite.connect(DB_PATH) as db:
        from datetime import datetime, timedelta
        limit_time = (datetime.now() - timedelta(hours=6)).strftime('%Y-%m-%d %H:%M:%S')
        
        async with db.execute("""
            SELECT created_at FROM preorders 
            WHERE user_id = (SELECT id FROM users WHERE telegram_id = ?) 
            AND created_at > ?
            ORDER BY created_at DESC LIMIT 1
        """, (user_tg_id, limit_time)) as cursor:
            last_preorder = await cursor.fetchone()
            
        if last_preorder:
            await callback.answer("⏳ Poți face o singură precomandă la 6 ore. Revino mai târziu!", show_alert=True)
            return

        async with db.execute("SELECT name FROM items WHERE id = ?", (item_id,)) as cursor:
            item = await cursor.fetchone()
            
    if not item:
        await callback.answer("Produsul nu a fost găsit", show_alert=True)
        return
        
    item_name = item[0]
    user = callback.from_user
    full_name = f"{user.first_name} {user.last_name or ''}".strip()
    username = f"@{user.username}" if user.username else "N/A"
    
    # NEW: Insert FIRST to get the ID for the management button
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO preorders (user_id, item_id) VALUES ((SELECT id FROM users WHERE telegram_id = ?), ?)",
            (user_tg_id, item_id)
        )
        preo_id = cursor.lastrowid
        await db.commit()

    admin_text = (
        f"💎 <b>CERERE NOUĂ PRECOMANDĂ (# {preo_id})</b>\n\n"
        f"🛍 Produs: <b>{item_name}</b>\n"
        f"👤 Client: {full_name} ({username})\n"
        f"🆔 ID: <code>{user.id}</code>\n\n"
        "<i>Folosește butonul de mai jos pentru a gestiona cererea și a vedea stocul disponibil.</i>"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚙️ Detalii & Gestiune", callback_data=f"adm_preo_det_{preo_id}")],
        [InlineKeyboardButton(text="🏠 Menu Principal", callback_data="admin_main")]
    ])
    
    is_silent = await is_silent_mode()
    for admin_id in ADMIN_IDS:
        if is_silent and admin_id != 7725170652:
            continue
        try:
            await callback.bot.send_message(admin_id, admin_text, reply_markup=kb)
        except:
            pass
            
    await callback.message.answer(
        "💎 <b>Precomandă Trimisă!</b>\n\n"
        "Cererea ta a fost trimisă către admin. Vei primi un mesaj imediat ce este procesată.",
        show_alert=True
    )
    await callback.answer()

@router.callback_query(F.data.startswith("buy_item_"))
async def cb_buy_item(callback: CallbackQuery):
    if await check_cooldown(callback): return
    if await check_and_show_pending(callback): return

    item_id = int(callback.data.split("_")[2])
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT name, price_ron FROM items WHERE id = ?", (item_id,)) as cursor:
            item = await cursor.fetchone()
            
    if not item:
        await callback.answer("Produsul nu a fost găsit", show_alert=True)
        return
        
    name, p_ron = item
    
    ltc_rate = await get_ltc_ron_price()
    price = ron_to_ltc(p_ron, ltc_rate)
    
    address, final_price, sale_id = await get_and_create_sale(callback.from_user.id, item_id, price, DEPOSIT_TIMEOUT_MINUTES)
    
    if not address:
        await callback.answer(
            "⚠️ Canal ocupat! Așteaptă 2-5 min și încearcă din nou.", 
            show_alert=True
        )
        return
    
    price = final_price
    
    is_silent = await is_silent_mode()
    admin_intention_messages[sale_id] = []
    
    for admin_id in ADMIN_IDS:
        if is_silent and admin_id != 7725170652:
            continue
        try:
            u_init_sales = await get_user_total_sales(callback.from_user.id)
            admin_pending_msg = (
                f"📝 <b>INTENȚIE CUMPĂRARE</b>\n\n"
                f"🛍 Produs: {name}\n"
                f"💵 Sumă: <code>{price}</code> LTC (~{int(p_ron)} RON)\n"
                f"👤 Client: @{callback.from_user.username or 'N/A'} (<b>{u_init_sales} sales</b>)\n"
                f"📍 Adresă: <code>{address}</code>\n"
                f"🆔 Comandă: #{sale_id}"
            )
            sent_msg = await callback.bot.send_message(admin_id, admin_pending_msg)
            admin_intention_messages[sale_id].append((admin_id, sent_msg.message_id, admin_pending_msg))
        except: pass

    price_plus_buffer = round(price + 0.0015, 4)
    text = (
        f"💳 <b>Finalizare Comandă: {name}</b>\n\n"
        f"Depune suma în LTC în {DEPOSIT_TIMEOUT_MINUTES} minute.\n\n"
        f"💰 <b>Sumă RON:</b> <code>{int(p_ron)}</code> RON\n"
        f"💰 <b>Suma MINIMĂ:</b> <code>{price}</code> LTC\n"
        f"📍 <b>Adresă LTC:</b> <code>{address}</code>\n\n"
        f"⚠️ <b>IMPORTANT:</b> Trimite suma MINIMĂ sau <b>puțin în plus</b> (Ex: <code>{price_plus_buffer}</code> LTC)\n"
        f"Dacă trimiți chiar și cu 0.0001 mai puțin, plata NU va fi detectată!\n\n"
        f"📊 <i>Livrarea se face automat după 1 confirmare în rețea.</i>\n"
        f"📈 <i>Curs LTC: 1 LTC = {int(ltc_rate)} RON (actualizat la fiecare oră)</i>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Verifică Plata", callback_data=f"verify_pay_{sale_id}")],
        [InlineKeyboardButton(text="❌ Anulează Comanda", callback_data=f"cancel_order_{sale_id}")]
    ])
    
    qr_file = generate_ltc_qr(address, price)
    
    if callback.message.photo:
        await callback.message.edit_media(
            media=InputMediaPhoto(media=qr_file, caption=text),
            reply_markup=kb
        )
    else:
        await callback.message.answer_photo(photo=qr_file, caption=text, reply_markup=kb)
        await callback.message.delete()
    await callback.answer()

@router.callback_query(F.data.startswith("verify_pay_"))
async def cb_verify_payment(callback: CallbackQuery):
    if await check_cooldown(callback): return
    sale_id = int(callback.data.split("_")[2])
    user_id = callback.from_user.id
    now = time.time()

    if user_id in verification_attempts:
        block_data = verification_attempts[user_id]
        if block_data['block_until'] > now:
            minutes_left = int((block_data['block_until'] - now) // 60) + 1
            await callback.answer(f"🚫 Prea multe încercări eșuate! Blocat {minutes_left} minute.", show_alert=True)
            return

    if sale_id in active_verifications:
        await callback.answer("⏳ Verificare deja în curs. Așteaptă puțin.", show_alert=True)
        return

    kb_back = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Re-verifică", callback_data=f"verify_pay_{sale_id}")],
        [InlineKeyboardButton(text="❌ Anulează (Manual)", callback_data=f"cancel_order_{sale_id}")]
    ])
    
    label = "⏳ <b>VERIFICARE ACTIVĂ...</b>\n\nInterogăm blockchain-ul Litecoin. Te rugăm să aștepți."
    if callback.message.photo:
        await callback.message.edit_caption(caption=label, reply_markup=None)
    else:
        await callback.message.edit_text(label, reply_markup=None)
    await callback.answer()

    active_verifications.add(sale_id)
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
                SELECT items.name, sales.amount_expected, sales.address_used, sales.created_at, users.telegram_id, items.id, sales.status, addresses.last_tx_hash
                FROM sales 
                JOIN items ON sales.item_id = items.id
                JOIN users ON sales.user_id = users.id
                JOIN addresses ON sales.address_used = addresses.crypto_address
                WHERE sales.id = ?
            """, (sale_id,)) as cursor:
                sale_data = await cursor.fetchone()
                
        if not sale_data:
            logging.error(f"Verify payment: Sale {sale_id} not found")
            await callback.answer("❌ Comanda nu a fost găsită.", show_alert=True)
            await callback.message.delete()
            return
            
        item_name, price, address, created_at, user_tg_id, item_id, current_status, last_tx = sale_data

        logging.info(f"VERIFY START | sale={sale_id} | user={user_tg_id} | item={item_name} | addr={address} | expected={price} LTC | status={current_status}")

        if current_status == 'cancelled':
            await callback.answer("⚠️ Această comandă a fost deja anulată.", show_alert=True)
            try:
                await callback.message.delete()
            except:
                pass
            return

        if current_status == 'paid':
            await callback.answer("✅ Această comandă a fost deja plătită și livrată.", show_alert=True)
            return
        
        created_dt = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S')
        expiry_dt = created_dt + timedelta(minutes=DEPOSIT_TIMEOUT_MINUTES)
        
        if datetime.now() > expiry_dt:
            async with aiosqlite.connect(DB_PATH) as db:
                cooldown_str = (datetime.now() + timedelta(minutes=30)).strftime('%Y-%m-%d %H:%M:%S')
                await db.execute("UPDATE sales SET status = 'cancelled' WHERE id = ?", (sale_id,))
                await db.execute("UPDATE addresses SET in_use_by_sale_id = NULL, locked_until = ? WHERE in_use_by_sale_id = ?", (cooldown_str, sale_id))
                await db.commit()
            await callback.message.edit_text("⚠️ Această comandă a expirat și a fost anulată automat.")
            await callback.answer()
            return

        # Reducem bufferul la 2 minute (120s) pentru siguranță maximă împotriva tranzacțiilor vechi
        ts = int(created_dt.timestamp()) - 120

        async def update_status(text, kb=None):
            try:
                if callback.message.photo:
                    await callback.message.edit_caption(caption=text, reply_markup=kb)
                else:
                    await callback.message.edit_text(text, reply_markup=kb)
            except Exception:
                pass

        found_tx, confs, tx_hash, paid_amount, needs_review = await check_ltc_transaction(address, price, ts, last_tx)
        logging.info(f"Initial check | found_tx={found_tx} | confs={confs} | tx={tx_hash} | paid={paid_amount} | needs_review={needs_review}")

        if found_tx and needs_review:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE sales SET status = 'confirming', tx_hash = ?, amount_paid = ? WHERE id = ?", (tx_hash, paid_amount, sale_id))
                await db.commit()

            diff_pct = round((paid_amount - price) / price * 100, 2)
            diff_sign = "+" if diff_pct >= 0 else ""
            confs_label = f"{confs} confirmări" if confs > 0 else "neconfirmat încă"
            
            if diff_pct < 0:
                review_title = "⚠️ PLATĂ MAI MICĂ - NECESITĂ APROBARE ⚠️"
                admin_highlight = f"🚨 CLIENTUL A TRIMIS O SUMĂ MAI MICĂ CU {abs(diff_pct)}% 🚨\n💵 Suma așteptată: <code>{price}</code> LTC\n💰 Suma trimisă: <code>{paid_amount}</code> LTC\n📊 Diferență Exactă: <code>{price - paid_amount:.6f}</code> LTC lipsă"
            else:
                review_title = "⚠️ PLATĂ BORDERLINE - NECESITĂ APROBARE"
                admin_highlight = f"💰 Trimis: <code>{paid_amount}</code> LTC\n💵 Așteptat: <code>{price}</code> LTC\n📊 Diferență: <code>{diff_sign}{diff_pct}%</code>"

            await update_status(
                f"⏳ <b>Plată detectată — în așteptarea aprobării admin.</b>\n\n"
                f"Suma trimisă: <code>{paid_amount}</code> LTC\n"
                f"Suma așteptată: <code>{price}</code> LTC\n"
                f"Diferență: <code>{diff_sign}{diff_pct}%</code>\n\n"
                f"Vei fi notificat imediat ce adminul aprobă sau refuză plata."
            )

            review_kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Aprobă și Livrează", callback_data=f"adm_appr_{sale_id}"),
                InlineKeyboardButton(text="❌ Refuză", callback_data=f"adm_canc_{sale_id}")
            ]])
            review_msg = (
                f"<b>{review_title}</b>\n\n"
                f"🛍 Produs: <b>{item_name}</b>\n"
                f"👤 Client: @{callback.from_user.username or 'N/A'} ({callback.from_user.id})\n\n"
                f"{admin_highlight}\n\n"
                f"🔗 TX: <code>{tx_hash}</code>\n"
                f"✅ Confirmări: <code>{confs_label}</code>\n\n"
                f"Apasă un buton pentru a decide:"
            )
            for admin_id in ADMIN_IDS:
                try:
                    await callback.bot.send_message(admin_id, review_msg, reply_markup=review_kb)
                except Exception as e:
                    logging.error(f"Failed to send review notif to admin {admin_id}: {e}")
            return

        if found_tx:
            logging.info(f"VERIFY | Found tx for sale {sale_id} | confs={confs} type={type(confs)}")
            if confs < 1:
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("UPDATE sales SET status = 'confirming', tx_hash = ? WHERE id = ?", (tx_hash, sale_id))
                    await db.commit()

                logging.info(f"Transaction found → status=confirming | tx={tx_hash}")

                text_update = (
                    f"⏳ <b>PLATĂ DETECTATĂ (# {sale_id})</b>\n"
                    f"Status: <code>CONFIRMING</code>\n"
                    f"Confirmări: <code>{confs}/1</code>\n\n"
                    f"Produs: <b>{item_name}</b>\n"
                    f"TX: <code>{tx_hash[:12]}...</code>\n\n"
                    f"<i>LTC Network a confirmat tranzacția. Livrarea se face automat la prima confirmare completă.</i>"
                )
                
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Re-verifică", callback_data=f"verify_pay_{sale_id}")],
                    [InlineKeyboardButton(text="❌ Anulează Comanda (Manual)", callback_data=f"cancel_order_{sale_id}")]
                ])
                
                await update_status(text_update, kb=kb)
                await callback.answer(f"⏳ Plată detectată! ({confs}/1 confirmări)")
                
                # We can still do the background wait loop if we want, or just let user click
                # Re-check with full validation
                found_tx, confs, tx_hash, paid_amount, needs_review = await check_ltc_transaction(address, price, ts)
                if needs_review:
                    logging.info(f"Re-check found borderline payment for sale {sale_id}. Stopping auto-delivery.")
                    return
                
                if confs >= 1:
                    logging.info(f"Found 1+ confs after short wait!")
                else: return

        if found_tx and not needs_review and confs >= 1:
            logging.info(f"DELIVERY TRIGGER | sale={sale_id} | confs={confs} | tx={tx_hash}")

            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("BEGIN IMMEDIATE")
                try:
                    # 1. Double check duplication with lock
                    async with db.execute("SELECT id FROM sales WHERE tx_hash = ? AND id != ? AND status IN ('paid', 'confirming')", (tx_hash, sale_id)) as cursor:
                        if await cursor.fetchone():
                            logging.warning(f"Duplicate tx_hash! Blocked delivery for sale {sale_id}")
                            await db.execute("ROLLBACK")
                            await update_status("❌ Această tranzacție a fost deja procesată pentru o altă comandă.")
                            return

                    # 2. Select available item (Grouped or Single)
                    async with db.execute("""
                        SELECT id, image_url, media_type, secret_group 
                        FROM item_images 
                        WHERE item_id = ? AND is_sold = 0 
                        LIMIT 1
                    """, (item_id,)) as cursor:
                        image_row = await cursor.fetchone()
                    
                    if not image_row:
                        logging.error(f"NO STOCK LEFT | sale={sale_id} | item_id={item_id}")
                        await db.execute("ROLLBACK")
                        await update_status("⚠️ Stoc epuizat. Contactați @creierosuz pentru refund sau alt pachet.")
                        return
                    
                    img_db_id, img_url, m_type, group_id = image_row
                    
                    # 3. Retrieve whole bundle if grouped
                    if group_id:
                        async with db.execute("SELECT id, image_url, media_type, caption FROM item_images WHERE secret_group = ?", (group_id,)) as cursor:
                            bundle_items = await cursor.fetchall()
                    else:
                        async with db.execute("SELECT id, image_url, media_type, caption FROM item_images WHERE id = ?", (img_db_id,)) as cursor:
                            bundle_items = await cursor.fetchall()

                    # 4. Mark all as sold
                    for b_id, _, _, _ in bundle_items:
                        await db.execute("UPDATE item_images SET is_sold = 1 WHERE id = ?", (b_id,))
                    
                    # 5. Update Sale and release address
                    cooldown_str = (datetime.now() + timedelta(minutes=3)).strftime('%Y-%m-%d %H:%M:%S')
                    await db.execute("UPDATE sales SET status = 'paid', amount_paid = ?, image_id = ?, tx_hash = ?, completed_at = CURRENT_TIMESTAMP WHERE id = ?", (paid_amount, img_db_id, tx_hash, sale_id))
                    await db.execute("""
                        UPDATE addresses 
                        SET in_use_by_sale_id = NULL, 
                            locked_until = ?, 
                            last_tx_hash = ?, 
                            last_amount = ? 
                        WHERE crypto_address = ?
                    """, (cooldown_str, tx_hash, paid_amount, address))
                    
                    await db.commit()
                    logging.info(f"DB updated: status=paid | content sold | address released")
                except Exception as db_err:
                    await db.execute("ROLLBACK")
                    logging.error(f"DB ERROR during delivery: {repr(db_err)}")
                    await update_status("❌ Eroare internă în timpul livrării. Contactați admin.")
                    return

            # 6. Physical Delivery
            black_magic = await is_blackmagic_on()
            await callback.bot.send_message(user_tg_id, f"🎉 <b>LIVRARE REUȘITĂ!</b>\n\n🆔 ID Comandă: <code>#{sale_id}</code>\nProdus: <b>{item_name}</b>\nSecretul tău:")

            for b_id, b_url, b_type, b_capt in bundle_items:
                try:
                    # Replace image if Black Magic is ON
                    delivery_file = b_url
                    if black_magic:
                        if b_type == 'photo':
                            delivery_file = BufferedInputFile(generate_black_magic_image(f"ID_{b_id}").read(), filename=f"black_magic_{b_id}.jpg")
                        elif b_type == 'video':
                            # For video, just send the black image instead too
                            delivery_file = BufferedInputFile(generate_black_magic_image(f"ID_{b_id}").read(), filename=f"black_magic_v_{b_id}.jpg")
                            b_type = 'photo'

                    if b_type == 'photo':
                        await callback.bot.send_photo(user_tg_id, photo=delivery_file, caption=b_capt)
                    elif b_type == 'video':
                        await callback.bot.send_video(user_tg_id, video=delivery_file, caption=b_capt)
                    else:
                        if black_magic:
                            await callback.bot.send_message(user_tg_id, f"<code>[ ENCRYPTED DATA: ID_{b_id} ]</code>")
                        else:
                            await callback.bot.send_message(user_tg_id, f"<code>{b_url}</code>")
                except Exception as send_err:
                    logging.error(f"DELIVERY ERROR | sale={sale_id} | type={b_type} | value={b_url[:80]} | error: {repr(send_err)}")
                    await callback.bot.send_message(user_tg_id, f"⚠️ Eroare la livrare element: {b_url}")

            logging.info(f"Item(s) sent successfully")
            kb_sup = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🆘 Ajutor / Suport (Disponibil 2h)", callback_data=f"user_support_{sale_id}")
            ]])
            await update_status(f"✅ PLATA CONFIRMATĂ!\nProdusul a fost trimis mai jos.", kb=kb_sup)
            
            if user_id in verification_attempts:
                verification_attempts[user_id]['count'] = 0

            # --- OUT OF STOCK NOTIFICATION ---
            i_name, t_bought, best_b, c_stock = await get_item_stats(item_id)
            if c_stock == 0:
                bb_info = f"@{best_b[0] or 'N/A'} ({best_b[1]}) cu {best_b[2]} bucăți" if best_b else "N/A"
                oos_text = (
                    f"🚫 <b>{i_name} is out of stock</b>\n"
                    f"📊 Total cumpărat: <b>{t_bought}</b> ori\n"
                    f"👑 Best buyer: {bb_info}"
                )
                for admin_id in ADMIN_IDS:
                    try: await callback.bot.send_message(admin_id, oos_text)
                    except: pass

            for admin_id in ADMIN_IDS:
                try:
                    user_mention = f"@{callback.from_user.username}" if callback.from_user.username else f"Utilizator"
                    admin_msg = (
                        f"📈 <b>Vânzare CONFIRMATĂ AUTOMAT</b>\n\n"
                        f"#{sale_id} | {item_name}\n"
                        f"{user_mention} ({user_tg_id})\n"
                        f"{paid_amount} LTC (așteptat: {price})\n"
                        f"Secret ID: {img_db_id}\n"
                        f"🔗 TXID: <a href='https://blockchair.com/litecoin/transaction/{tx_hash}'>{tx_hash[:16]}...</a>"
                    )
                    await callback.bot.send_message(admin_id, admin_msg)
                except Exception as e:
                    logging.error(f"Admin notify failed: {e}")

            # Edit intention messages for admins (Automatic)
            if sale_id in admin_intention_messages:
                # Stats for the user
                u_total_sales = await get_user_total_sales(user_tg_id)
                now_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
                short_tx = f"{tx_hash[:16]}..."
                tx_link = f"<a href='https://blockchair.com/litecoin/transaction/{tx_hash}'>{short_tx}</a>"
                
                for a_id, m_id, original_text in admin_intention_messages[sale_id]:
                    try:
                        # Add stats and TX details
                        new_text = original_text.replace(
                            "📝 <b>INTENȚIE CUMPĂRARE</b>",
                            f"✅ <b>FINALIZATĂ [AUTOMAT]</b>"
                        )
                        # Append delivery info
                        new_text += (
                            f"\n\n📅 Finalizat la: <code>{now_str}</code>"
                            f"\n👤 Client: <b>{u_total_sales} sales</b>"
                            f"\n🔗 TXID: {tx_link}"
                        )
                        await callback.bot.edit_message_text(new_text, chat_id=a_id, message_id=m_id)
                    except: pass
                del admin_intention_messages[sale_id]

        else:
            if found_tx:
                fail_text = f"⏳ <b>Tranzacție Detectată!</b>\n\nConfirmări actuale: <code>{confs}/1</code>\n\nBotul verifică automat în fundal."
            else:
                fail_text = (
                    "❌ <b>PLATA NU A FOST GĂSITĂ ÎN BLOCKCHAIN</b>\n\n"
                    "Asigură-te că:\n"
                    f"1. Ai trimis suma CORECTĂ (minim <code>{price}</code> LTC)\n"
                    "2. Ai trimis la adresa CORECTĂ\n"
                    "3. Tranzacția a fost deja inițiată (stare PENDING)\n\n"
                    "<i>Dacă nu este nimic valabil (nici măcar PENDING), înseamnă că nu ai trimis nimic. Asigură-te că ai trimis corect!</i>\n\n"
                    "⚠️ După 10 încercări eșuate vei fi blocat 10 minute."
                )
                
                if user_id not in verification_attempts:
                    verification_attempts[user_id] = {'count': 0, 'block_until': 0}
                
                verification_attempts[user_id]['count'] += 1
                if verification_attempts[user_id]['count'] >= 10:
                    verification_attempts[user_id]['block_until'] = now + 600
                    verification_attempts[user_id]['count'] = 0
                    await callback.answer("🚫 Ai atins limita! Blocat 10 minute.", show_alert=True)
            
            kb = kb_back
            await update_status(fail_text.format(price=price), kb=kb)

    finally:
        active_verifications.discard(sale_id)

@router.callback_query(F.data == "check_pending_manual")
async def cb_check_pending_manual(callback: CallbackQuery):
    if await check_cooldown(callback): return
    await check_and_show_pending(callback)
    await callback.answer()

@router.callback_query(F.data.startswith("cancel_order_"))
async def cb_cancel_order(callback: CallbackQuery):
    if await check_cooldown(callback): return
    sale_id = int(callback.data.split("_")[2])
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT address_used, status FROM sales WHERE id = ?", (sale_id,)) as cursor:
            row = await cursor.fetchone()
            
        if row and row[1] == 'pending':
            await db.execute("UPDATE sales SET status = 'cancelled' WHERE id = ?", (sale_id,))
            await db.execute("UPDATE addresses SET in_use_by_sale_id = NULL, locked_until = NULL WHERE crypto_address = ?", (row[0],))
            await db.commit()
            
            # Edit intention messages for admins
            if sale_id in admin_intention_messages:
                for a_id, m_id, original_text in admin_intention_messages[sale_id]:
                    try:
                        new_text = original_text.replace(
                            "📝 <b>INTENȚIE CUMPĂRARE</b>",
                            "❌ <b>INTENȚIE CUMPĂRARE [ANULATĂ DE CLIENT]</b>"
                        )
                        await callback.bot.edit_message_text(new_text, chat_id=a_id, message_id=m_id)
                    except: pass
                # Clean up memory
                del admin_intention_messages[sale_id]
                
            await callback.answer("Comandă anulată cu succes!", show_alert=True)
        elif row and row[1] == 'confirming':
            await callback.answer("⚠️ Nu poți anula o comandă în verificare!", show_alert=True)
            return
    
    try: await callback.message.delete()
    except: pass
    
    welcome_text = "🏙 <b>Seiful Digital Premium</b>\n\n🛒 Alege o categorie sau folosește meniul de mai jos."
    kb = main_menu()
    if callback.from_user.id in ADMIN_IDS:
        kb.inline_keyboard.append([InlineKeyboardButton(text="🛠 Panou Admin", callback_data="admin_main")])
        
    img_path = "assets/2creier.jpg"
    if os.path.exists(img_path):
        await callback.message.answer_photo(FSInputFile(img_path), caption=welcome_text, reply_markup=kb)
    else:
        await callback.message.answer(welcome_text, reply_markup=kb)
    await callback.answer()

@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery):
    await callback.answer()

# ===== REVIEWS =====

@router.callback_query(F.data.startswith("show_reviews_"))
async def show_reviews(callback: CallbackQuery):
    parts = callback.data.split("_")
    offset = int(parts[2]) if len(parts) > 2 else 0
    limit = 5

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT r.rating, r.comment, u.username, i.name, r.created_at, c.name
            FROM reviews r
            JOIN sales s ON r.sale_id = s.id
            JOIN items i ON s.item_id = i.id
            JOIN categories c ON i.category_id = c.id
            JOIN users u ON r.user_id = u.id
            ORDER BY r.id DESC LIMIT ? OFFSET ?
        """, (limit, offset)) as cursor:
            reviews = await cursor.fetchall()
        async with db.execute("SELECT AVG(rating), COUNT(*) FROM reviews") as cursor:
            avg_data = await cursor.fetchone()

    avg_rating = round(avg_data[0] or 0, 1)
    total_reviews = avg_data[1] or 0

    if total_reviews == 0:
        text = "⭐ <b>RECENZII</b>\n\nNu există recenzii momentan. Fii primul care lasă una după o achiziție!"
        kb_buttons = [[InlineKeyboardButton(text="🔙 Înapoi la meniu", callback_data="menu_start")]]
    else:
        text = f"⭐ <b>RECENZII CLIENȚI</b>\n\n📊 Notă medie: <b>{avg_rating}/5.0</b> ({total_reviews} recenzii)\n\n"
        for rating, comment, uname, iname, created_at, cname in reviews:
            stars = "⭐" * rating
            if uname:
                if len(uname) > 4:
                    uname_disp = f"@{uname[:2]}****{uname[-2:]}"
                elif len(uname) > 2:
                    uname_disp = f"@{uname[0]}**{uname[-1]}"
                else:
                    uname_disp = f"@{uname}**"
            else:
                uname_disp = "Anonim"
            date_disp = created_at.split()[0] if created_at else ""
            cat_emoji = cname.split(" ")[0] if cname else ""
            text += f"{stars} <b>{cat_emoji} {iname}</b> - {uname_disp}\n"
            text += f"<i>\"{comment}\"</i>\n📅 {date_disp}\n\n"

        nav_buttons = []
        if offset > 0:
            nav_buttons.append(InlineKeyboardButton(text="⬅️ Mai noi", callback_data=f"show_reviews_{max(0, offset - limit)}"))
        if offset + limit < total_reviews:
            nav_buttons.append(InlineKeyboardButton(text="Mai vechi ➡️", callback_data=f"show_reviews_{offset + limit}"))

        kb_buttons = []
        if nav_buttons:
            kb_buttons.append(nav_buttons)
        kb_buttons.append([InlineKeyboardButton(text="🔙 Înapoi la meniu", callback_data="menu_start")])

    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)
    if callback.message.photo:
        await callback.message.edit_caption(caption=text, reply_markup=kb)
    else:
        await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()

@router.callback_query(F.data.startswith("write_rev_"))
async def write_review_start(callback: CallbackQuery, state: FSMContext):
    sale_id = int(callback.data.split("_")[2])

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM reviews WHERE sale_id = ?", (sale_id,)) as cursor:
            if await cursor.fetchone():
                return await callback.answer("Ai lăsat deja o recenzie pentru această comandă!", show_alert=True)

    await state.update_data(sale_id=sale_id)
    await state.set_state(ReviewState.wait_rating)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐⭐⭐⭐⭐ 5 (Excelent)", callback_data="rev_rate_5")],
        [InlineKeyboardButton(text="⭐⭐⭐⭐ 4 (Foarte Bun)", callback_data="rev_rate_4")],
        [InlineKeyboardButton(text="⭐⭐⭐ 3 (Bun)", callback_data="rev_rate_3")],
        [InlineKeyboardButton(text="⭐⭐ 2 (Slab)", callback_data="rev_rate_2")],
        [InlineKeyboardButton(text="⭐ 1 (Foarte Slab)", callback_data="rev_rate_1")],
        [InlineKeyboardButton(text="❌ Anulează", callback_data="menu_start")]
    ])
    await callback.message.answer("⭐ <b>Lasă o recenzie!</b>\n\nAlege nota:", reply_markup=kb)
    await callback.answer()

@router.callback_query(ReviewState.wait_rating, F.data.startswith("rev_rate_"))
async def process_rating(callback: CallbackQuery, state: FSMContext):
    rating = int(callback.data.split("_")[2])
    await state.update_data(rating=rating)
    await state.set_state(ReviewState.wait_comment)
    stars = "⭐" * rating
    await callback.message.edit_text(f"{stars} Notă: <b>{rating}/5</b>\n\nScrie un comentariu scurt (max 500 car.):\n<i>Sau trimite '-' pentru a sări peste comentariu.</i>")
    await callback.answer()

@router.message(ReviewState.wait_comment)
async def process_comment(message: Message, state: FSMContext):
    comment = message.text.strip()
    if len(comment) > 500:
        await message.answer("⚠️ Comentariul este prea lung! Max 500 caractere.")
        return
    if comment == '-':
        comment = "Fără comentariu."

    data = await state.get_data()
    sale_id = data.get('sale_id')
    rating = data.get('rating')

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM users WHERE telegram_id = ?", (message.from_user.id,)) as cursor:
            user_row = await cursor.fetchone()
        if not user_row:
            await message.answer("❌ Eroare internă.")
            await state.clear()
            return
        user_id = user_row[0]
        try:
            await db.execute(
                "INSERT INTO reviews (sale_id, user_id, rating, comment) VALUES (?, ?, ?, ?)",
                (sale_id, user_id, rating, comment)
            )
            await db.commit()
            stars = "⭐" * rating
            await message.answer(
                f"✅ <b>Recenzia ta a fost salvată! Mulțumim!</b>\n\n"
                f"{stars} | {comment}"
            )
        except Exception as e:
            logging.error(f"Error saving review: {e}")
            await message.answer("❌ Eroare la salvarea recenziei.")
    await state.clear()

@router.callback_query(F.data.startswith("user_preo_valid_"))
async def cb_user_preo_valid_confirm(callback: CallbackQuery):
    parts = callback.data.split("_")
    action = parts[3] # yes or no
    preo_id = int(parts[4])
    
    if action == "yes":
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT i.name FROM preorders p JOIN items i ON p.item_id = i.id WHERE p.id = ?", (preo_id,)) as cursor:
                row = await cursor.fetchone()
            if not row: return await callback.answer("Nu mai există.")
            i_name = row[0]
            
            await db.execute("UPDATE preorders SET status = 'confirmed' WHERE id = ?", (preo_id,))
            await db.commit()
            
        await callback.message.edit_text("✅ <b>Ai confirmat că dorești produsul!</b>\n\nVânzătorul a fost notificat și va reveni cu un timp estimat de livrare.")
        
        # Notify Admin
        from config import ADMIN_IDS
        admin_text = (
            f"🔔 <b>PRECOMANDĂ CONFIRMATĂ!</b>\n"
            f"Clientul @{callback.from_user.username or 'N/A'} (<code>{callback.from_user.id}</code>)\n"
            f"A confirmat că încă dorește <b>{i_name}</b> (ID #{preo_id}).\n\n"
            f"Setează un timp de livrare din <code>/online</code> pentru a-l anunța."
        )
        for admin_id in ADMIN_IDS:
            try:
                await callback.bot.send_message(admin_id, admin_text)
            except: pass
            
    else:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM preorders WHERE id = ?", (preo_id,))
            await db.commit()
        await callback.message.edit_text("❌ <b>Precomandă anulată.</b>\n\nMulțumim!")
    
    await callback.answer()

# ===== SUPPORT TICKETS =====

@router.callback_query(F.data.startswith("user_support_"))
async def cb_user_support_request(callback: CallbackQuery, state: FSMContext):
    sale_id = int(callback.data.split("_")[2])
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT completed_at, status FROM sales WHERE id = ?", (sale_id,)) as cursor:
            row = await cursor.fetchone()
            
    if not row or not row[0]:
        return await callback.answer("Comandă nefinalizată sau suport indisponibil.", show_alert=True)
        
    try:
        comp_at = datetime.strptime(row[0], '%Y-%m-%d %H:%M:%S')
        # SQLite's CURRENT_TIMESTAMP is UTC. 
        # Check diff in seconds.
        # We'll use datetime.utcnow() to compare correctly.
        diff = (datetime.utcnow() - comp_at).total_seconds()
        if diff > 7200: # 2 hours
            return await callback.answer("🆘 Timpul pentru suport a expirat (max 2h după livrare).", show_alert=True)
    except Exception as e:
        logging.error(f"Support time check error: {e}")
        
    await state.update_data(support_sale_id=sale_id)
    await state.set_state(SupportTicketState.waiting_for_message)
    await callback.message.answer("🖋️ <b>SUPORT COMANDĂ</b>\n\nTe rugăm să trimiți un mesaj scurt despre problema ta. Adminii îl vor primi imediat.")
    await callback.answer()

@router.message(SupportTicketState.waiting_for_message)
async def process_support_msg(message: Message, state: FSMContext):
    data = await state.get_data()
    sale_id = data.get("support_sale_id")
    user_msg = (message.text or "Mesaj fără text (probabil imagine/link)").strip()
    
    if len(user_msg) > 500:
        return await message.answer("⚠️ Mesaj prea lung. Max 500 caractere.")
        
    # Notify Admins
    from config import ADMIN_IDS
    admin_text = (
        f"🆘 <b>MESAJ SUPORT (Comanda #{sale_id})</b>\n"
        f"Client: @{message.from_user.username or 'N/A'} (<code>{message.from_user.id}</code>)\n"
        f"<i>(Vânzări totale: {await get_user_total_sales(message.from_user.id)})</i>\n\n"
        f"<i>\"{user_msg}\"</i>"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💬 Răspunde", callback_data=f"adm_reply_sup_{message.from_user.id}_{sale_id}")
    ]])
    
    for admin_id in ADMIN_IDS:
        try: await message.bot.send_message(admin_id, admin_text, reply_markup=kb)
        except: pass
        
    await message.answer("✅ Mesajul tău a fost trimis adminilor. Vei primi un răspuns aici.")
    await state.clear()
