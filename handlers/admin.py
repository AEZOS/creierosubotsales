import asyncio
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile, FSInputFile, InputMediaPhoto
from utils.keyboards import admin_main_menu
from config import ADMIN_IDS
from aiogram.fsm.context import FSMContext
from handlers.states import AdminCategory, AdminItem, AdminStock, AdminRemoval, AdminAddress, AdminPreorder, AdminReplyState
from database import DB_PATH, db_session, is_silent_mode, set_silent_mode, get_last_completed_sales, restore_secret_and_delete_sale, get_item_stats, get_user_total_sales, is_blackmagic_on, set_blackmagic
from utils.image_cleaner import strip_exif
from utils.ui import smart_edit
import psycopg
import logging
import io
import re
import os
import uuid
import time
from datetime import datetime, timedelta
import asyncio


router = Router()

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# smart_edit removed, now using utils.ui.smart_edit

@router.message(Command("check"))
async def cmd_check_slots(message: Message, auth_id: int = None):
    if not is_admin(auth_id if auth_id is not None else message.from_user.id): return
    await show_addresses_menu(message)

async def show_addresses_menu(reply_target):
    """Shows the interactive LTC addresses management panel."""
    from datetime import datetime
    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("SELECT id, crypto_address, in_use_by_sale_id, locked_until FROM addresses ORDER BY id ASC")
            slots = await cursor.fetchall()

    now = datetime.now()
    text = "� <b>GESTIONARE ADRESE LTC</b>\n\n"
    kb_rows = []

    for s in slots:
        slot_id = s['id']
        addr = s['crypto_address']
        sale_id = s['in_use_by_sale_id']
        locked = s['locked_until']

        if addr.startswith("UNSET_SLOT"):
            status = "⬜ NESETAT"
            display = "(nesetat)"
        elif sale_id:
            status = f"🛒 ÎN UZ (#{sale_id})"
            display = addr[:18] + "..."
        else:
            status = "✅ LIBER"
            if locked:
                try:
                    locked_dt = locked if not isinstance(locked, str) else datetime.strptime(locked, '%Y-%m-%d %H:%M:%S')
                    if locked_dt > now:
                        status = f"🛡️ COOLDOWN ({locked_dt.strftime('%H:%M')})"
                except: pass
            display = addr[:18] + "..."

        text += f"<b>Slot #{slot_id}</b> — {status}\n<code>{addr}</code>\n\n"
        kb_rows.append([
            InlineKeyboardButton(text=f"✏️ Set #{slot_id}",   callback_data=f"edit_slot_{slot_id}"),
            InlineKeyboardButton(text=f"🗑️ Reset #{slot_id}", callback_data=f"reset_slot_{slot_id}")
        ])

    kb_rows.append([InlineKeyboardButton(text="🔙 Înapoi", callback_data="admin_main")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)

    # reply_target can be a Message or a CallbackQuery.message
    await reply_target.answer(text, reply_markup=kb)


@router.message(Command("silent"))
async def cmd_silent_toggle(message: Message):
    if not is_admin(message.from_user.id): return
    
    current = await is_silent_mode()
    
    # If already on, show the management menu
    if current:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🧹 Gestionare Comenzi și Stoc", callback_data="admin_silent_mgmt")],
            [InlineKeyboardButton(text="🔔 Dezactivează Silent Mode", callback_data="admin_silent_off")]
        ])
        await message.answer(
            "🔕 <b>SILENT MODE ESTE ACTIV</b>\n\nNotificările sunt oprite (cu excepția ID 7725170652).",
            reply_markup=kb
        )
    else:
        await set_silent_mode(True)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🧹 Gestionare Comenzi și Stoc", callback_data="admin_silent_mgmt")],
            [InlineKeyboardButton(text="🔔 Dezactivează Silent Mode", callback_data="admin_silent_off")]
        ])
        await message.answer(
            "🔕 <b>SILENT MODE ACTIVAT</b>\n\nNiciun admin (cu excepția ID 7725170652) nu va mai primi notificări despre intenții sau precomenzi.",
            reply_markup=kb
        )

@router.callback_query(F.data == "admin_silent_off")
async def cb_silent_off(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    await set_silent_mode(False)
    await smart_edit(callback, "🔔 <b>SILENT MODE DEZACTIVAT</b>\n\nToți adminii primesc agora notificări.")
    await callback.answer()

@router.message(Command("blackmagic"))
async def cmd_blackmagic_toggle(message: Message):
    if not is_admin(message.from_user.id): return
    
    # Check current state
    current = await is_blackmagic_on()
    args = message.text.split()
    
    if len(args) > 1:
        if args[1].lower() == "on":
            await set_blackmagic(True)
            await message.answer("🌑 <b>BLACK MAGIC: ON</b>\n\nToate pozele secrete vor fi înlocuite cu imagini negre (STEALTH MODE ACTIVE).")
        elif args[1].lower() == "off":
            await set_blackmagic(False)
            await message.answer("🌕 <b>BLACK MAGIC: OFF</b>\n\nPozele originale vor fi trimise din nou clienților.")
        return

    # No args, just toggle
    new_state = not current
    await set_blackmagic(new_state)
    status = "ON 🌑" if new_state else "OFF 🌕"
    await message.answer(f"🌑 <b>BLACK MAGIC STATUS:</b> {status}\n\nFolosește <code>/blackmagic on</code> sau <code>/blackmagic off</code> pentru control precis.")

@router.callback_query(F.data == "admin_silent_mgmt")
async def cb_silent_mgmt(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    await show_silent_mgmt_menu(callback.message)
    await callback.answer()

async def show_silent_mgmt_menu(message: Message):
    sales = await get_last_completed_sales(limit=5)
    
    if not sales:
        await message.answer("ℹ️ Nu există comenzi finalizate recente pentru gestionare.")
        return

    text = "🧹 <b>GESTIONARE COMENZI (Ultimile 5)</b>\n\nPoți șterge o comandă și să pui produsul înapoi în stoc:\n\n"
    kb_rows = []
    
    for s in sales:
        s_id = s['id']
        name = s['name']
        user = s['username']
        text += f"📦 #{s_id} | {name} | {user}\n"
        kb_rows.append([InlineKeyboardButton(text=f"🗑️ Restaurare #{s_id}", callback_data=f"silent_restore_{s_id}")])
    
    kb_rows.append([InlineKeyboardButton(text="🔙 Înapoi", callback_data="admin_main")])
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))

