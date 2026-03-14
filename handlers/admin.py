import asyncio
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile, FSInputFile
from utils.keyboards import admin_main_menu
from config import ADMIN_IDS
from aiogram.fsm.context import FSMContext
from handlers.states import AdminCategory, AdminItem, AdminStock, AdminRemoval
from database import DB_PATH
from utils.image_cleaner import strip_exif
import aiosqlite
import logging
import io
import re
import os
import uuid
import time
import time


router = Router()

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def is_emoji_only(text: str) -> bool:
    # Basic check for emojis/symbols. 
    # This regex covers common emoji ranges.
    clean_text = text.replace(" ", "").strip()
    if not clean_text: return False
    emoji_pattern = re.compile(r'^[^\w\s,.<>?/;:\'\"[\]{}|\\`~!@#$%^&*()_+=\-]+$', re.UNICODE)
    # Actually, a better way is to check if it contains ANY alphanumeric. 
    # If it contains only non-alphanumeric (emojis are non-alphanumeric usually in basic regex), we allow it.
    # But user specifically asked for emojis.
    # Let's use a simpler heuristic: If it has letters or numbers, it's not JUST emojis.
    return not any(c.isalnum() for c in clean_text)

@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        return
    from handlers.user import BOT_START_TIME
    uptime = int(time.time() - BOT_START_TIME)
    text = f"🛠 <b>Control Panel Administrator</b>\n⏱ Uptime: {uptime}s\n\n(Dacă vezi mai multe uptime-uri diferite când dai click, înseamnă că ai mai multe instanțe pornite!)"
    
    img_path = "assets/admin.png"
    if os.path.exists(img_path):
        await message.answer_photo(FSInputFile(img_path), caption=text, reply_markup=admin_main_menu())
    else:
        await message.answer(text, reply_markup=admin_main_menu())


@router.message(Command("pending", prefix="!/"))
async def cmd_pending_orders(message: Message):
    if not is_admin(message.from_user.id):
        return
        
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT sales.id, items.name, sales.amount_expected, users.username, users.telegram_id, sales.address_used, sales.created_at
            FROM sales
            JOIN items ON sales.item_id = items.id
            JOIN users ON sales.user_id = users.id
            JOIN addresses ON addresses.in_use_by_sale_id = sales.id
            WHERE sales.status = 'pending'
            ORDER BY sales.created_at DESC
        """) as cursor:
            pending = await cursor.fetchall()
            
    if not pending:
        await message.answer("ℹ️ Nu există comenzi active (trackuite) momentan.")
        return
        
    for p in pending:
        text = (
            f"⏳ <b>Comandă Activă #{p[0]}</b>\n"
            f"🛍 Produs: {p[1]}\n"
            f"💰 Sumă: <code>{p[2]}</code> LTC\n"
            f"👤 Client: @{p[3] or 'N/A'} (<code>{p[4]}</code>)\n"
            f"📍 Adresă: <code>{p[5]}</code>\n"
            f"🕒 Creată: {p[6]}"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Finalizează", callback_data=f"adm_appr_{p[0]}", style="success"),
                InlineKeyboardButton(text="❌ Anulează", callback_data=f"adm_canc_{p[0]}", style="danger")
            ]
        ])

        await message.answer(text, reply_markup=kb)

@router.message(Command("secretcreiermare", prefix="!/"))
async def cmd_reveal_all_secrets(message: Message):
    """Admin command to list only products WITH stock using compact buttons."""
    if not is_admin(message.from_user.id):
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id, name FROM categories ORDER BY id") as cursor:
            categories = await cursor.fetchall()

    if not categories:
        await message.answer("⚠️ Nu există categorii.")
        return

    await message.answer("📂 <b>LISTĂ STOC ACTIV</b>\n<i>Apasă pe un pachet pentru a vedea conținutul sau a-l șterge.</i>")

    for cat_id, cat_name in categories:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
                SELECT items.id, items.name, 
                       (
                         (SELECT COUNT(DISTINCT secret_group) FROM item_images WHERE item_id = items.id AND is_sold = 0 AND secret_group IS NOT NULL) +
                         (SELECT COUNT(*) FROM item_images WHERE item_id = items.id AND is_sold = 0 AND secret_group IS NULL)
                       ) as stock_count
                FROM items WHERE category_id = ?
            """, (cat_id,)) as cursor:
                items = await cursor.fetchall()

        if not items: continue
        
        # Only show items that actually have stock to avoid flooding
        stock_items = [i for i in items if i[2] > 0]
        if not stock_items: continue

        cat_text = f"━━━━━━━━━━━━━━━━━━━━\n📂 <b>{cat_name}</b>"
        await message.answer(cat_text)

        for i_id, i_name, stock in stock_items:
            # Get specific secrets (Grouped + Individual)
            async with aiosqlite.connect(DB_PATH) as db:
                # 1. Non-NULL groups
                async with db.execute("""
                    SELECT DISTINCT secret_group FROM item_images 
                    WHERE item_id = ? AND is_sold = 0 AND secret_group IS NOT NULL
                """, (i_id,)) as cursor:
                    grouped = await cursor.fetchall()
                
                # 2. Individual NULL groups (Legacy/Single)
                async with db.execute("""
                    SELECT id FROM item_images 
                    WHERE item_id = ? AND is_sold = 0 AND secret_group IS NULL
                """, (i_id,)) as cursor:
                    singles = await cursor.fetchall()
            
            kb_rows = []
            # Add grouped ones
            for idx, s in enumerate(grouped, 1):
                s_id = s[0]
                kb_rows.append([
                    InlineKeyboardButton(text=f"📦 Pachet #{idx}", callback_data=f"adm_view_s_{s_id}"),
                    InlineKeyboardButton(text="🗑 Șterge", callback_data=f"adm_del_s_{s_id}")
                ])
            # Add single ones (use row ID as unique identifier for delete)
            offset = len(grouped)
            for idx, s in enumerate(singles, 1):
                raw_id = s[0]
                kb_rows.append([
                    InlineKeyboardButton(text=f"📦 Pachet #{idx + offset} (Single)", callback_data=f"adm_view_r_{raw_id}"),
                    InlineKeyboardButton(text="🗑 Șterge", callback_data=f"adm_del_r_{raw_id}")
                ])
            
            kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
            await message.answer(f"🛍 <b>{i_name}</b>\nStoc: <code>{stock}</code> pachete", reply_markup=kb)
            await asyncio.sleep(0.3) # Avoid flood

@router.callback_query(F.data.startswith("adm_view_s_"))
async def cb_view_secret_content(callback: CallbackQuery):
    s_id = callback.data.split("_")[3]
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT image_url, media_type FROM item_images WHERE secret_group = ?", (s_id,)) as cursor:
            items = await cursor.fetchall()
    
    if not items:
        await callback.answer("Secretul nu mai există.", show_alert=True)
        return
        
    await callback.message.answer(f"📦 <b>Conținut Pachet:</b> <code>{s_id}</code>")
    for val, mt in items:
        try:
            if mt == 'photo': await callback.message.answer_photo(val)
            elif mt == 'video': await callback.message.answer_video(val)
            else: await callback.message.answer(f"📝 {val}")
        except Exception as e:
             await callback.message.answer(f"⚠️ <b>Media Error:</b>\nTip: <code>{mt}</code>\nID: <code>{val}</code>\n<i>Notă: Dacă ai schimbat token-ul botului, fișierele vechi nu mai pot fi afișate. Trebuie să le re-adaugi.</i>")
    await callback.answer()

@router.callback_query(F.data.startswith("adm_del_s_"))
async def cb_del_secret(callback: CallbackQuery):
    s_id = callback.data.split("_")[3]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM item_images WHERE secret_group = ?", (s_id,))
        await db.commit()
    await callback.message.edit_text(f"✅ Pachetul <code>{s_id}</code> a fost șters.")
    await callback.answer("Pachet șters!", show_alert=True)

@router.callback_query(F.data.startswith("adm_view_r_"))
async def cb_view_single_secret(callback: CallbackQuery):
    raw_id = callback.data.split("_")[3]
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT image_url, media_type FROM item_images WHERE id = ?", (raw_id,)) as cursor:
            item = await cursor.fetchone()
    
    if not item:
        await callback.answer("Elementul nu mai există.", show_alert=True)
        return
        
    val, mt = item
    await callback.message.answer(f"📦 <b>Conținut Pachet (Single):</b>")
    try:
        if mt == 'photo': await callback.message.answer_photo(val)
        elif mt == 'video': await callback.message.answer_video(val)
        else: await callback.message.answer(f"📝 {val}")
    except Exception as e:
         await callback.message.answer(f"⚠️ <b>Media Error:</b>\nTip: <code>{mt}</code>\nID: <code>{val}</code>\n<i>Notă: Dacă ai schimbat token-ul botului, fișierele vechi nu mai pot fi afișate. Trebuie să le re-adaugi.</i>")
    await callback.answer()

@router.callback_query(F.data.startswith("adm_del_r_"))
async def cb_del_single_secret(callback: CallbackQuery):
    raw_id = callback.data.split("_")[3]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM item_images WHERE id = ?", (raw_id,))
        await db.commit()
    await callback.message.edit_text(f"✅ Elementul individual <code>{raw_id}</code> a fost șters.")
    await callback.answer("Șters!", show_alert=True)