@router.callback_query(F.data.startswith("silent_restore_"))
async def cb_silent_restore(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    sale_id = int(callback.data.split("_")[2])
    
    await restore_secret_and_delete_sale(sale_id)
    await callback.answer(f"✅ Comanda #{sale_id} ștearsă și stocul a fost restaurat!", show_alert=True)
    # Refresh menu
    await callback.message.delete()
    await show_silent_mgmt_menu(callback.message)

def is_emoji_only(text: str) -> bool:
    clean_text = text.replace(" ", "").strip()
    if not clean_text: return False
    return not any(c.isalnum() for c in clean_text)

@router.message(Command("link"))
async def cmd_link(message: Message):
    if not is_admin(message.from_user.id): return
    
    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("SELECT value FROM bot_settings WHERE key = 'dashboard_url'")
            row = await cursor.fetchone()
            url = row['value'] if row else None
            
    if url:
        await message.answer(f"🌐 <b>Dashboard-ul tău Creierosu este live aici:</b>\n\n{url}\n\n<i>*Acest link se schimbă la fiecare restart al botului.</i>", disable_web_page_preview=True)
    else:
        await message.answer("⚠️ Link-ul nu este disponibil. Asigură-te că botul rulează din `main.py` și tunelul Serveo s-a inițializat cu succes.")

@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        return
    from handlers.user import BOT_START_TIME
    uptime = int(time.time() - BOT_START_TIME)
    
    text = f"🛠 <b>Control Panel Administrator</b>\n⏱ Uptime: {uptime}s\n\n"
    text += "(Dacă vezi mai multe uptime-uri diferite când dai click, înseamnă că ai mai multe instanțe pornite!)"
    
    img_path = "assets/admin.png"
    if os.path.exists(img_path):
        await message.answer_photo(FSInputFile(img_path), caption=text, reply_markup=admin_main_menu())
    else:
        await message.answer(text, reply_markup=admin_main_menu())

@router.callback_query(F.data == "admin_main")
async def cb_admin_main(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    from handlers.user import BOT_START_TIME
    uptime = int(time.time() - BOT_START_TIME)
    text = f"🛠 <b>Control Panel Administrator</b>\n⏱ Uptime: {uptime}s\n\n(Dacă vezi mai multe uptime-uri diferite când dai click, înseamnă că ai mai multe instanțe pornite!)"
    img_path = "assets/admin.png"
    
    if os.path.exists(img_path):
        if callback.message.photo:
            try:
                await callback.message.edit_media(media=InputMediaPhoto(media=FSInputFile(img_path), caption=text), reply_markup=admin_main_menu())
            except: pass
        else:
            await callback.message.answer_photo(FSInputFile(img_path), caption=text, reply_markup=admin_main_menu())
            await callback.message.delete()
    else:
        await smart_edit(callback.message, text, reply_markup=admin_main_menu())
    await callback.answer()
@router.callback_query(F.data.startswith("adm_preo_mgmt_"))
async def cb_admin_preo_list(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    try:
        page = int(callback.data.split("_")[3])
    except (ValueError, IndexError):
        page = 0
    limit = 10
    offset = page * limit
    
    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("""
                SELECT p.id, i.name as item_name, u.username, p.status, p.created_at, u.telegram_id
                FROM preorders p
                JOIN items i ON p.item_id = i.id
                JOIN users u ON p.user_id = u.id
                WHERE p.status IN ('pending', 'verifying', 'confirmed', 'accepted')
                ORDER BY p.id DESC
                LIMIT %s OFFSET %s
            """, (limit + 1, offset))
            rows = await cursor.fetchall()
            
    has_next = len(rows) > limit
    rows = rows[:limit]
    
    if not rows and page == 0:
        return await smart_edit(callback, "📭 Nu există precomenzi active.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Înapoi", callback_data="admin_main")]]))
        
    text = f"📥 <b>GESTIUNE PRECOMENZI (Pagina {page+1})</b>\n\n"
    kb_rows = []
    
    for r in rows:
        p_id = r['id']
        i_name = r['item_name']
        uname = r['username']
        status = r['status']
        status_emoji = {"pending": "⏳", "verifying": "🔄", "confirmed": "✅", "accepted": "👌"}.get(status, "❓")
        text += f"{status_emoji} <b>#{p_id}</b> | {i_name} | @{uname or 'N/A'}\n"
        
        row_btns = [InlineKeyboardButton(text=f"⚙️ Detalii #{p_id}", callback_data=f"adm_preo_det_{p_id}")]
        if status == 'confirmed':
            row_btns.append(InlineKeyboardButton(text=f"⏱️ Timp #{p_id}", callback_data=f"adm_preo_timer_{p_id}"))
        kb_rows.append(row_btns)
        
    nav_btns = []
    if page > 0: nav_btns.append(InlineKeyboardButton(text="⬅️", callback_data=f"adm_preo_mgmt_{page-1}"))
    if has_next: nav_btns.append(InlineKeyboardButton(text="➡️", callback_data=f"adm_preo_mgmt_{page+1}"))
    if nav_btns: kb_rows.append(nav_btns)
    
    kb_rows.append([InlineKeyboardButton(text="🏠 Menu Principal", callback_data="admin_main")])
    await smart_edit(callback.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await callback.answer()

@router.callback_query(F.data.startswith("adm_preo_det_"))
async def cb_admin_preo_detail(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    preo_id = int(callback.data.split("_")[3])
    
    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("""
                SELECT p.id, i.name as item_name, u.username, p.status, p.created_at, u.telegram_id, i.id as item_id
                FROM preorders p
                JOIN items i ON p.item_id = i.id
                JOIN users u ON p.user_id = u.id
                WHERE p.id = %s
            """, (preo_id,))
            row = await cursor.fetchone()
            
    if not row: return await callback.answer("Nu mai există.")
    p_id = row['id']
    i_name = row['item_name']
    uname = row['username']
    status = row['status']
    created = row['created_at']
    u_tg_id = row['telegram_id']
    it_id = row['item_id']
    
    # Get current stock
    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("""
                 SELECT (SELECT COUNT(DISTINCT secret_group) FROM item_images WHERE item_id = %s AND is_sold = FALSE AND secret_group IS NOT NULL) +
                        (SELECT COUNT(*) FROM item_images WHERE item_id = %s AND is_sold = FALSE AND secret_group IS NULL) as total_stock
            """, (it_id, it_id))
            stock_res = await cursor.fetchone()
            stock = stock_res['total_stock']

    text = (
        f"📋 <b>DETALII PRECOMANDĂ #{p_id}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Produs: <b>{i_name}</b>\n"
        f"👤 Client: @{uname or 'N/A'} (<code>{u_tg_id}</code>)\n"
        f"🕒 Creată la: {created}\n"
        f"📊 Status: <b>{status.upper()}</b>\n"
        f"📦 Stoc curent: <code>{stock}</code> pachete\n"
    )
    
    kb = [
        [InlineKeyboardButton(text="✅ Acceptă & Notifică", callback_data=f"adm_preo_action_ok_{p_id}")],
        [InlineKeyboardButton(text="🔄 Verifică (Individual)", callback_data=f"adm_preo_verify_{p_id}")],
        [InlineKeyboardButton(text="❌ Refuză / Șterge", callback_data=f"adm_preo_action_no_{p_id}")],
        [InlineKeyboardButton(text="🔙 Înapoi la Listă", callback_data="adm_preo_mgmt_0")]
    ]
    await smart_edit(callback.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await callback.answer()

@router.callback_query(F.data.startswith("adm_preo_verify_"))
async def cb_admin_preo_single_verify(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    preo_id = int(callback.data.split("_")[3])
    
    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("""
                SELECT p.id, i.name as item_name, u.telegram_id 
                FROM preorders p
                JOIN items i ON p.item_id = i.id
                JOIN users u ON p.user_id = u.id
                WHERE p.id = %s
            """, (preo_id,))
            row = await cursor.fetchone()
            
    if not row: return await callback.answer("Nu mai există.")
    p_id = row['id']
    i_name = row['item_name']
    u_tg_id = row['telegram_id']
    
    try:
        msg_text = (
            f"👋 <b>Vânzătorul este acum ONLINE!</b>\n\n"
            f"Ai făcut o precomandă pentru: <b>{i_name}</b> (ID #{p_id}).\n\n"
            f"Încă mai ești interesat? Dacă da, voi pregăti stocul special pentru tine!"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ DA, Încă doresc", callback_data=f"user_preo_valid_yes_{p_id}"),
                InlineKeyboardButton(text="❌ NU, Anulează", callback_data=f"user_preo_valid_no_{p_id}")
            ]
        ])
        await callback.bot.send_message(u_tg_id, msg_text, reply_markup=kb)
        
        async with db_session() as db:
            await db.execute("UPDATE preorders SET status = 'verifying' WHERE id = %s", (p_id,))
            await db.commit()
            
        await callback.answer("✅ Mesaj de verificare trimis către utilizator!", show_alert=True)
        await cb_admin_preo_detail(callback) # Refresh detail view
    except Exception as e:
        await callback.answer(f"❌ Eroare: {e}", show_alert=True)

@router.callback_query(F.data == "adm_preo_mass_verify")
async def cb_admin_preo_mass_verify(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    
    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("""
                SELECT p.id, i.name as item_name, u.telegram_id 
                FROM preorders p
                JOIN items i ON p.item_id = i.id
                JOIN users u ON p.user_id = u.id
                WHERE p.status = 'pending'
            """)
            pending = await cursor.fetchall()
            
    if not pending:
        return await callback.answer("Nu există precomenzi noi (PENDING) de verificat.", show_alert=True)
    
    await callback.answer(f"Se trimit {len(pending)} mesaje de verificare...", show_alert=True)
    
    count = 0
    async with db_session() as db:
        for p_row in pending:
            p_id = p_row['id']
            i_name = p_row['item_name']
            u_tg_id = p_row['telegram_id']
            try:
                msg_text = (
                    f"👋 <b>Vânzătorul este acum ONLINE!</b>\n\n"
                    f"Ai făcut o precomandă pentru: <b>{i_name}</b> (ID #{p_id}).\n\n"
                    f"Încă mai ești interesat? Dacă da, voi pregăti stocul special pentru tine!"
                )
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text="✅ DA, Încă doresc", callback_data=f"user_preo_valid_yes_{p_id}"),
                        InlineKeyboardButton(text="❌ NU, Anulează", callback_data=f"user_preo_valid_no_{p_id}")
                    ]
                ])
                await callback.bot.send_message(u_tg_id, msg_text, reply_markup=kb)
                await db.execute("UPDATE preorders SET status = 'verifying' WHERE id = %s", (p_id,))
                count += 1
                await asyncio.sleep(0.1)
            except: pass
        await db.commit()
        
    await callback.message.answer(f"✅ Finalizat! Am întrebat {count} utilizatori dacă precomenzile lor mai sunt valabile.")
    await cb_admin_preo_list(callback)

@router.callback_query(F.data.startswith("adm_preo_timer_"))
async def cb_admin_preo_timer_ask(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return
    preo_id = int(callback.data.split("_")[3])
    await state.update_data(target_id=preo_id)
    await state.set_state(AdminPreorder.waiting_for_time)
    await callback.message.answer(f"🕒 În câte minute va fi gata precomanda #{preo_id}?\n\nScrie timpul (ex: 20 sau 45):")
    await callback.answer()

@router.message(AdminPreorder.waiting_for_time)
async def process_preo_timer_val(message: Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("Te rog scrie un număr valid de minute.")
    
    data = await state.get_data()
    pre_id = data['target_id']
    minutes = int(message.text)
    
    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("""
                SELECT u.telegram_id, i.name as item_name
                FROM preorders p 
                JOIN items i ON p.item_id = i.id
                JOIN users u ON p.user_id = u.id
                WHERE p.id = %s
            """, (pre_id,))
            row = await cursor.fetchone()
            
    if not row:
        await state.clear()
        return await message.answer("Precomanda a dispărut.")
        
    u_tg_id = row['telegram_id']
    i_name = row['item_name']
    try:
        user_msg = (
            f"🚀 <b>VEȘTI BUNE!</b>\n\n"
            f"Vânzătorul a confirmat și a început pregătirea pentru: <b>{i_name}</b>.\n\n"
            f"Produsul va fi în stoc în aproximativ <b>{minutes} minute</b>. Vei primi un mesaj imediat ce poți comanda!"
        )
        await message.bot.send_message(u_tg_id, user_msg)
        await message.answer(f"✅ Utilizatorul a fost anunțat: {minutes} min până la stoc.")
    except Exception as e:
        await message.answer(f"❌ Eroare trimitere mesaj: {e}")
        
    await state.clear()
    await cmd_admin(message)

@router.callback_query(F.data.startswith("adm_preo_action_"))
async def cb_admin_preo_final_action(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    parts = callback.data.split("_")
    action = parts[3] # ok or no
    preo_id = int(parts[4])
    
    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("""
                SELECT u.telegram_id, i.name as item_name, i.id as item_id
                FROM preorders p
                JOIN items i ON p.item_id = i.id
                JOIN users u ON p.user_id = u.id
                WHERE p.id = %s
            """, (preo_id,))
            row = await cursor.fetchone()
            
    if not row: return await callback.answer("Nu mai există.")
    user_tg_id = row['telegram_id']
    it_name = row['item_name']
    it_id = row['item_id']
    
    if action == "ok":
        async with db_session() as db:
            async with db.cursor() as cursor:
                # Try to find a secret to fulfill IMMEDIATELY
                await cursor.execute("""
                    SELECT id, image_url, media_type, secret_group, caption 
                    FROM item_images 
                    WHERE item_id = %s AND is_sold = FALSE 
                    ORDER BY RANDOM() LIMIT 1
                """, (it_id,))
                stock_row = await cursor.fetchone()
            
            if stock_row:
                img_db_id = stock_row['id']
                img_url = stock_row['image_url']
                m_type = stock_row['media_type']
                group_id = stock_row['secret_group']
                main_caption = stock_row['caption']
                
                # 1. Retrieve whole bundle if grouped
                if group_id:
                    await cursor.execute("SELECT id, image_url, media_type, caption FROM item_images WHERE secret_group = %s", (group_id,))
                    bundle_items = await cursor.fetchall()
                else:
                    bundle_items = [{'id': img_db_id, 'image_url': img_url, 'media_type': m_type, 'caption': main_caption}]

                # 2. Mark all as sold
                for b_r in bundle_items:
                    await cursor.execute("UPDATE item_images SET is_sold = TRUE WHERE id = %s", (b_r['id'],))
                
                # 3. Update Preorder
                await cursor.execute("UPDATE preorders SET status = 'accepted' WHERE id = %s", (preo_id,))
                await db.commit()

                # 4. Physical Delivery
                await callback.bot.send_message(user_tg_id, f"🎁 <b>LIVRARE PRECOMANDĂ!</b>\n\n🆔 ID Precomandă: <code>#{preo_id}</code>\nProdus: <b>{it_name}</b>\nSecretul tău:")
                
                for b_r in bundle_items:
                    b_url = b_r['image_url']
                    b_t = b_r['media_type']
                    b_c = b_r['caption']
                    try:
                        if b_t == 'photo':
                            await callback.bot.send_photo(user_tg_id, photo=b_url, caption=b_c)
                        elif b_t == 'video':
                            await callback.bot.send_video(user_tg_id, video=b_url, caption=b_c)
                        elif b_t == 'text':
                            await callback.bot.send_message(user_tg_id, f"📝 <b>Conținut:</b>\n\n<code>{b_url}</code>")
                        else:
                            await callback.bot.send_message(user_tg_id, f"<code>{b_url}</code>")
                    except: pass
                
                await callback.message.answer(f"✅ Precomandă #{preo_id} a fost EXPEDIATĂ din stoc!")
            else:
                # No stock, just update status
                await db.execute("UPDATE preorders SET status = 'accepted' WHERE id = %s", (preo_id,))
                await db.commit()
                try:
                    user_text = (
                        f"✅ <b>PRECOMANDĂ ACCEPTATĂ!</b>\n\n"
                        f"Precomanda ta pentru <b>{it_name}</b> (ID #{preo_id}) a fost acceptată.\n\n"
                        f"Stai pe aproape, vei primi imediat detaliile de plată când stocul e gata!"
                    )
                    await callback.bot.send_message(user_tg_id, user_text)
                except: pass
                await callback.message.answer(f"✅ Precomandă #{preo_id} acceptată (Stoc indisponibil momentan).")
    else:
        async with db_session() as db:
            await db.execute("DELETE FROM preorders WHERE id = %s", (preo_id,))
            await db.commit()
        try:
            user_text = (
                f"❌ <b>PRECOMANDĂ REFUZATĂ</b>\n\n"
                f"Ne pare rău, precomanda ta pentru <b>{it_name}</b> (ID #{preo_id}) a fost refuzată de admin.\n\n"
                f"Poți încerca din nou mai târziu."
            )
            await callback.bot.send_message(user_tg_id, user_text)
        except: pass
        await callback.message.answer(f"❌ Precomandă #{preo_id} ștearsă/refuzată.")

@router.message(Command("pending", prefix="!/"))
async def cmd_pending_orders(message: Message, auth_id: int = None):
    if not is_admin(auth_id if auth_id is not None else message.from_user.id):
        return
        
    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("""
                SELECT s.id, i.name as item_name, s.amount_expected, u.username, u.telegram_id, s.address_used, s.created_at, s.status
                FROM sales s
                JOIN items i ON s.item_id = i.id
                JOIN users u ON s.user_id = u.id
                WHERE s.status IN ('pending', 'confirming')
                ORDER BY s.created_at DESC
                LIMIT 3
            """)
            pending = await cursor.fetchall()
            
    if not pending:
        await message.answer("ℹ️ Nu există comenzi active (trackuite) momentan.")
        return
        
    for p in pending:
        status = p['status']
        emoji = "⏳" if status == 'pending' else "🔄" if status == 'confirming' else "❌"
        text = (
            f"{emoji} <b>ID #{p['id']}</b> | Status: <b>{status.upper()}</b>\n"
            f"🛍 Produs: {p['item_name']}\n"
            f"💰 Sumă: <code>{p['amount_expected']}</code> LTC\n"
            f"👤 Client: @{p['username'] or 'N/A'} (<code>{p['telegram_id']}</code>)\n"
            f"📍 Adresă: <code>{p['address_used']}</code>\n"
            f"🕒 Creată: {p['created_at']}"
        )
        kb = None
        if status != 'cancelled':
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Finalizează", callback_data=f"adm_appr_{p['id']}"),
                    InlineKeyboardButton(text="❌ Anulează", callback_data=f"adm_canc_{p['id']}")
                ]
            ])
        await message.answer(text, reply_markup=kb)