@router.callback_query(F.data.startswith("adm_appr_"))
async def cb_admin_approve(callback: CallbackQuery):
    sale_id = int(callback.data.split("_")[2])
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT sales.item_id, sales.user_id, items.name, users.telegram_id, sales.amount_expected, sales.address_used
            FROM sales 
            JOIN items ON sales.item_id = items.id
            JOIN users ON sales.user_id = users.id
            WHERE sales.id = ?
        """, (sale_id,)) as cursor:
            data = await cursor.fetchone()
            
        if not data:
            await callback.answer("Comanda nu mai există.")
            return
            
        item_id, user_db_id, item_name, user_tg_id, amount, address = data
        
        async with db.execute("SELECT id, image_url, media_type, secret_group FROM item_images WHERE item_id = ? AND is_sold = 0 LIMIT 1", (item_id,)) as cursor:
            image_row = await cursor.fetchone()
            
        if not image_row:
            await callback.answer("EROARE: Stoc epuizat pentru acest produs!", show_alert=True)
            return
            
        img_db_id, _, _, group_id = image_row
        
        # Fetch the whole bundle
        if group_id:
            async with db.execute("SELECT id, image_url, media_type FROM item_images WHERE secret_group = ?", (group_id,)) as cursor:
                bundle_items = await cursor.fetchall()
        else:
            async with db.execute("SELECT id, image_url, media_type FROM item_images WHERE id = ?", (img_db_id,)) as cursor:
                bundle_items = await cursor.fetchall()

        # Mark as paid and sold
        for b_id, _, _ in bundle_items:
            await db.execute("UPDATE item_images SET is_sold = 1 WHERE id = ?", (b_id,))
            
        await db.execute("UPDATE sales SET status = 'paid', amount_paid = ?, image_id = ?, tx_hash = 'MANUAL_' || ? WHERE id = ?", (amount, img_db_id, sale_id, sale_id))
        await db.execute("UPDATE addresses SET in_use_by_sale_id = NULL, locked_until = NULL WHERE crypto_address = ?", (address,))
        await db.commit()
        
        # Deliver to user
        try:
            await callback.bot.send_message(user_tg_id, f"🎉 <b>LIVRARE PRODUS! (Aprobat Manual)</b>\n\n🛍 <b>{item_name}</b>\n\nIată pachetul tău:")
            for _, val, mt in bundle_items:
                try:
                    if mt == 'photo': await callback.bot.send_photo(user_tg_id, photo=val)
                    elif mt == 'video': await callback.bot.send_video(user_tg_id, video=val)
                    else: await callback.bot.send_message(user_tg_id, f"<code>{val}</code>")
                except Exception:
                    await callback.bot.send_message(user_tg_id, f"<code>{val}</code>")
            
            success_label = f"✅ Comanda #{sale_id} a fost finalizată și livrată!"
            if callback.message.photo:
                await callback.message.edit_caption(caption=success_label)
            else:
                await callback.message.edit_text(success_label)
                
            await callback.answer("Succes!", show_alert=True)
        except Exception as e:
            await callback.answer(f"Livrat în DB, dar eroare trimitere mesaj: {e}", show_alert=True)

@router.callback_query(F.data.startswith("adm_canc_"))
async def cb_admin_cancel_sale(callback: CallbackQuery):
    sale_id = int(callback.data.split("_")[2])
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT address_used, user_id FROM sales WHERE id = ?", (sale_id,)) as cursor:
            row = await cursor.fetchone()
        if row:
            await db.execute("UPDATE sales SET status = 'cancelled' WHERE id = ?", (sale_id,))
            await db.execute("UPDATE addresses SET in_use_by_sale_id = NULL, locked_until = NULL WHERE crypto_address = ?", (row[0],))
            await db.commit()
            
    cancel_label = f"❌ Comanda #{sale_id} a fost anulată de Admin."
    if callback.message.photo:
        await callback.message.edit_caption(caption=cancel_label)
    else:
        await callback.message.edit_text(cancel_label)
    await callback.answer("Comandă anulată.")




@router.callback_query(F.data.startswith("pre_"))
async def cb_preorder_decision(callback: CallbackQuery):
    parts = callback.data.split("_")
    decision = parts[1] # yes/no
    user_id = int(parts[2])
    item_id = int(parts[3])
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT name FROM items WHERE id = ?", (item_id,)) as cursor:
            item = await cursor.fetchone()
    
    item_name = item[0] if item else "produsul selectat"
    
    if decision == "yes":
        msg_to_user = (
            f"✅ <b>Precomandă Aprobată!</b>\n\n"
            f"Adminul a aprobat cererea ta pentru: <b>{item_name}</b>\n\n"
            "Te rugăm să contactezi @creierosuz pentru a finaliza plata și a primi detalii despre livrare."
        )
        status_text = f"✅ Ai APROBAT precomanda clientului {user_id} pentru {item_name}."

    else:
        msg_to_user = (
            f"❌ <b>Precomandă Respinsă</b>\n\n"
            f"Din păcate, precomanda ta pentru <b>{item_name}</b> nu a put "
            "fi confirmată în acest moment. Poți reîncerca mai târziu sau alege alt produs."
        )
        status_text = f"❌ Ai RESPINS precomanda clientului {user_id} pentru {item_name}."
        
    try:
        await callback.bot.send_message(user_id, msg_to_user)
    except:
        status_text += " (Eroare: Userul a blocat botul?)"
        
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM preorders WHERE user_id = (SELECT id FROM users WHERE telegram_id = ?) AND item_id = ?", (user_id, item_id))
        await db.commit()

    if callback.message.photo:
        await callback.message.edit_caption(caption=status_text)
    else:
        await callback.message.edit_text(status_text)
        
    await callback.answer()

@router.callback_query(F.data == "admin_main")

async def cb_admin_main(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Neautorizat", show_alert=True)
        return
    
    await state.clear()
    from handlers.user import BOT_START_TIME
    uptime = int(time.time() - BOT_START_TIME)
    text = f"🛠 <b>Panou Administrare</b>\n⏱ Uptime: {uptime}s\n\nDe aici poți gestiona categoriile, produsele și stocul magazinului."
    
    img_path = "assets/admin.png"
    if callback.message.photo and os.path.exists(img_path):
        from aiogram.types import InputMediaPhoto
        await callback.message.edit_media(
            media=InputMediaPhoto(media=FSInputFile(img_path), caption=text),
            reply_markup=admin_main_menu()
        )
    else:
        if os.path.exists(img_path):
            await callback.message.answer_photo(FSInputFile(img_path), caption=text, reply_markup=admin_main_menu())
            await callback.message.delete()
        else:
            await callback.message.edit_text(text, reply_markup=admin_main_menu())
    await callback.answer()


@router.callback_query(F.data.startswith("admin_"))
async def cb_admin_actions(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Neautorizat", show_alert=True)
        return
        
    action = callback.data.split("_")[1]
    
    # --- ADD ACTIONS ---
    if action == "cats":
        label = "Trimite **Emoji-ul** pentru noua categorie (Ex: ❄️):"
        if callback.message.photo: await callback.message.edit_caption(caption=label)
        else: await callback.message.edit_text(label)
        await state.set_state(AdminCategory.waiting_for_name)
        
    elif action == "items":
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT id, name FROM categories") as cursor:
                cats = await cursor.fetchall()
        if not cats:
            await callback.answer("Nu există categorii!", show_alert=True)
            return
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=cat[1], callback_data=f"ai_cat_{cat[0]}")] for cat in cats])
        kb.inline_keyboard.append([InlineKeyboardButton(text="❌ Anulare", callback_data="admin_main")])
        label = "Selectați categoria pentru noul produs:"
        if callback.message.photo: await callback.message.edit_caption(caption=label, reply_markup=kb)
        else: await callback.message.edit_text(label, reply_markup=kb)
        await state.set_state(AdminItem.waiting_for_category)
        
    elif action == "stock":
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT id, name FROM categories") as cursor:
                cats = await cursor.fetchall()
        if not cats:
            await callback.answer("Nu există categorii!", show_alert=True)
            return
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=cat[1], callback_data=f"as_cat_{cat[0]}")] for cat in cats])
        kb.inline_keyboard.append([InlineKeyboardButton(text="❌ Anulare", callback_data="admin_main")])
        label = "📦 <b>Adăugare Stoc</b>\nSelectați categoria din care face parte produsul:"
        if callback.message.photo: await callback.message.edit_caption(caption=label, reply_markup=kb)
        else: await callback.message.edit_text(label, reply_markup=kb)
        # state is NOT set here yet, we wait for as_cat_...
        
    elif action == "history":

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
                SELECT sales.id, items.name, sales.amount_paid, users.username, users.telegram_id, sales.image_id, item_images.image_url
                FROM sales
                JOIN items ON sales.item_id = items.id
                JOIN users ON sales.user_id = users.id
                JOIN item_images ON sales.image_id = item_images.id
                WHERE sales.status = 'paid'
                ORDER BY sales.created_at DESC
                LIMIT 10
            """) as cursor:
                sales = await cursor.fetchall()
        
        if not sales:
            await callback.answer("Nu există vânzări încă.", show_alert=True)
            return
            
        text = "📈 <b>Ultimele 10 Vânzări Confirmed:</b>\n\n"
        kb_rows = []
        for s in sales:
            text += f"🔹 #{s[0]} | <b>{s[1]}</b>\n👤 @{s[3] or 'N/A'} (<code>{s[4]}</code>)\n🔑 Secret ID: <code>{s[5]}</code>\n\n"
            kb_rows.append([InlineKeyboardButton(text=f"🔄 Retrimite #{s[0]}", callback_data=f"resend_{s[0]}", style="primary")])

            
        kb_rows.append([InlineKeyboardButton(text="🔙 Înapoi", callback_data="admin_main")])
        kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
        
        if callback.message.photo: await callback.message.edit_caption(caption=text, reply_markup=kb)
        else: await callback.message.edit_text(text, reply_markup=kb)

    elif action == "cancelled":
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
                SELECT sales.id, items.name, sales.amount_expected, users.username, users.telegram_id, sales.created_at
                FROM sales
                JOIN items ON sales.item_id = items.id
                JOIN users ON sales.user_id = users.id
                WHERE sales.status = 'cancelled'
                ORDER BY sales.created_at DESC
                LIMIT 10
            """) as cursor:
                cancelled = await cursor.fetchall()
        
        if not cancelled:
            await callback.answer("Nu există comenzi anulate.", show_alert=True)
            return
            
        text = "❌ <b>Ultimele 10 Comenzi Anulate:</b>\n\n"
        kb_rows = []
        for c in cancelled:
            text += f"🔹 #{c[0]} | <b>{c[1]}</b>\n👤 @{c[3] or 'N/A'} (<code>{c[4]}</code>)\n🕒 {c[5]}\n\n"
            kb_rows.append([InlineKeyboardButton(text=f"✅ Finalizează #{c[0]}", callback_data=f"adm_appr_{c[0]}", style="success")])

            
        kb_rows.append([InlineKeyboardButton(text="🔙 Înapoi", callback_data="admin_main")])
        kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
        
        if callback.message.photo: await callback.message.edit_caption(caption=text, reply_markup=kb)
        else: await callback.message.edit_text(text, reply_markup=kb)

    elif action == "preorders":
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
                SELECT preorders.id, items.name, users.username, users.telegram_id, preorders.created_at, items.id
                FROM preorders
                JOIN items ON preorders.item_id = items.id
                JOIN users ON preorders.user_id = users.id
                ORDER BY preorders.created_at DESC
                LIMIT 15
            """) as cursor:
                preorders = await cursor.fetchall()
        
        if not preorders:
            await callback.answer("Nu există precomenzi înregistrate.", show_alert=True)
            return
            
        text = "⏳ <b>Ultimele 15 Precomenzi:</b>\n\n"
        kb_rows = []
        for p in preorders:
            text += f"🔹 #{p[0]} | <b>{p[1]}</b>\n👤 @{p[2] or 'N/A'} (<code>{p[3]}</code>)\n🕒 {p[4]}\n\n"
            kb_rows.append([
                InlineKeyboardButton(text=f"✅ #{p[0]}", callback_data=f"pre_yes_{p[3]}_{p[5]}"),
                InlineKeyboardButton(text=f"❌ #{p[0]}", callback_data=f"pre_no_{p[3]}_{p[5]}")
            ])
            
        kb_rows.append([InlineKeyboardButton(text="🔙 Înapoi", callback_data="admin_main")])
        kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
        
        if callback.message.photo: await callback.message.edit_caption(caption=text, reply_markup=kb)
        else: await callback.message.edit_text(text, reply_markup=kb)

    # --- REMOVE ACTIONS ---

    elif action == "rem":
        sub_type = callback.data.split("_")[2] # cat, item, stock
        async with aiosqlite.connect(DB_PATH) as db:
            if sub_type == "cat":
                async with db.execute("SELECT id, name FROM categories") as cursor:
                    cats = await cursor.fetchall()
                if not cats: await callback.answer("Nu există categorii!", show_alert=True); return
                kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🗑 {c[1]}", callback_data=f"del_cat_{c[0]}")] for c in cats])
                label = "⚠️ <b>Selectați categoria de ȘTERS:</b>\n(Atenție: Va șterge toate produsele din ea!)"
            elif sub_type == "item":
                async with db.execute("SELECT id, name FROM items") as cursor:
                    items = await cursor.fetchall()
                if not items: await callback.answer("Nu există produse!", show_alert=True); return
                kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🗑 {i[1]}", callback_data=f"del_item_{i[0]}")] for i in items])
                label = "⚠️ <b>Selectați produsul de ȘTERS:</b>"
            elif sub_type == "stock":
                async with db.execute("""
                    SELECT DISTINCT items.id, items.name 
                    FROM items 
                    JOIN item_images ON items.id = item_images.item_id 
                    WHERE item_images.is_sold = 0
                """) as cursor:
                    items = await cursor.fetchall()
                if not items: await callback.answer("Nu există produse cu stoc disponibil (secrete)!", show_alert=True); return
                kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🧹 Golește: {i[1]}", callback_data=f"clr_stock_{i[0]}")] for i in items])
                label = "⚠️ <b>GOLIȚI STOCUL (Secretele)</b>\nAcestea sunt produsele care au stoc activ:"
            
            kb.inline_keyboard.append([InlineKeyboardButton(text="🔙 Înapoi", callback_data="admin_main")])
            if callback.message.photo: await callback.message.edit_caption(caption=label, reply_markup=kb)
            else: await callback.message.edit_text(label, reply_markup=kb)
            
    await callback.answer()