@router.message(Command("specialdrop", prefix="!/"))
async def cmd_toggle_special_drop(message: Message):
    """Toggle the visibility of the special item (Item ID 66)."""
    if not is_admin(message.from_user.id): return
    
    SPECIAL_ITEM_ID = 66 # Item ID for 🏇 S-isomer
    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("SELECT is_hidden FROM items WHERE id = %s", (SPECIAL_ITEM_ID,))
            row = await cursor.fetchone()
            if not row:
                return await message.answer("⚠️ EROARE: Item-ul special (66) nu a fost găsit în bază.")
        
        new_state = not row['is_hidden']
        await db.execute("UPDATE items SET is_hidden = %s WHERE id = %s", (new_state, SPECIAL_ITEM_ID))
        await db.commit()
    
    status_str = "🕵️ **DEZACTIVAT**" if new_state else "👀 **ACTIVAT**"
    await message.answer(f"🎁 <b>DROP SPECIAL:</b> {status_str} acum în categoria 🐎.")

@router.message(Command("setdropwallet", prefix="!/"))
async def cmd_set_special_wallet(message: Message):
    """Admin command to set the dedicated LTC address for item 66."""
    if not is_admin(message.from_user.id): return
    
    parts = message.text.split()
    if len(parts) < 2:
        return await message.answer("ℹ️ Utilizare: <code>/setdropwallet [adresa_ltc]</code>")
        
    new_addr = parts[1].strip()
    async with db_session() as db:
        await db.execute("UPDATE items SET dedicated_address = %s WHERE id = 66", (new_addr,))
        await db.commit()
        
    await message.answer(f"✅ **WALLET ACTUALIZAT!**\nAdresa pentru drop-ul S-isomer este acum:\n<code>{new_addr}</code>")

@router.message(Command("all", prefix="!/"))
async def cmd_all_broadcast(message: Message):
    if not is_admin(message.from_user.id):
        return
        
    broadcast_msg = message.text.replace("/all", "").replace("!all", "").strip()
    reply_msg = message.reply_to_message

    if not broadcast_msg and not reply_msg:
        await message.answer("ℹ️ Utilizare: <code>/all [mesaj]</code> sau dă reply la un mesaj (poate conține poze/video) cu comanda <code>/all</code>.")
        return

    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("SELECT telegram_id FROM users")
            users = await cursor.fetchall()
            
    if not users:
        await message.answer("⚠️ Nu există utilizatori în baza de date.")
        return

    await message.answer(f"📢 <b>Începe trimiterea către {len(users)} utilizatori...</b>")
    
    success_count = 0
    fail_count = 0
    
    for u in users:
        user_tg_id = u['telegram_id']
        try:
            if reply_msg:
                # Folosim copy_to pentru a păstra exact pozele, denumirile și formatarea originală
                await reply_msg.copy_to(user_tg_id)
            else:
                await message.bot.send_message(user_tg_id, broadcast_msg)
            success_count += 1
            await asyncio.sleep(0.05) # Prevent flood limit
        except Exception as e:
            # e.g., user blocked the bot
            fail_count += 1
            
    await message.answer(f"✅ <b>Broadcast Finalizat!</b>\nTrimise cu succes: {success_count}\nEșuate: {fail_count} (Utilizatori care au blocat botul)")

@router.message(Command("info", prefix="!/"))
async def cmd_admin_info(message: Message):
    if not is_admin(message.from_user.id):
        return

    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("SELECT COUNT(*) as total FROM users")
            users_total = (await cursor.fetchone())['total']
            
            await cursor.execute("SELECT COUNT(*) as total FROM users WHERE joined_at >= CURRENT_TIMESTAMP - INTERVAL '7 days'")
            users_7d = (await cursor.fetchone())['total']

            await cursor.execute("SELECT COUNT(*) as total, SUM(amount_paid) as vol FROM sales WHERE status IN ('paid', 'confirming', 'completed', 'delivered')")
            row_total = await cursor.fetchone()
            sales_total = row_total['total']
            vol_total = float(row_total['vol'] or 0.0)

            await cursor.execute("SELECT COUNT(*) as total, SUM(amount_paid) as vol FROM sales WHERE status IN ('paid', 'confirming', 'completed', 'delivered') AND created_at >= CURRENT_TIMESTAMP - INTERVAL '7 days'")
            row_7d = await cursor.fetchone()
            sales_7d = row_7d['total']
            vol_7d = float(row_7d['vol'] or 0.0)

            await cursor.execute("SELECT COUNT(*) as total FROM sales WHERE status IN ('expired', 'cancelled', 'failed')")
            sales_failed = (await cursor.fetchone())['total']

            await cursor.execute("SELECT COUNT(*) as total FROM categories")
            cats_total = (await cursor.fetchone())['total']
            
            await cursor.execute("SELECT COUNT(*) as total FROM items")
            items_total = (await cursor.fetchone())['total']
            
            await cursor.execute("""
                SELECT 
                    (SELECT COUNT(DISTINCT secret_group) FROM item_images WHERE is_sold = FALSE AND secret_group IS NOT NULL) +
                    (SELECT COUNT(*) FROM item_images WHERE is_sold = FALSE AND secret_group IS NULL) as stock_active
            """)
            stock_active = (await cursor.fetchone())['stock_active'] or 0

            await cursor.execute("""
                SELECT COUNT(*) as total FROM sales 
                WHERE tx_hash IS NOT NULL AND status IN ('paid', 'confirming', 'completed', 'delivered')
            """)
            stock_sold = (await cursor.fetchone())['total'] or 0

    text = (
        f"📊 <b>STATISTICI GENERALE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 <b>Utilizatori:</b>\n"
        f"   • Total: <code>{users_total}</code>\n"
        f"   • Noi (ult. 7 zile): <code>+{users_7d}</code>\n\n"
        
        f"💰 <b>Vânzări:</b>\n"
        f"   • Reușite Total: <code>{sales_total}</code> (Volum: <code>{vol_total:.4f} LTC</code>)\n"
        f"   • Reușite (7 zile): <code>{sales_7d}</code> (Volum: <code>{vol_7d:.4f} LTC</code>)\n"
        f"   • Expirate/Anulate: <code>{sales_failed}</code>\n\n"
        
        f"📦 <b>Inventar:</b>\n"
        f"   • Categorii: <code>{cats_total}</code>\n"
        f"   • Produse (Tipuri): <code>{items_total}</code>\n"
        f"   • Pachete în Stoc: <code>{stock_active}</code>\n"
        f"   • Pachete Vândute: <code>{stock_sold}</code>\n"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📈 Vânzări (Cat.)", callback_data="adm_stats_sales_0"),
            InlineKeyboardButton(text="👥 Top Cumpărători", callback_data="adm_stats_top_0")
        ],
        [
            InlineKeyboardButton(text="👥 Utilizatori", callback_data="adm_stats_users_0"),
            InlineKeyboardButton(text="📦 Stoc Detaliat", callback_data="adm_stats_stock_0")
        ],
        [
            InlineKeyboardButton(text="🆕 Ultimele Achiziții", callback_data="adm_stats_latest_0")
        ]
    ])
    try:
        await message.answer(text, reply_markup=kb)
    except Exception as e:
        await message.answer(f"Eroare: {e}")

@router.callback_query(F.data == "adm_stats_info")
async def cb_admin_stats_info(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    await cmd_admin_info(callback.message, auth_id=callback.from_user.id)
    await callback.answer()

@router.callback_query(F.data == "adm_pending_link")
async def cb_admin_pending_link(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    await cmd_pending_orders(callback.message, auth_id=callback.from_user.id)
    await callback.answer()

@router.callback_query(F.data == "adm_addresses_link")
async def cb_admin_addresses_link(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    await cmd_check_slots(callback.message, auth_id=callback.from_user.id)
    await callback.answer()

@router.callback_query(F.data == "admin_cats")
async def cb_legacy_admin_cats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    await cb_admin_cats(callback)

@router.callback_query(F.data == "admin_rem_cat")
async def cb_legacy_admin_rem_cat(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    await callback.answer("Pentru a șterge, selectează Categoria -> Șterge Categoria.", show_alert=True)
    await cb_admin_cats(callback)

@router.callback_query(F.data == "admin_items")
async def cb_legacy_admin_items(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    await callback.answer("Selectează o Categorie pentru a adăuga sau vedea Produsele ei.", show_alert=True)
    await cb_admin_cats(callback)

@router.callback_query(F.data == "admin_rem_item")
async def cb_legacy_admin_rem_item(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    await callback.answer("Pentru a șterge, intră la Categorie -> Produs -> Șterge.", show_alert=True)
    await cb_admin_cats(callback)

@router.callback_query(F.data == "admin_stock")
async def cb_legacy_admin_stock(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    await callback.answer("Pentru a adăuga stoc, intră la Categorie -> Produs -> Adaugă Stoc.", show_alert=True)
    await cb_admin_cats(callback)

@router.callback_query(F.data == "admin_rem_stock")
async def cb_legacy_admin_rem_stock(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    await callback.answer("Utilizează Funcția 'Șterge' de la produs pentru a-l goli.", show_alert=True)

        
@router.callback_query(F.data == "admin_history")
async def cb_admin_history_bridge(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    await cb_admin_stats_info(callback)

@router.callback_query(F.data.startswith("adm_stats_"))
async def cb_admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
        
    parts = callback.data.split("_")
    action = parts[2]
    page = int(parts[3]) if len(parts) > 3 else 0
    limit = 10
    offset = page * limit
    
    if action == "sales":
        async with db_session() as db:
            async with db.cursor() as cursor:
                await cursor.execute("""
                    SELECT c.name as category_name, COUNT(s.id) as sale_count, SUM(s.amount_paid) as total_vol
                    FROM sales s
                    JOIN items i ON s.item_id = i.id
                    JOIN categories c ON i.category_id = c.id
                    WHERE s.tx_hash IS NOT NULL AND s.status IN ('paid', 'confirming', 'completed', 'delivered')
                    GROUP BY c.id, c.name
                    ORDER BY SUM(s.amount_paid) DESC
                    LIMIT %s OFFSET %s
                """, (limit + 1, offset))
                rows = await cursor.fetchall()
        
        has_next = len(rows) > limit
        rows = rows[:limit]
        
        text = f"📈 <b>VÂNZĂRI PE CATEGORII (Pagina {page+1}):</b>\n\n"
        if not rows: text += "Fără date."
        for r in rows: text += f"• <b>{r['category_name']}</b>: {r['sale_count']} pachete (<code>{float(r['total_vol']):.4f} LTC</code>)\n"
        
    elif action == "top":
        async with db_session() as db:
            async with db.cursor() as cursor:
                await cursor.execute("""
                    SELECT u.telegram_id, u.username, COUNT(s.id) as sale_count, SUM(s.amount_paid) as total_vol
                    FROM sales s
                    JOIN users u ON s.user_id = u.id
                    WHERE s.tx_hash IS NOT NULL AND s.status IN ('paid', 'confirming', 'completed', 'delivered')
                    GROUP BY u.id, u.telegram_id, u.username
                    ORDER BY SUM(s.amount_paid) DESC
                    LIMIT %s OFFSET %s
                """, (limit + 1, offset))
                rows = await cursor.fetchall()
                
        has_next = len(rows) > limit
        rows = rows[:limit]
        
        text = f"👥 <b>TOP CUMPĂRĂTORI (Pagina {page+1}):</b>\n\n"
        if not rows: text += "Fără date."
        for idx, r in enumerate(rows, 1): 
            username = f"@{r['username']}" if r['username'] else str(r['telegram_id'])
            text += f"{offset + idx}. {username} - {r['sale_count']} comenzi, <code>{float(r['total_vol']):.4f} LTC</code>\n"

    elif action == "users":
        async with db_session() as db:
            async with db.cursor() as cursor:
                await cursor.execute("""
                    SELECT telegram_id, username, joined_at
                    FROM users 
                    ORDER BY joined_at DESC
                    LIMIT %s OFFSET %s
                """, (limit + 1, offset))
                rows = await cursor.fetchall()
                
        has_next = len(rows) > limit
        rows = rows[:limit]
        
        text = f"👥 <b>ULTIMII UTILIZATORI ÎNREGISTRAȚI (Pagina {page+1}):</b>\n\n"
        if not rows: text += "Fără date."
        for r in rows: 
            username = f"@{r['username']}" if r['username'] else str(r['telegram_id'])
            joined_at = r['joined_at']
            date_str = joined_at if isinstance(joined_at, str) else joined_at.strftime('%Y-%m-%d %H:%M')
            text += f"• {username} | Alăturat: {date_str[:16]}\n"
            
    elif action == "stock":
        async with db_session() as db:
            async with db.cursor() as cursor:
                await cursor.execute("""
                    SELECT c.name as category_name, 
                        (SELECT COUNT(DISTINCT secret_group) FROM item_images im JOIN items it ON im.item_id=it.id WHERE it.category_id=c.id AND im.is_sold = FALSE AND im.secret_group IS NOT NULL) +
                        (SELECT COUNT(*) FROM item_images im JOIN items it ON im.item_id=it.id WHERE it.category_id=c.id AND im.is_sold = FALSE AND im.secret_group IS NULL) as stock_total
                    FROM categories c
                    LIMIT %s OFFSET %s
                """, (limit + 1, offset))
                rows = await cursor.fetchall()
                
        has_next = len(rows) > limit
        rows = rows[:limit]
                
        text = f"📦 <b>STOC DISPONIBIL PE CATEGORII (Pagina {page+1}):</b>\n\n"
        if not rows: text += "Fără date."
        for r in rows: text += f"• <b>{r['category_name']}</b>: <code>{r['stock_total']}</code> pachete\n"

    elif action == "latest":
        limit = 5
        offset = page * limit
        async with db_session() as db:
            async with db.cursor() as cursor:
                await cursor.execute("""
                    SELECT s.id, s.amount_paid, s.tx_hash, u.telegram_id, u.username, i.name
                    FROM sales s
                    JOIN users u ON s.user_id = u.id
                    JOIN items i ON s.item_id = i.id
                    WHERE s.tx_hash IS NOT NULL AND s.status IN ('paid', 'confirming', 'completed', 'delivered')
                    ORDER BY s.created_at DESC
                    LIMIT %s OFFSET %s
                """, (limit + 1, offset))
                rows = await cursor.fetchall()
                
        has_next = len(rows) > limit
        rows = rows[:limit]
        
        text = f"🆕 <b>ULTIMELE ACHIZIȚII (Pagina {page+1}):</b>\n\n"
        if not rows: text += "Fără date."
        for r in rows:
            username = f"@{r['username']}" if r['username'] else str(r['telegram_id'])
            t_hash = r['tx_hash']
            tx_link = f"<a href='https://blockchair.com/litecoin/transaction/{t_hash}'>{t_hash[:12]}...</a>"
            text += f"🛍 <b>{r['name']}</b>\n👤 Client: {username} | 💰 {float(r['amount_paid']):.4f} LTC\n🔗 {tx_link}\n\n"
            
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Înapoi", callback_data=f"adm_stats_{action}_{page-1}"))
    if has_next:
        nav_row.append(InlineKeyboardButton(text="Înainte ➡️", callback_data=f"adm_stats_{action}_{page+1}"))
        
    kb_rows = [nav_row] if nav_row else []
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    
    try:
        await smart_edit(callback, text, reply_markup=kb, disable_web_page_preview=True)
    except:
        pass
    await callback.answer()

@router.message(Command("latest", prefix="!/"))
async def cmd_latest_sales(message: Message):
    if not is_admin(message.from_user.id):
        return
        
    parts = message.text.split()
    limit = 5
    if len(parts) > 1 and parts[1].isdigit():
        limit = int(parts[1])
        if limit > 20: limit = 20
        
    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("""
                SELECT s.amount_paid, s.tx_hash, u.telegram_id, u.username, i.name
                FROM sales s
                JOIN users u ON s.user_id = u.id
                JOIN items i ON s.item_id = i.id
                WHERE s.tx_hash IS NOT NULL AND s.status IN ('paid', 'confirming', 'completed', 'delivered')
                ORDER BY s.created_at DESC
                LIMIT %s
            """, (limit,))
            rows = await cursor.fetchall()
 
    text = f"🆕 <b>ULTIMELE {len(rows)} ACHIZIȚII:</b>\n\n"
    if not rows: text += "Fără date."
    for r in rows:
        username = f"@{r['username']}" if r['username'] else str(r['telegram_id'])
        t_hash = r['tx_hash']
        tx_link = f"<a href='https://blockchair.com/litecoin/transaction/{t_hash}'>{t_hash[:12]}...</a>"
        text += f"🛍 <b>{r['name']}</b>\n👤 Client: {username} | 💰 {float(r['amount_paid']):.4f} LTC\n🔗 {tx_link}\n\n"
        
    await message.answer(text, disable_web_page_preview=True)

@router.message(Command("restart", prefix="!/"))
async def cmd_restart_bot(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("🔄 <b>Bot-ul se repornește...</b>")
    await cmd_start(message)
    import os, sys
    os.execv(sys.executable, ['python'] + sys.argv)

@router.message(Command("unfreeze", prefix="!/"))
async def cmd_unfreeze_address(message: Message):
    if not is_admin(message.from_user.id):
        return
        
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("ℹ️ Utilizare: <code>/unfreeze [ADRESA] [TX_HASH_OPTIONAL] [SUMA_OPTIONAL]</code>")
        return
        
    address = parts[1]
    
    if address.lower() == "all":
        async with db_session() as db:
            await db.execute("UPDATE addresses SET in_use_by_sale_id = NULL, locked_until = NULL")
            await db.commit()
        await message.answer("✅ <b>Toate adresele au fost DEBLOCATE.</b>")
        return

    last_tx = parts[2] if len(parts) > 2 else None
    last_amount = None
    if len(parts) > 3:
        try: last_amount = float(parts[3])
        except: pass
    
    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("SELECT id FROM addresses WHERE crypto_address = %s", (address,))
            if not await cursor.fetchone():
                await message.answer(f"❌ Adresa <code>{address}</code> nu a fost găsită.")
                return
        
        await db.execute("""
            UPDATE addresses 
            SET in_use_by_sale_id = NULL, locked_until = NULL, last_tx_hash = %s, last_amount = %s
            WHERE crypto_address = %s
        """, (last_tx, last_amount, address))
        await db.commit()
        
    msg = f"✅ Adresa <code>{address}</code> deblocată."
    if last_tx: msg += f"\nArzi TX: <code>{last_tx[:10]}...</code>"
    await message.answer(msg)

@router.callback_query(F.data.startswith("adm_view_s_"))
async def cb_view_secret_content(callback: CallbackQuery):
    s_id = callback.data.split("_")[3]
    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("SELECT image_url, media_type, caption FROM item_images WHERE secret_group = %s", (s_id,))
            items = await cursor.fetchall()
    
    if not items:
        return await callback.answer("Secretul nu mai există.", show_alert=True)
        
    await callback.message.answer(f"📦 <b>Conținut Pachet:</b> <code>{s_id}</code>")
    for r in items:
        val, mt, capt = r['image_url'], r['media_type'], r['caption']
        try:
            if mt == 'photo': await callback.message.answer_photo(val, caption=capt)
            elif mt == 'video': await callback.message.answer_video(val, caption=capt)
            else: await callback.message.answer(f"📝 {val}\n\n<i>Note: {capt or ''}</i>")
        except: pass
    await callback.answer()

@router.callback_query(F.data.startswith("adm_del_s_"))
async def cb_del_secret(callback: CallbackQuery):
    s_id = callback.data.split("_")[3]
    async with db_session() as db:
        await db.execute("DELETE FROM item_images WHERE secret_group = %s", (s_id,))
        await db.commit()
    await smart_edit(callback, f"✅ Pachetul <code>{s_id}</code> a fost șters.")
    await callback.answer("Pachet șters!", show_alert=True)

@router.callback_query(F.data.startswith("adm_view_r_"))
async def cb_view_single_secret(callback: CallbackQuery):
    s_id = callback.data.split("_")[3]
    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("SELECT image_url, media_type, caption FROM item_images WHERE secret_group = %s", (s_id,))
            rows = await cursor.fetchall()
            
    if not rows: return await callback.answer("Nu există conținut.")
    
    for r in rows:
        val, mt, capt = r['image_url'], r['media_type'], r['caption']
        try:
            if mt == 'photo': await callback.message.answer_photo(val, caption=capt)
            elif mt == 'video': await callback.message.answer_video(val, caption=capt)
            else: await callback.message.answer(f"📝 {val}\n\n<i>Note: {capt or ''}</i>")
        except: pass
    await callback.answer()

    kb_rows = []
    nav_btns = []
    if page > 0: nav_btns.append(InlineKeyboardButton(text="⬅️", callback_data=f"adm_stats_{action}_{page-1}"))
    if has_next: nav_btns.append(InlineKeyboardButton(text="➡️", callback_data=f"adm_stats_{action}_{page+1}"))
    if nav_btns: kb_rows.append(nav_btns)
    kb_rows.append([InlineKeyboardButton(text="🔙 Înapoi la Info", callback_data="adm_stats_info")])
    
    await smart_edit(callback.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await callback.answer()

# NOTE: adm_stats_info handler is defined at line 767 (correct version with from_user spoof)

# ===== APPROVAL / REJECTION =====

@router.callback_query(F.data.startswith("adm_appr_"))
async def cb_admin_approve_payment(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    sale_id = int(callback.data.split("_")[2])
    
    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("""
                SELECT s.id, i.name as item_name, s.amount_expected, s.address_used, u.telegram_id, i.id as item_id, s.status, s.tx_hash, s.amount_paid
                FROM sales s 
                JOIN items i ON s.item_id = i.id
                JOIN users u ON s.user_id = u.id
                WHERE s.id = %s
            """, (sale_id,))
            sale_data = await cursor.fetchone()
            
    if not sale_data:
        return await callback.answer("❌ Comanda nu a fost găsită.", show_alert=True)
        
    if sale_data['status'] == 'paid':
        return await callback.answer("✅ Deja livrată.", show_alert=True)

    # 1. Delivery logic
    item_id = sale_data['item_id']
    user_tg_id = sale_data['telegram_id']
    item_name = sale_data['item_name']
    tx_hash = sale_data['tx_hash'] or f"MANUAL_{uuid.uuid4().hex[:8]}"
    paid_amount = sale_data['amount_paid'] or sale_data['amount_expected']
    address = sale_data['address_used']
    
    async with db_session() as db:
        async with db.cursor() as cursor:
            try:
                # Select available item
                await cursor.execute("""
                    SELECT id, image_url, media_type, secret_group, caption 
                    FROM item_images 
                    WHERE item_id = %s AND is_sold = FALSE 
                    LIMIT 1
                """, (item_id,))
                image_row = await cursor.fetchone()
                
                if not image_row:
                    await callback.answer("❌ EROARE: Stocul s-a epuizat între timp!", show_alert=True)
                    return
                    
                img_db_id = image_row['id']
                group_id = image_row['secret_group']
                
                if group_id:
                    await cursor.execute("SELECT id, image_url, media_type, caption FROM item_images WHERE secret_group = %s", (group_id,))
                else:
                    await cursor.execute("SELECT id, image_url, media_type, caption FROM item_images WHERE id = %s", (img_db_id,))
                bundle_items = await cursor.fetchall()

                for b_r in bundle_items:
                    await cursor.execute("UPDATE item_images SET is_sold = TRUE WHERE id = %s", (b_r['id'],))
                
                now = datetime.now()
                cooldown_str = now + timedelta(minutes=3)
                await cursor.execute("UPDATE sales SET status = 'paid', amount_paid = %s, image_id = %s, tx_hash = %s, completed_at = %s WHERE id = %s", (float(paid_amount), img_db_id, tx_hash, now, sale_id))
                await cursor.execute("""
                    UPDATE addresses SET in_use_by_sale_id = NULL, locked_until = %s, last_tx_hash = %s 
                    WHERE crypto_address = %s
                """, (cooldown_str, tx_hash, address))
                
                await db.commit()
            except Exception as e:
                await db.rollback()
                logging.error(f"Approval DB error: {e}")
                return await callback.answer("❌ Eroare baza de date.", show_alert=True)

    await callback.bot.send_message(user_tg_id, f"✅ <b>PLATA A FOST APROBATĂ!</b>\n\n🆔 ID Comandă: <code>#{sale_id}</code>\nProdus: <b>{item_name}</b>\n\nSecretul tău a fost eliberat:")
    
    black_magic = await is_blackmagic_on()
    for b_r in bundle_items:
        b_id = b_r['id']
        b_url = b_r['image_url']
        b_type = b_r['media_type']
        b_capt = b_r['caption']
        try:
            delivery_file = b_url
            if black_magic:
                from aiogram.types import BufferedInputFile
                from utils.blackmagic import generate_black_magic_image
                if b_type == 'photo':
                    delivery_file = BufferedInputFile(generate_black_magic_image(f"ID_{b_id}").read(), filename=f"black_magic_{b_id}.jpg")
                elif b_type == 'video':
                    delivery_file = BufferedInputFile(generate_black_magic_image(f"ID_{b_id}").read(), filename=f"black_magic_v_{b_id}.jpg")
                    b_type = 'photo'

            if b_type == 'photo':
                await callback.bot.send_photo(user_tg_id, photo=delivery_file, caption=b_capt)
            elif b_type == 'video':
                await callback.bot.send_video(user_tg_id, video=delivery_file, caption=b_capt)
            elif b_type == 'text':
                await callback.bot.send_message(user_tg_id, f"📝 <b>Conținut:</b>\n\n<code>{b_url}</code>")
            else:
                await callback.bot.send_message(user_tg_id, f"<code>{b_url}</code>")
        except: pass
        
    await callback.answer("✅ Aprobat și livrat!", show_alert=True)
    success_msg = f"✅ Comanda #{sale_id} a fost finalizată și livrată!"
    if callback.message.photo:
        await callback.message.edit_caption(caption=success_msg)
    else:
        await smart_edit(callback, text=success_msg)
    
    if sale_id in admin_intention_messages:
        for a_id, m_id, original_text in admin_intention_messages[sale_id]:
            try:
                new_text = original_text.replace("📝 <b>INTENȚIE CUMPĂRARE</b>", f"✅ <b>APROBATĂ MANUAL de @{callback.from_user.username}</b>")
                await callback.bot.edit_message_text(new_text, chat_id=a_id, message_id=m_id)
            except: pass
        del admin_intention_messages[sale_id]

@router.callback_query(F.data.startswith("adm_canc_"))
async def cb_admin_cancel_sale(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    sale_id = int(callback.data.split("_")[2])
    
    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("SELECT address_used, user_id FROM sales WHERE id = %s", (sale_id,))
            row = await cursor.fetchone()
            if not row: return await callback.answer("Nu există.")
            
            addr = row['address_used']
            u_id = row['user_id']
            await cursor.execute("SELECT telegram_id FROM users WHERE id = %s", (u_id,))
            u_tg_id = (await cursor.fetchone())['telegram_id']

            await cursor.execute("UPDATE sales SET status = 'cancelled' WHERE id = %s", (sale_id,))
            await cursor.execute("UPDATE addresses SET in_use_by_sale_id = NULL, locked_until = NULL WHERE crypto_address = %s", (addr,))
            await db.commit()
    
    try:
        await callback.bot.send_message(u_tg_id, f"❌ <b>PLATĂ REFUZATĂ</b>\n\nComanda #{sale_id} a fost refuzată de admin. Contactează @creierosuz dacă crezi că e o greșeală.")
    except: pass
    
    await callback.answer("❌ Comandă refuzată.", show_alert=True)
    cancel_label = f"❌ Comanda #{sale_id} a fost anulată de Admin."
    if callback.message.photo:
        await callback.message.edit_caption(caption=cancel_label)
    else:
        await smart_edit(callback, text=cancel_label)
    
    if sale_id in admin_intention_messages:
        for a_id, m_id, original_text in admin_intention_messages[sale_id]:
            try:
                new_text = original_text.replace("📝 <b>INTENȚIE CUMPĂRARE</b>", f"❌ <b>REFUZATĂ MANUAL de @{callback.from_user.username}</b>")
                await callback.bot.edit_message_text(new_text, chat_id=a_id, message_id=m_id)
            except: pass
        del admin_intention_messages[sale_id]

@router.callback_query(F.data.startswith("pre_"))
async def cb_preorder_decision(callback: CallbackQuery):
    parts = callback.data.split("_")
    decision = parts[1] # yes/no
    user_tg_id = int(parts[2])
    item_id = int(parts[3])
    
    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("SELECT name FROM items WHERE id = %s", (item_id,))
            item = await cursor.fetchone()
            item_name = item['name'] if item else "produsul selectat"
    
    if decision == "yes":
        msg_to_user = (
            f"✅ <b>Precomandă Aprobată!</b>\n\n"
            f"Pentru <b>{item_name}</b>.\n\n"
            f"Vânzătorul a verificat și a confirmat că acest produs este valabil. Acum poți merge la profil și să finalizezi comanda!"
        )
        try:
            await callback.bot.send_message(user_tg_id, msg_to_user)
            await callback.answer("Aprobat și trimis utilizatorului.")
            await smart_edit(callback, f"✅ Ai aprobat precomanda pentru {item_name}.")
        except:
            await callback.answer("Eroare la trimiterea mesajului.", show_alert=True)
    else:
        msg_to_user = (
            f"❌ <b>Precomandă Respinsă</b>\n\n"
            f"Ne pare rău, dar precomanda pentru <b>{item_name}</b> nu a putut fi aprobată momentan."
        )
        try:
            await callback.bot.send_message(user_tg_id, msg_to_user)
            await callback.answer("Respins și notificat.")
            await smart_edit(callback, f"❌ Ai respins precomanda pentru {item_name}.")
        except:
            await callback.answer("Eroare la trimiterea mesajului.", show_alert=True)

# --- CATEGORY MANAGEMENT ---

@router.callback_query(F.data == "adm_cats")
async def cb_admin_cats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("SELECT id, name FROM categories ORDER BY id ASC")
            cats = await cursor.fetchall()
            
    text = "� <b>GESTIONARE CATEGORII</b>"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        *[[InlineKeyboardButton(text=f"� {c['name']}", callback_data=f"adm_cat_view_{c['id']}")] for c in cats],
        [InlineKeyboardButton(text="➕ Adaugă", callback_data="adm_cat_add")],
        [InlineKeyboardButton(text="🔙 Înapoi", callback_data="admin_main")]
    ])
    await smart_edit(callback, text, reply_markup=kb)

@router.callback_query(F.data.startswith("adm_cat_view_"))
async def cb_admin_cat_view(callback: CallbackQuery):
    cat_id = int(callback.data.split("_")[3])
    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("SELECT name FROM categories WHERE id = %s", (cat_id,))
            cat = await cursor.fetchone()
    if not cat: return await callback.answer("Nu există.")
    text = f"📂 <b>CATEGORIE: {cat['name']}</b>"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="� Vezi Produse", callback_data=f"adm_items_{cat_id}_0")],
        [InlineKeyboardButton(text="🗑️ Șterge Categoria", callback_data=f"adm_cat_del_{cat_id}")],
        [InlineKeyboardButton(text="🔙 Înapoi", callback_data="adm_cats")]
    ])
    await smart_edit(callback, text, reply_markup=kb)

# --- ITEM MANAGEMENT ---

@router.callback_query(F.data.startswith("adm_items_"))
async def cb_admin_items(callback: CallbackQuery):
    parts = callback.data.split("_")
    cat_id, page = int(parts[2]), int(parts[3])
    limit, offset = 10, page * 10
    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("SELECT id, name, price_ron FROM items WHERE category_id = %s ORDER BY id ASC LIMIT %s OFFSET %s", (cat_id, limit+1, offset))
            items = await cursor.fetchall()
            
    has_next = len(items) > limit
    items = items[:limit]
    text = f"📦 <b>Produse (Pag {page+1})</b>"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        *[[InlineKeyboardButton(text=f"{it['name']} ({it['price_ron']} RON)", callback_data=f"adm_item_view_{it['id']}")] for it in items],
        [InlineKeyboardButton(text="➕ Adaugă Produs", callback_data=f"adm_item_add_{cat_id}")],
        [InlineKeyboardButton(text="🔙 Înapoi", callback_data=f"adm_cat_view_{cat_id}")]
    ])
    if page > 0 or has_next:
        nav = []
        if page > 0: nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"adm_items_{cat_id}_{page-1}"))
        if has_next: nav.append(InlineKeyboardButton(text="➡️", callback_data=f"adm_items_{cat_id}_{page+1}"))
        kb.inline_keyboard.insert(-2, nav)
    await smart_edit(callback, text, reply_markup=kb)

@router.callback_query(F.data.startswith("adm_item_view_"))
async def cb_admin_item_view(callback: CallbackQuery):
    item_id = int(callback.data.split("_")[3])
    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("SELECT i.*, c.name as category_name FROM items i JOIN categories c ON i.category_id = c.id WHERE i.id = %s", (item_id,))
            it = await cursor.fetchone()
            await cursor.execute("SELECT COUNT(DISTINCT secret_group) as grp, COUNT(*) FILTER (WHERE secret_group IS NULL) as sgl FROM item_images WHERE item_id = %s AND is_sold = FALSE", (item_id,))
            stk = await cursor.fetchone()
            stock_count = (stk['grp'] or 0) + (stk['sgl'] or 0)
            
    if not it: return await callback.answer("Nu există.")
    text = f"📦 <b>{it['name']}</b>\nPreț: {it['price_ron']} RON\nStoc: {stock_count} pachete\nStatus: {'🕵️ Ascuns' if it['is_hidden'] else '👀 Vizibil'}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Adaugă Stoc", callback_data=f"adm_stock_add_{item_id}")],
        [InlineKeyboardButton(text="🗑️ Șterge", callback_data=f"adm_item_del_{item_id}")],
        [InlineKeyboardButton(text="🔙 Înapoi", callback_data=f"adm_items_{it['category_id']}_0")]
    ])
    await smart_edit(callback, text, reply_markup=kb)