@router.callback_query(F.data.startswith("resend_"))
async def cb_admin_resend_secret(callback: CallbackQuery):
    sale_id = int(callback.data.split("_")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT items.name, item_images.image_url, users.telegram_id
            FROM sales
            JOIN items ON sales.item_id = items.id
            JOIN users ON sales.user_id = users.id
            JOIN item_images ON sales.image_id = item_images.id
            WHERE sales.id = ?
        """, (sale_id,)) as cursor:
            data = await cursor.fetchone()
            
    if data:
        name, img_url, user_tg_id = data
        msg_text = f"📦 <b>Retrimitere Comandă #{sale_id}</b>\nAdminul ți-a retrimis conținutul pentru: <b>{name}</b>"
        try:
            if img_url.startswith("http") or len(img_url) > 40:
                await callback.bot.send_photo(user_tg_id, photo=img_url, caption=msg_text)
            else:
                await callback.bot.send_message(user_tg_id, f"{msg_text}\n\nConținut:\n<code>{img_url}</code>")
            await callback.answer(f"✅ Secret retrimis utilizatorului (TG ID: {user_tg_id})", show_alert=True)
        except Exception as e:
            await callback.answer(f"❌ Eroare la trimitere: {e}", show_alert=True)


# --- DELETE LOGIC ---
@router.callback_query(F.data.startswith("as_cat_"))
async def cb_stock_cat(callback: CallbackQuery, state: FSMContext):
    cat_id = int(callback.data.split("_")[2])
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id, name FROM items WHERE category_id = ?", (cat_id,)) as cursor:
            items = await cursor.fetchall()
    
    if not items:
        await callback.answer("Nu există produse în această categorie!", show_alert=True)
        return
        
    kb_rows = []
    for i in items:
        kb_rows.append([InlineKeyboardButton(text=i[1], callback_data=f"as_item_{i[0]}")])
    kb_rows.append([InlineKeyboardButton(text="🔙 Înapoi", callback_data="admin_actions_stock")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    
    text = f"📦 <b>Produse în Categorie</b>\nSelectați produsul pentru stoc:"
    if callback.message.photo: await callback.message.edit_caption(caption=text, reply_markup=kb)
    else: await callback.message.edit_text(text, reply_markup=kb)
    await state.set_state(AdminStock.waiting_for_item)
    await callback.answer()

@router.callback_query(F.data.startswith("del_cat_"))
async def cb_del_cat(callback: CallbackQuery, state: FSMContext):
    cat_id = int(callback.data.split("_")[2])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM item_images WHERE item_id IN (SELECT id FROM items WHERE category_id = ?)", (cat_id,))
        await db.execute("DELETE FROM items WHERE category_id = ?", (cat_id,))
        await db.execute("DELETE FROM categories WHERE id = ?", (cat_id,))
        await db.commit()
    await callback.answer("Categoria a fost ștearsă!", show_alert=True)
    await cb_admin_main(callback, state)

@router.callback_query(F.data.startswith("del_item_"))
async def cb_del_item(callback: CallbackQuery, state: FSMContext):
    item_id = int(callback.data.split("_")[2])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM item_images WHERE item_id = ?", (item_id,))
        await db.execute("DELETE FROM items WHERE id = ?", (item_id,))
        await db.commit()
    await callback.answer("Produsul a fost șters!", show_alert=True)
    await cb_admin_main(callback, state)

@router.callback_query(F.data.startswith("clr_stock_"))
async def cb_clr_stock(callback: CallbackQuery, state: FSMContext):
    item_id = int(callback.data.split("_")[2])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM item_images WHERE item_id = ? AND is_sold = 0", (item_id,))
        await db.commit()
    await callback.answer("Stocul a fost golit!", show_alert=True)
    await cb_admin_main(callback, state)

# --- CATEGORY ADDITION ---
@router.message(AdminCategory.waiting_for_name)
async def process_cat_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if not is_emoji_only(name):
        await message.answer("❌ Te rog trimite **doar emoji** pentru numele categoriei!")
        return
        
    await state.update_data(name=name)
    await message.answer(f"Emoji '{name}' setat. Trimite URL-ul sau Imaginea de fundal pentru această categorie:")
    await state.set_state(AdminCategory.waiting_for_image)

@router.message(AdminCategory.waiting_for_image)
async def process_cat_image(message: Message, state: FSMContext):
    image_url = message.text.strip() if message.text else None
    if message.photo:
        image_url = message.photo[-1].file_id
    
    data = await state.get_data()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO categories (name, display_image) VALUES (?, ?)", (data['name'], image_url))
        await db.commit()
    await message.answer(f"Categoria {data['name']} a fost adăugată!", reply_markup=admin_main_menu())
    await state.clear()

# --- ITEM ADDITION ---
@router.callback_query(AdminItem.waiting_for_category, F.data.startswith("ai_cat_"))
async def process_item_category(callback: CallbackQuery, state: FSMContext):
    cat_id = int(callback.data.split("_")[2])
    await state.update_data(cat_id=cat_id)
    label = "Trimite numele produsului (Ex: 1x❄️ = 500 RON):"
    if callback.message.photo: await callback.message.edit_caption(caption=label)
    else: await callback.message.edit_text(label)
    await state.set_state(AdminItem.waiting_for_name)
    await callback.answer()

@router.message(AdminItem.waiting_for_name)
async def process_item_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await message.answer("Trimite descrierea produsului:")
    await state.set_state(AdminItem.waiting_for_description)

@router.message(AdminItem.waiting_for_description)
async def process_item_description(message: Message, state: FSMContext):
    await state.update_data(description=message.text.strip())
    await message.answer("Trimite prețul produsului în **RON** (Ex: 500):")
    await state.set_state(AdminItem.waiting_for_price_ron)

@router.message(AdminItem.waiting_for_price_ron)
async def process_item_price_ron(message: Message, state: FSMContext):
    try:
        price_ron = float(message.text.strip())
        await state.update_data(price_ron=price_ron)
        await message.answer("Trimite URL-ul sau Imaginea de previzualizare pentru produs:")
        await state.set_state(AdminItem.waiting_for_image)
    except ValueError:
        await message.answer("Preț invalid. Trimiteți un număr.")

@router.message(AdminItem.waiting_for_image)
async def process_item_image(message: Message, state: FSMContext):
    image_url = message.text.strip() if message.text else None
    if message.photo:
        image_url = message.photo[-1].file_id
    
    data = await state.get_data()
    RON_TO_LTC_RATE = 280.0 
    price_ltc = round(data['price_ron'] / RON_TO_LTC_RATE, 4)
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO items (category_id, name, description, price_ron, price_ltc, display_image) VALUES (?, ?, ?, ?, ?, ?)",
            (data['cat_id'], data['name'], data['description'], data['price_ron'], price_ltc, image_url)
        )
        await db.commit()
    
    await message.answer(f"Produsul '{data['name']}' a fost adăugat!\nPreț: {price_ltc} LTC", reply_markup=admin_main_menu())
    await state.clear()

# --- STOCK ADDITION ---
@router.callback_query(AdminStock.waiting_for_item, F.data.startswith("as_item_"))
async def process_stock_item(callback: CallbackQuery, state: FSMContext):
    item_id = int(callback.data.split("_")[2])
    bundle_id = str(uuid.uuid4())[:8]
    
    # Fetch item name for display
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT name FROM items WHERE id = ?", (item_id,)) as c:
            row = await c.fetchone()
    item_name = row[0] if row else f"Item #{item_id}"
    
    await state.update_data(item_id=item_id, item_name=item_name, bundle_id=bundle_id, bundle_count=0, last_media_group=None)

    label = (
        f"📦 <b>Adaugă Secret pentru: {item_name}</b>\n\n"
        "Trimite orice fișier(e): imagini, video, text.\n"
        "Poți trimite câte vrei — toate vor fi grupate într-un singur secret.\n\n"
        "Apasă <b>GATA</b> când ai terminat secretul curent.\n"
        "După, poți adăuga alt secret sau ieși la meniu."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ GATA (Finalizează Secretul)", callback_data="admin_stock_finish")],
        [InlineKeyboardButton(text="❌ Anulează", callback_data="admin_main")]
    ])

    if callback.message.photo: await callback.message.edit_caption(caption=label, reply_markup=kb)
    else: await callback.message.edit_text(label, reply_markup=kb)
    await state.set_state(AdminStock.waiting_for_bundle)
    await callback.answer()

@router.callback_query(F.data == "admin_stock_finish")
async def cb_admin_stock_finish(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    count = data.get('bundle_count', 0)
    if count == 0:
        await callback.answer("⚠️ Nu ai adăugat niciun fișier în secretul curent!", show_alert=True)
        return

    item_id = data.get('item_id')
    item_name = data.get('item_name', f'Item #{item_id}')
    
    # Offer: finish entirely OR start a new secret for the same item
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Adaugă Alt Secret (același produs)", callback_data="admin_stock_new_secret")],
        [InlineKeyboardButton(text="✅ Gata! Ieși la meniu", callback_data="admin_stock_done")],
    ])
    await callback.message.answer(
        f"✅ Secret salvat! ({count} element(e) în pachet)\n\n"
        f"Produs: <b>{item_name}</b>\n\n"
        "Vrei să adaugi un alt secret pentru același produs sau ești gata?",
        reply_markup=kb
    )
    await callback.answer()

@router.callback_query(F.data == "admin_stock_new_secret")
async def cb_admin_stock_new_secret(callback: CallbackQuery, state: FSMContext):
    """Start a fresh bundle for the same item."""
    data = await state.get_data()
    new_bundle_id = str(uuid.uuid4())[:8]
    await state.update_data(bundle_id=new_bundle_id, bundle_count=0, last_media_group=None)
    
    item_name = data.get('item_name', '')
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ GATA (Finalizează Secretul)", callback_data="admin_stock_finish")],
        [InlineKeyboardButton(text="❌ Renunță", callback_data="admin_stock_done")],
    ])
    await callback.message.answer(
        f"📦 <b>Secret Nou</b> pentru <b>{item_name}</b>\n\n"
        "Trimite imagini, video sau text pentru acest secret.\n"
        "Poți trimite câte fișiere vrei. Apasă GATA când termini.",
        reply_markup=kb
    )
    await state.set_state(AdminStock.waiting_for_bundle)
    await callback.answer()

@router.callback_query(F.data == "admin_stock_done")
async def cb_admin_stock_done(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("✅ Stoc adăugat cu succes!", reply_markup=admin_main_menu())
    await callback.answer()

@router.message(AdminStock.waiting_for_bundle)
async def process_stock_bundle(message: Message, state: FSMContext):
    media_type = 'text'
    value = message.text.strip() if message.text else None

    if message.photo:
        media_type = 'photo'
        value = message.photo[-1].file_id
    elif message.video:
        media_type = 'video'
        value = message.video.file_id
    elif message.document:
        if message.document.mime_type:
            if message.document.mime_type.startswith("image/"): media_type = 'photo'
            elif message.document.mime_type.startswith("video/"): media_type = 'video'
            else: media_type = 'document'
        value = message.document.file_id
    elif message.audio:
        media_type = 'audio'
        value = message.audio.file_id

    if not value:
        await message.answer("⚠️ Tip de fișier nesuportat. Trimite text, poză, video sau document.")
        return

    data = await state.get_data()
    bundle_id = data.get('bundle_id')
    
    # Deduplicate media albums: if same media_group_id, don't re-announce but still save
    media_group_id = message.media_group_id
    last_media_group = data.get('last_media_group')

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO item_images (item_id, image_url, media_type, secret_group) VALUES (?, ?, ?, ?)",
            (data['item_id'], value, media_type, bundle_id)
        )
        await db.commit()

    new_count = data.get('bundle_count', 0) + 1
    await state.update_data(bundle_count=new_count, last_media_group=media_group_id)

    # Only show the confirmation message once per album group (or always for single files)
    if media_group_id and media_group_id == last_media_group:
        # Part of current album batch — silently added, no extra message spam
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ GATA (Finalizează Secretul)", callback_data="admin_stock_finish")],
        [InlineKeyboardButton(text="❌ Renunță", callback_data="admin_stock_done")],
    ])
    type_icons = {'photo': '🖼', 'video': '🎬', 'text': '📝', 'document': '📄', 'audio': '🎵'}
    icon = type_icons.get(media_type, '📁')
    await message.answer(
        f"{icon} Element #{new_count} ({media_type}) adăugat în secret.\n"
        "Trimite mai multe sau apasă GATA când ai terminat.",
        reply_markup=kb
    )