@router.callback_query(F.data.startswith("adm_item_del_"))
async def cb_admin_item_del(callback: CallbackQuery):
    item_id = int(callback.data.split("_")[3])
    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("SELECT category_id FROM items WHERE id = %s", (item_id,))
            row = await cursor.fetchone()
            await db.execute("DELETE FROM items WHERE id = %s", (item_id,))
            await db.commit()
    await callback.answer("Șters.")
    if row:
        await cb_admin_cat_view(callback.model_copy(update={'data': f"adm_cat_view_{row['category_id']}"}))

# --- STOCK ---
@router.callback_query(F.data.startswith("adm_stock_add_"))
async def cb_item_stock_add(callback: CallbackQuery, state: FSMContext):
    item_id = int(callback.data.split("_")[3])
    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("SELECT name FROM items WHERE id = %s", (item_id,))
            row = await cursor.fetchone()
    if not row: return await callback.answer("Eroare.")
    await state.update_data(item_id=item_id, item_name=row['name'], bundle_id=str(uuid.uuid4())[:8], bundle_count=0)
    await state.set_state(AdminStock.waiting_for_bundle)
    await callback.message.answer(f"📦 <b>Stoc: {row['name']}</b>\nTrimite fișierele, apoi GATA:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ GATA", callback_data="admin_stock_finish")]]))
    await callback.answer()

@router.message(AdminStock.waiting_for_bundle)
async def process_stock_bundle(message: Message, state: FSMContext):
    media_type = 'photo' if message.photo else ('video' if message.video else 'text')
    val = message.photo[-1].file_id if message.photo else (message.video.file_id if message.video else message.text)
    if not val: return
    data = await state.get_data()
    async with db_session() as db:
        await db.execute("INSERT INTO item_images (item_id, image_url, media_type, caption, secret_group) VALUES (%s, %s, %s, %s, %s)", (data['item_id'], val, media_type, message.caption, data['bundle_id']))
        await db.commit()
    await state.update_data(bundle_count=data['bundle_count']+1)
    if not message.media_group_id:
        await message.answer(f"✅ Adăugat. Mai trimiți?", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ GATA", callback_data="admin_stock_finish")]]))

@router.callback_query(F.data == "admin_stock_finish")
async def cb_admin_stock_finish(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if data['bundle_count'] == 0: return await callback.answer("Goli!", show_alert=True)
    await callback.message.answer(f"✅ Salvat ({data['bundle_count']} el) pentru {data['item_name']}.", reply_markup=admin_main_menu())
    await state.clear()
    await callback.answer()

# --- ADDRESS SLOTS ---

@router.callback_query(F.data.startswith("edit_slot_"))
async def cb_edit_slot(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return
    slot_id = int(callback.data.split("_")[2])
    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("SELECT crypto_address FROM addresses WHERE id = %s", (slot_id,))
            row = await cursor.fetchone()
    if not row:
        return await callback.answer("Slot inexistent.", show_alert=True)
    await state.update_data(edit_slot_id=slot_id)
    await state.set_state(AdminAddress.waiting_for_address)
    await callback.message.answer(
        f"✏️ <b>Setare Slot #{slot_id}</b>\n"
        f"Curent: <code>{row['crypto_address']}</code>\n\n"
        f"Trimite noua adresă LTC (sau /cancel pentru anulare):"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("reset_slot_"))
async def cb_reset_slot(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    slot_id = int(callback.data.split("_")[2])
    async with db_session() as db:
        await db.execute(
            "UPDATE addresses SET crypto_address = %s, in_use_by_sale_id = NULL, locked_until = NULL WHERE id = %s",
            (f"UNSET_SLOT_{slot_id}", slot_id)
        )
        await db.commit()
    await callback.answer(f"✅ Slot #{slot_id} resetat!", show_alert=True)
    # Refresh addresses menu in-place
    await show_addresses_menu(callback.message)

@router.message(AdminAddress.waiting_for_address)
async def process_new_address(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    data = await state.get_data()
    new_addr = message.text.strip()
    if not new_addr or len(new_addr) < 26:
        return await message.answer("⚠️ Adresă invalidă. Trimite o adresă LTC validă.")
    async with db_session() as db:
        await db.execute(
            "UPDATE addresses SET crypto_address = %s, in_use_by_sale_id = NULL, locked_until = NULL WHERE id = %s",
            (new_addr, data['edit_slot_id'])
        )
        await db.commit()
    await state.clear()
    await message.answer(f"✅ Slot #{data['edit_slot_id']} actualizat la:\n<code>{new_addr}</code>")
    await show_addresses_menu(message)

# --- SUPPORT TICKETS ---

@router.callback_query(F.data.startswith("adm_reply_sup_"))
async def cb_admin_reply_ticket(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    await state.update_data(target_user_id=int(parts[3]), support_sale_id=int(parts[4]))
    await state.set_state(AdminReplyState.waiting_for_reply)
    await callback.message.answer(f"💬 Introdu răspunsul pentru client:")
    await callback.answer()

@router.message(AdminReplyState.waiting_for_reply)
async def process_admin_support_reply(message: Message, state: FSMContext):
    data = await state.get_data()
    try:
        await message.bot.send_message(data['target_user_id'], f"📩 <b>RĂSPUNS MAG (Comanda #{data['support_sale_id']})</b>\n\n{message.text}")
        await message.answer("✅ Trimis!")
    except: await message.answer("❌ Eroare trimitere.")
    await state.clear()
