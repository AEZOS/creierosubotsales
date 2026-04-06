import asyncio
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile, FSInputFile, InputMediaPhoto
from utils.keyboards import admin_main_menu
from config import ADMIN_IDS
from aiogram.fsm.context import FSMContext
from handlers.states import AdminCategory, AdminItem, AdminStock, AdminRemoval, AdminAddress, AdminPreorder, AdminReplyState
from database import DB_PATH, is_silent_mode, set_silent_mode, get_last_completed_sales, restore_secret_and_delete_sale, get_item_stats, get_user_total_sales, is_blackmagic_on, set_blackmagic
from utils.image_cleaner import strip_exif
from utils.ui import smart_edit
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

# smart_edit removed, now using utils.ui.smart_edit

@router.message(Command("check"))
async def cmd_check_slots(message: Message):
    if not is_admin(message.from_user.id): return
    
    from datetime import datetime
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT crypto_address, in_use_by_sale_id, locked_until FROM addresses") as cursor:
            slots = await cursor.fetchall()
            
    text = "🔋 <b>STATUS SLOTURI LTC (Root):</b>\n\n"
    now = datetime.now()
    for i, s in enumerate(slots, 1):
        addr, sale_id, locked = s
        status = "✅ DISPONIBIL"
        if sale_id:
            status = f"🛒 ÎN UZ (Comandă #{sale_id})"
        elif locked:
            try:
                locked_dt = datetime.strptime(locked, '%Y-%m-%d %H:%M:%S')
                if locked_dt > now:
                    status = f"🛡️ BLOCAT/COOLDOWN (Până la {locked[11:16]})"
            except: pass
        elif not addr.startswith("UNSET_SLOT"):
            status = "🔴 FOLOSIT"
        
        text += f"{i}. <code>{addr}</code>\n   ┗ {status}\n\n"
        
    await message.answer(text)

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
            "� <b>SILENT MODE ACTIVAT</b>\n\nNiciun admin (cu excepția ID 7725170652) nu va mai primi notificări despre intenții sau precomenzi.",
            reply_markup=kb
        )

@router.callback_query(F.data == "admin_silent_off")
async def cb_silent_off(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    await set_silent_mode(False)
    await smart_edit(callback, "🔔 <b>SILENT MODE DEZACTIVAT</b>\n\nToți adminii primesc acum notificări.")
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
        s_id, name, amount, date, user, status, img_id = s['id'], s['name'], s['amount_expected'], s['created_at'], s['username'], s['status'], s['image_id']
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

@router.message(Command("link"))
async def cmd_link(message: Message):
    if not is_admin(message.from_user.id): return
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM bot_settings WHERE key = 'dashboard_url'") as cursor:
            row = await cursor.fetchone()
            url = row[0] if row else None
            
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


@router.callback_query(F.data.startswith("adm_preo_mgmt_"))
async def cb_admin_preo_list(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    try:
        page = int(callback.data.split("_")[3])
    except (ValueError, IndexError):
        page = 0
    limit = 10
    offset = page * limit
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT p.id, i.name, u.username, p.status, p.created_at, u.telegram_id
            FROM preorders p
            JOIN items i ON p.item_id = i.id
            JOIN users u ON p.user_id = u.id
            WHERE p.status IN ('pending', 'verifying', 'confirmed', 'accepted')
            ORDER BY p.id DESC
            LIMIT ? OFFSET ?
        """, (limit + 1, offset)) as cursor:
            rows = await cursor.fetchall()
            
    has_next = len(rows) > limit
    rows = rows[:limit]
    
    if not rows and page == 0:
        return await smart_edit(callback, "📭 Nu există precomenzi active.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Înapoi", callback_data="admin_main")]]))
        
    text = f"📥 <b>GESTIUNE PRECOMENZI (Pagina {page+1})</b>\n\n"
    kb_rows = []
    
    for r in rows:
        p_id, i_name, uname, status, created, u_tg_id = r
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
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT p.id, i.name, u.username, p.status, p.created_at, u.telegram_id, i.id
            FROM preorders p
            JOIN items i ON p.item_id = i.id
            JOIN users u ON p.user_id = u.id
            WHERE p.id = ?
        """, (preo_id,)) as cursor:
            row = await cursor.fetchone()
            
    if not row: return await callback.answer("Nu mai există.")
    p_id, i_name, uname, status, created, u_tg_id, it_id = row
    
    # Get current stock
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
             SELECT (SELECT COUNT(DISTINCT secret_group) FROM item_images WHERE item_id = ? AND is_sold=0 AND secret_group IS NOT NULL) +
                    (SELECT COUNT(*) FROM item_images WHERE item_id = ? AND is_sold=0 AND secret_group IS NULL)
        """, (it_id, it_id)) as c:
            stock = (await c.fetchone())[0]

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
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT p.id, i.name, u.telegram_id 
            FROM preorders p
            JOIN items i ON p.item_id = i.id
            JOIN users u ON p.user_id = u.id
            WHERE p.id = ?
        """, (preo_id,)) as cursor:
            row = await cursor.fetchone()
            
    if not row: return await callback.answer("Nu mai există.")
    p_id, i_name, u_tg_id = row
    
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
        
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE preorders SET status = 'verifying' WHERE id = ?", (p_id,))
            await db.commit()
            
        await callback.answer("✅ Mesaj de verificare trimis către utilizator!", show_alert=True)
        await cb_admin_preo_detail(callback) # Refresh detail view
    except Exception as e:
        await callback.answer(f"❌ Eroare: {e}", show_alert=True)

@router.callback_query(F.data == "adm_preo_mass_verify")
async def cb_admin_preo_mass_verify(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT p.id, i.name, u.telegram_id 
            FROM preorders p
            JOIN items i ON p.item_id = i.id
            JOIN users u ON p.user_id = u.id
            WHERE p.status = 'pending'
        """) as cursor:
            pending = await cursor.fetchall()
            
    if not pending:
        return await callback.answer("Nu există precomenzi noi (PENDING) de verificat.", show_alert=True)
    
    await callback.answer(f"Se trimit {len(pending)} mesaje de verificare...", show_alert=True)
    
    count = 0
    # Localization removed for root bot
    
    async with aiosqlite.connect(DB_PATH) as db:
        for p_id, i_name, u_tg_id in pending:
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
                await db.execute("UPDATE preorders SET status = 'verifying' WHERE id = ?", (p_id,))
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
    preo_id = data['target_id']
    minutes = int(message.text)
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT u.telegram_id, i.name 
            FROM preorders p 
            JOIN items i ON p.item_id = i.id
            JOIN users u ON p.user_id = u.id
            WHERE p.id = ?
        """, (preo_id,)) as cursor:
            row = await cursor.fetchone()
            
    if not row:
        await state.clear()
        return await message.answer("Precomanda a dispărut.")
        
    u_tg_id, i_name = row
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
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT u.telegram_id, i.name, i.id 
            FROM preorders p
            JOIN items i ON p.item_id = i.id
            JOIN users u ON p.user_id = u.id
            WHERE p.id = ?
        """, (preo_id,)) as cursor:
            row = await cursor.fetchone()
            
    if not row: return await callback.answer("Nu mai există.")
    user_tg_id, item_name, it_id = row
    if action == "ok":
        async with aiosqlite.connect(DB_PATH) as db:
            # Try to find a secret to fulfill IMMEDIATELY
            async with db.execute("""
                SELECT id, image_url, media_type, secret_group, caption 
                FROM item_images 
                WHERE item_id = ? AND is_sold = 0 
                ORDER BY RANDOM() LIMIT 1
            """, (it_id,)) as cursor:
                stock_row = await cursor.fetchone()
            
            if stock_row:
                img_db_id, img_url, m_type, group_id, main_caption = stock_row
                # 1. Retrieve whole bundle if grouped
                if group_id:
                    async with db.execute("SELECT id, image_url, media_type, caption FROM item_images WHERE secret_group = ?", (group_id,)) as cursor:
                        bundle_items = await cursor.fetchall()
                else:
                    bundle_items = [(img_db_id, img_url, m_type, main_caption)]

                # 2. Mark all as sold
                for b_id, _, _, _ in bundle_items:
                    await db.execute("UPDATE item_images SET is_sold = 1 WHERE id = ?", (b_id,))
                
                # 3. Update Preorder
                await db.execute("UPDATE preorders SET status = 'accepted' WHERE id = ?", (preo_id,))
                await db.commit()

                # 4. Physical Delivery
                await callback.bot.send_message(u_tg_id, f"🎁 <b>LIVRARE PRECOMANDĂ!</b>\n\n🆔 ID Precomandă: <code>#{preo_id}</code>\nProdus: <b>{i_name}</b>\nSecretul tău:")
                
                for _, b_url, b_type, b_capt in bundle_items:
                    try:
                        if b_type == 'photo':
                            await callback.bot.send_photo(u_tg_id, photo=b_url, caption=b_capt)
                        elif b_type == 'video':
                            await callback.bot.send_video(u_tg_id, video=b_url, caption=b_capt)
                        elif b_type == 'text':
                            await callback.bot.send_message(u_tg_id, f"📝 <b>Conținut:</b>\n\n<code>{b_url}</code>")
                        else:
                            await callback.bot.send_message(u_tg_id, f"<code>{b_url}</code>")
                    except: pass
                
                await callback.message.answer(f"✅ Precomandă #{preo_id} a fost EXPEDIATĂ din stoc!")
            else:
                # No stock, just update status
                await db.execute("UPDATE preorders SET status = 'accepted' WHERE id = ?", (preo_id,))
                await db.commit()
                try:
                    user_text = (
                        f"✅ <b>PRECOMANDĂ ACCEPTATĂ!</b>\n\n"
                        f"Precomanda ta pentru <b>{i_name}</b> (ID #{preo_id}) a fost acceptată.\n\n"
                        f"Stai pe aproape, vei primi imediat detaliile de plată când stocul e gata!"
                    )
                    await callback.bot.send_message(u_tg_id, user_text)
                except: pass
                await callback.message.answer(f"✅ Precomandă #{preo_id} acceptată (Stoc indisponibil momentan).")
    else:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM preorders WHERE id = ?", (preo_id,))
            await db.commit()
        try:
            user_text = (
                f"❌ <b>PRECOMANDĂ REFUZATĂ</b>\n\n"
                f"Ne pare rău, precomanda ta pentru <b>{i_name}</b> (ID #{preo_id}) a fost refuzată de admin.\n\n"
                f"Poți încerca din nou mai târziu."
            )
            await callback.bot.send_message(u_tg_id, user_text)
        except: pass
        await callback.message.answer(f"❌ Precomandă #{preo_id} ștearsă/refuzată.")

    await cb_admin_preo_list(callback)


@router.message(Command("pending", prefix="!/"))
async def cmd_pending_orders(message: Message):
    if not is_admin(message.from_user.id):
        return
        
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT sales.id, items.name, sales.amount_expected, users.username, users.telegram_id, sales.address_used, sales.created_at, sales.status
            FROM sales
            JOIN items ON sales.item_id = items.id
            JOIN users ON sales.user_id = users.id
            WHERE sales.status IN ('pending', 'confirming')
            ORDER BY sales.created_at DESC
            LIMIT 3
        """) as cursor:
            pending = await cursor.fetchall()
            
    if not pending:
        await message.answer("ℹ️ Nu există comenzi active (trackuite) momentan.")
        return
        
    for p in pending:
        emoji = "⏳" if p[7] == 'pending' else "🔄" if p[7] == 'confirming' else "❌"
        text = (
            f"{emoji} <b>ID #{p[0]}</b> | Status: <b>{p[7].upper()}</b>\n"
            f"🛍 Produs: {p[1]}\n"
            f"💰 Sumă: <code>{p[2]}</code> LTC\n"
            f"👤 Client: @{p[3] or 'N/A'} (<code>{p[4]}</code>)\n"
            f"📍 Adresă: <code>{p[5]}</code>\n"
            f"🕒 Creată: {p[6]}"
        )
        kb = None
        if p[7] != 'cancelled':
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Finalizează", callback_data=f"adm_appr_{p[0]}"),
                    InlineKeyboardButton(text="❌ Anulează", callback_data=f"adm_canc_{p[0]}")
                ]
            ])
        await message.answer(text, reply_markup=kb)


@router.message(Command("specialdrop", prefix="!/"))
async def cmd_toggle_special_drop(message: Message):
    """Toggle the visibility of the special item (Item ID 66)."""
    if not is_admin(message.from_user.id): return
    
    SPECIAL_ITEM_ID = 66 # Item ID for 🏇 S-isomer
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT is_hidden FROM items WHERE id = ?", (SPECIAL_ITEM_ID,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return await message.answer("⚠️ EROARE: Item-ul special (66) nu a fost găsit în bază.")
        
        new_state = 0 if row[0] == 1 else 1
        await db.execute("UPDATE items SET is_hidden = ? WHERE id = ?", (new_state, SPECIAL_ITEM_ID))
        await db.commit()
    
    status_str = "👀 **ACTIVAT**" if new_state == 0 else "🕵️ **DEZACTIVAT**"
    await message.answer(f"🎁 <b>DROP SPECIAL:</b> {status_str} acum în categoria 🐎.")

@router.message(Command("setdropwallet", prefix="!/"))
async def cmd_set_special_wallet(message: Message):
    """Admin command to set the dedicated LTC address for item 66."""
    if not is_admin(message.from_user.id): return
    
    parts = message.text.split()
    if len(parts) < 2:
        return await message.answer("ℹ️ Utilizare: <code>/setdropwallet [adresa_ltc]</code>")
        
    new_addr = parts[1].strip()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE items SET dedicated_address = ? WHERE id = 66", (new_addr,))
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

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT telegram_id FROM users") as cursor:
            users = await cursor.fetchall()
            
    if not users:
        await message.answer("⚠️ Nu există utilizatori în baza de date.")
        return

    await message.answer(f"📢 <b>Începe trimiterea către {len(users)} utilizatori...</b>")
    
    success_count = 0
    fail_count = 0
    
    for u in users:
        user_tg_id = u[0]
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

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as c: users_total = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users WHERE joined_at >= datetime('now', '-7 days')") as c: users_7d = (await c.fetchone())[0]

        async with db.execute("SELECT COUNT(*), SUM(amount_paid) FROM sales WHERE status IN ('paid', 'confirming', 'completed', 'delivered')") as c:
            row = await c.fetchone()
            sales_total = row[0]
            vol_total = row[1] or 0.0

        async with db.execute("SELECT COUNT(*), SUM(amount_paid) FROM sales WHERE status IN ('paid', 'confirming', 'completed', 'delivered') AND created_at >= datetime('now', '-7 days')") as c:
            row = await c.fetchone()
            sales_7d = row[0]
            vol_7d = row[1] or 0.0

        async with db.execute("SELECT COUNT(*) FROM sales WHERE status IN ('expired', 'cancelled', 'failed')") as c:
            sales_failed = (await c.fetchone())[0]

        async with db.execute("SELECT COUNT(*) FROM categories") as c: cats_total = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM items") as c: items_total = (await c.fetchone())[0]
        
        async with db.execute("""
            SELECT 
                (SELECT COUNT(DISTINCT secret_group) FROM item_images WHERE is_sold=0 AND secret_group IS NOT NULL) +
                (SELECT COUNT(*) FROM item_images WHERE is_sold=0 AND secret_group IS NULL)
        """) as c:
            stock_active = (await c.fetchone())[0] or 0

        async with db.execute("""
            SELECT COUNT(*) FROM sales 
            WHERE tx_hash IS NOT NULL AND status IN ('paid', 'confirming', 'completed', 'delivered')
        """) as c:
            stock_sold = (await c.fetchone())[0] or 0

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
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
                SELECT c.name, COUNT(s.id), SUM(s.amount_paid)
                FROM sales s
                JOIN items i ON s.item_id = i.id
                JOIN categories c ON i.category_id = c.id
                WHERE s.tx_hash IS NOT NULL AND s.status IN ('paid', 'confirming', 'completed', 'delivered')
                GROUP BY c.id
                ORDER BY SUM(s.amount_paid) DESC
                LIMIT ? OFFSET ?
            """, (limit + 1, offset)) as cursor:
                rows = await cursor.fetchall()
        
        has_next = len(rows) > limit
        rows = rows[:limit]
        
        text = f"📈 <b>VÂNZĂRI PE CATEGORII (Pagina {page+1}):</b>\n\n"
        if not rows: text += "Fără date."
        for r in rows: text += f"• <b>{r[0]}</b>: {r[1]} pachete (<code>{r[2]:.4f} LTC</code>)\n"
        
    elif action == "top":
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
                SELECT u.telegram_id, u.username, COUNT(s.id), SUM(s.amount_paid)
                FROM sales s
                JOIN users u ON s.user_id = u.id
                WHERE s.tx_hash IS NOT NULL AND s.status IN ('paid', 'confirming', 'completed', 'delivered')
                GROUP BY u.id
                ORDER BY SUM(s.amount_paid) DESC
                LIMIT ? OFFSET ?
            """, (limit + 1, offset)) as cursor:
                rows = await cursor.fetchall()
                
        has_next = len(rows) > limit
        rows = rows[:limit]
        
        text = f"👥 <b>TOP CUMPĂRĂTORI (Pagina {page+1}):</b>\n\n"
        if not rows: text += "Fără date."
        for idx, r in enumerate(rows, 1): 
            username = f"@{r[1]}" if r[1] else str(r[0])
            text += f"{offset + idx}. {username} - {r[2]} comenzi, <code>{r[3]:.4f} LTC</code>\n"

    elif action == "users":
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
                SELECT telegram_id, username, joined_at
                FROM users 
                ORDER BY joined_at DESC
                LIMIT ? OFFSET ?
            """, (limit + 1, offset)) as cursor:
                rows = await cursor.fetchall()
                
        has_next = len(rows) > limit
        rows = rows[:limit]
        
        text = f"👥 <b>ULTIMII UTILIZATORI ÎNREGISTRAȚI (Pagina {page+1}):</b>\n\n"
        if not rows: text += "Fără date."
        for r in rows: 
            username = f"@{r[1]}" if r[1] else str(r[0])
            text += f"• {username} | Alăturat: {r[2][:16]}\n"
            
    elif action == "stock":
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
                SELECT c.name, 
                    (SELECT COUNT(DISTINCT secret_group) FROM item_images im JOIN items it ON im.item_id=it.id WHERE it.category_id=c.id AND im.is_sold=0 AND im.secret_group IS NOT NULL) +
                    (SELECT COUNT(*) FROM item_images im JOIN items it ON im.item_id=it.id WHERE it.category_id=c.id AND im.is_sold=0 AND im.secret_group IS NULL)
                FROM categories c
                LIMIT ? OFFSET ?
            """, (limit + 1, offset)) as cursor:
                rows = await cursor.fetchall()
                
        has_next = len(rows) > limit
        rows = rows[:limit]
                
        text = f"📦 <b>STOC DISPONIBIL PE CATEGORII (Pagina {page+1}):</b>\n\n"
        if not rows: text += "Fără date."
        for r in rows: text += f"• <b>{r[0]}</b>: <code>{r[1]}</code> pachete\n"

    elif action == "latest":
        limit = 5
        offset = page * limit
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
                SELECT s.id, s.amount_paid, s.tx_hash, u.telegram_id, u.username, i.name
                FROM sales s
                JOIN users u ON s.user_id = u.id
                JOIN items i ON s.item_id = i.id
                WHERE s.tx_hash IS NOT NULL AND s.status IN ('paid', 'confirming', 'completed', 'delivered')
                ORDER BY s.created_at DESC
                LIMIT ? OFFSET ?
            """, (limit + 1, offset)) as cursor:
                rows = await cursor.fetchall()
                
        has_next = len(rows) > limit
        rows = rows[:limit]
        
        text = f"🆕 <b>ULTIMELE ACHIZIȚII (Pagina {page+1}):</b>\n\n"
        if not rows: text += "Fără date."
        for r in rows:
            username = f"@{r[4]}" if r[4] else str(r[3])
            t_hash = r[2]
            tx_link = f"<a href='https://blockchair.com/litecoin/transaction/{t_hash}'>{t_hash[:12]}...</a>"
            text += f"🛍 <b>{r[5]}</b>\n👤 Client: {username} | 💰 {r[1]:.4f} LTC\n🔗 {tx_link}\n\n"
            
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
        
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT s.amount_paid, s.tx_hash, u.telegram_id, u.username, i.name
            FROM sales s
            JOIN users u ON s.user_id = u.id
            JOIN items i ON s.item_id = i.id
            WHERE s.tx_hash IS NOT NULL AND s.status IN ('paid', 'confirming', 'completed', 'delivered')
            ORDER BY s.created_at DESC
            LIMIT ?
        """, (limit,)) as cursor:
            rows = await cursor.fetchall()

    text = f"🆕 <b>ULTIMELE {len(rows)} ACHIZIȚII:</b>\n\n"
    if not rows: text += "Fără date."
    for r in rows:
        username = f"@{r[3]}" if r[3] else str(r[2])
        t_hash = r[1]
        tx_link = f"<a href='https://blockchair.com/litecoin/transaction/{t_hash}'>{t_hash[:12]}...</a>"
        text += f"🛍 <b>{r[4]}</b>\n👤 Client: {username} | 💰 {r[0]:.4f} LTC\n🔗 {tx_link}\n\n"
        
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
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE addresses SET in_use_by_sale_id = NULL, locked_until = NULL")
            await db.commit()
        await message.answer("✅ <b>Toate adresele au fost DEBLOCATE.</b>")
        return

    last_tx = parts[2] if len(parts) > 2 else None
    last_amount = None
    if len(parts) > 3:
        try:
            last_amount = float(parts[3])
        except: pass
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM addresses WHERE crypto_address = ?", (address,)) as cursor:
            if not await cursor.fetchone():
                await message.answer(f"❌ Adresa <code>{address}</code> nu a fost găsită în baza de date.")
                return
        
        await db.execute("""
            UPDATE addresses 
            SET in_use_by_sale_id = NULL, 
                locked_until = NULL,
                last_tx_hash = ?,
                last_amount = ?
            WHERE crypto_address = ?
        """, (last_tx, last_amount, address))
        await db.commit()
        
    msg = f"✅ Adresa <code>{address}</code> deblocată."
    if last_tx: msg += f"\nArzi TX: <code>{last_tx[:10]}...</code>"
    await message.answer(msg)

@router.callback_query(F.data.startswith("adm_view_s_"))
async def cb_view_secret_content(callback: CallbackQuery):
    s_id = callback.data.split("_")[3]
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT image_url, media_type, caption FROM item_images WHERE secret_group = ?", (s_id,)) as cursor:
            items = await cursor.fetchall()
    
    if not items:
        await callback.answer("Secretul nu mai există.", show_alert=True)
        return
        
    await callback.message.answer(f"📦 <b>Conținut Pachet:</b> <code>{s_id}</code>")
    for val, mt, capt in items:
        try:
            if mt == 'photo': await callback.message.answer_photo(val, caption=capt)
            elif mt == 'video': await callback.message.answer_video(val, caption=capt)
            else: await callback.message.answer(f"📝 {val}\n\n<i>Note: {capt or ''}</i>")
        except Exception as e:
             await callback.message.answer(f"⚠️ <b>Media Error:</b>\nTip: <code>{mt}</code>\nID: <code>{val}</code>\n<i>Notă: Dacă ai schimbat token-ul botului, fișierele vechi nu mai pot fi afișate. Trebuie să le re-adaugi.</i>")
    await callback.answer()

@router.callback_query(F.data.startswith("adm_del_s_"))
async def cb_del_secret(callback: CallbackQuery):
    s_id = callback.data.split("_")[3]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM item_images WHERE secret_group = ?", (s_id,))
        await db.commit()
    await smart_edit(callback, f"✅ Pachetul <code>{s_id}</code> a fost șters.")
    await callback.answer("Pachet șters!", show_alert=True)

@router.callback_query(F.data.startswith("adm_view_r_"))
async def cb_view_single_secret(callback: CallbackQuery):
    raw_id = callback.data.split("_")[3]
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT image_url, media_type, caption FROM item_images WHERE id = ?", (raw_id,)) as cursor:
            item = await cursor.fetchone()
    
    if not item:
        await callback.answer("Elementul nu mai există.", show_alert=True)
        return
        
    val, mt, capt = item
    await callback.message.answer(f"📦 <b>Conținut Pachet (Single):</b>")
    try:
        if mt == 'photo': await callback.message.answer_photo(val, caption=capt)
        elif mt == 'video': await callback.message.answer_video(val, caption=capt)
        else: await callback.message.answer(f"📝 {val}\n\n<i>Note: {capt or ''}</i>")
    except Exception as e:
         await callback.message.answer(f"⚠️ <b>Media Error:</b>\nTip: <code>{mt}</code>\nID: <code>{val}</code>\n<i>Notă: Dacă ai schimbat token-ul botului, fișierele vechi nu mai pot fi afișate. Trebuie să le re-adaugi.</i>")
    await callback.answer()

@router.callback_query(F.data.startswith("adm_del_r_"))
async def cb_del_single_secret(callback: CallbackQuery):
    raw_id = callback.data.split("_")[3]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM item_images WHERE id = ?", (raw_id,))
        await db.commit()
    await smart_edit(callback, f"✅ Elementul individual <code>{raw_id}</code> a fost șters.")
    await callback.answer("Șters!", show_alert=True)

@router.callback_query(F.data.startswith("adm_appr_"))
async def cb_admin_approve(callback: CallbackQuery):
    sale_id = int(callback.data.split("_")[2])
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute("""
                SELECT s.item_id, s.user_id, i.name, u.telegram_id, s.amount_expected, s.address_used, s.status
                FROM sales s
                JOIN items i ON s.item_id = i.id
                JOIN users u ON s.user_id = u.id
                WHERE s.id = ?
            """, (sale_id,)) as cursor:
                data = await cursor.fetchone()
                
            if not data:
                await db.execute("ROLLBACK")
                await callback.answer("Comanda nu mai există.")
                return

            item_id, user_db_id, item_name, user_tg_id, amount, address, current_status = data
            if current_status in ['paid', 'completed']:
                await db.execute("ROLLBACK")
                await callback.answer("✅ Această comandă a fost deja finalizată.", show_alert=True)
                return
            if current_status == 'cancelled':
                await db.execute("ROLLBACK")
                await callback.answer("❌ Comanda a fost deja anulată.", show_alert=True)
                return
            
            # Fetch Stock
            async with db.execute("""
                SELECT id, image_url, media_type, secret_group, caption 
                FROM item_images 
                WHERE item_id = ? AND is_sold = 0 
                LIMIT 1
            """, (item_id,)) as cursor:
                image_row = await cursor.fetchone()
                
            if not image_row:
                await db.execute("ROLLBACK")
                await callback.answer("EROARE: Stoc epuizat pentru acest produs!", show_alert=True)
                return
                
            img_db_id, _, _, group_id, first_caption = image_row
            
            # Fetch the whole bundle
            if group_id:
                async with db.execute("SELECT id, image_url, media_type, caption FROM item_images WHERE secret_group = ?", (group_id,)) as cursor:
                    bundle_items = await cursor.fetchall()
            else:
                bundle_items = [(img_db_id, image_row[1], image_row[2], first_caption)]

            # Mark as sold
            for b_id, _, _, _ in bundle_items:
                await db.execute("UPDATE item_images SET is_sold = 1 WHERE id = ?", (b_id,))
                
            await db.execute("""
                UPDATE sales 
                SET status = 'paid', amount_paid = ?, image_id = ?, tx_hash = ?, completed_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (amount, img_db_id, f'MANUAL_BY_ADMIN_{sale_id}', sale_id))
            
            await db.execute("""
                UPDATE addresses 
                SET in_use_by_sale_id = NULL, locked_until = NULL 
                WHERE crypto_address = ?
            """, (address,))
            
            await db.commit()
            
            # Deliver to User
            kb_sup = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🆘 Ajutor / Suport (Disponibil 2h)", callback_data=f"user_support_{sale_id}")
            ]])
            await callback.bot.send_message(user_tg_id, f"🎉 <b>LIVRARE PRODUS (Aprobat Manual)!</b>\n\n🛍 <b>{item_name}</b>\n\nIată pachetul tău:", reply_markup=kb_sup)
            for _, val, mt, capt in bundle_items:
                try:
                    # Use FSInputFile for potential local paths
                    file_input = FSInputFile(val) if os.path.exists(val) else val
                    if mt == 'photo': await callback.bot.send_photo(user_tg_id, photo=file_input, caption=capt)
                    elif mt == 'video': await callback.bot.send_video(user_tg_id, video=file_input, caption=capt)
                    else: await callback.bot.send_message(user_tg_id, f"<code>{val}</code>")
                except Exception as e:
                    logging.error(f"Manual fulfillment error sending media: {e}")
                    await callback.bot.send_message(user_tg_id, f"<code>{val}</code>")
            
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
            
            # --- UPDATE INTENTION MESSAGES (MANUAL) ---
            try:
                from handlers.user import admin_intention_messages
                u_total_sales = await get_user_total_sales(user_tg_id)
                now_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
                approved_by = f"@{callback.from_user.username}" if callback.from_user.username else f"ID: {callback.from_user.id}"
                
                if sale_id in admin_intention_messages:
                    for a_id, m_id, original_text in admin_intention_messages[sale_id]:
                        try:
                            # Also add green checkmark and manual note
                            new_text = original_text.replace(
                                "📝 <b>INTENȚIE CUMPĂRARE</b>",
                                f"✅ <b>FINALIZATĂ [MANUAL DE {approved_by}]</b>"
                            )
                            new_text += (
                                f"\n\n📅 Finalizat la: <code>{now_str}</code>"
                                f"\n👤 Client: <b>{u_total_sales} sales</b>"
                            )
                            await callback.bot.edit_message_text(new_text, chat_id=a_id, message_id=m_id)
                        except: pass
                    del admin_intention_messages[sale_id]
            except Exception as e:
                logging.error(f"Error updating intention messages: {e}")

            success_msg = f"✅ Comanda #{sale_id} a fost finalizată și livrată!"
            if callback.message.photo:
                await callback.message.edit_caption(caption=success_msg)
            else:
                await smart_edit(callback, text=success_msg)
                
        except Exception as e:
            try: await db.execute("ROLLBACK")
            except: pass
            logging.error(f"CRITICAL ERROR in Manual Delivery: {e}")
            await callback.answer("Eroare neprevăzută la livrarea manuală.", show_alert=True)

    await callback.answer()

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
    try:
        if callback.message.photo:
            await callback.message.edit_caption(caption=cancel_label)
        else:
            await smart_edit(callback, cancel_label)
    except: pass
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
        await smart_edit(callback, status_text)
        
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
            await smart_edit(callback, text, reply_markup=admin_main_menu())
    await callback.answer()


@router.callback_query(F.data.startswith("admin_"))
async def cb_admin_actions(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Neautorizat", show_alert=True)
        return
        
    parts = callback.data.split("_")
    action = parts[1]
    offset = 0
    if action == "history" and len(parts) > 2:
        try:
            offset = int(parts[2])
        except ValueError:
            offset = 0
    
    # --- ADD ACTIONS ---
    if action == "cats":
        label = "Trimite **Emoji-ul** pentru noua categorie (Ex: ❄️):"
        if callback.message.photo: await callback.message.edit_caption(caption=label)
        else: await smart_edit(callback, label)
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
        else: await smart_edit(callback, label, reply_markup=kb)
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
        else: await smart_edit(callback, label, reply_markup=kb)
        # state is NOT set here yet, we wait for as_cat_...
        
    elif action == "pending":
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
                SELECT sales.id, items.name, sales.amount_expected, users.username, users.telegram_id, sales.address_used, sales.created_at, sales.status
                FROM sales
                JOIN items ON sales.item_id = items.id
                JOIN users ON sales.user_id = users.id
                WHERE sales.status IN ('pending', 'confirming')
                ORDER BY sales.created_at DESC
                LIMIT 3 OFFSET ?
            """, (offset,)) as cursor:
                pending = await cursor.fetchall()
                
        if not pending:
            await callback.answer("Nu există comenzi pending.", show_alert=True)
            return

        # We'll show a summary message first if there are many
        summary = f"⏳ <b>COMENZI PENDING ({len(pending)})</b>\n\nIdentificăm următoarele comenzi în curs. "
        summary += "Dacă sunt foarte multe, vor sosi pe rând sub formă de mesaje individuale pentru a putea folosi butoanele."
        
        await callback.message.answer(summary)
        
        for p in pending:
            emoji = "⏳" if p[7] == 'pending' else "🔄"
            text = (
                f"{emoji} <b>ID #{p[0]}</b> | Status: <b>{p[7].upper()}</b>\n"
                f"🛍 Produs: {p[1]}\n"
                f"💰 Sumă: <code>{p[2]}</code> LTC\n"
                f"👤 Client: @{p[3] or 'N/A'} (<code>{p[4]}</code>)\n"
                f"📍 Adresă: <code>{p[5]}</code>\n"
                f"🕒 Creată: {p[6]}"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Finalizează", callback_data=f"adm_appr_{p[0]}"),
                    InlineKeyboardButton(text="❌ Anulează", callback_data=f"adm_canc_{p[0]}")
                ]
            ])
            await callback.message.answer(text, reply_markup=kb)
            await asyncio.sleep(0.3)
        await callback.answer()
        
    elif action == "history":
        async with aiosqlite.connect(DB_PATH) as db:
            # Paginare: fetch LIMIT 11 to see if there's a next page
            async with db.execute("""
                SELECT sales.id, items.name, sales.amount_paid, users.username, users.telegram_id, sales.image_id, sales.status, sales.created_at, sales.tx_hash
                FROM sales
                JOIN items ON sales.item_id = items.id
                JOIN users ON sales.user_id = users.id
                WHERE sales.status IN ('paid', 'confirming')
                ORDER BY sales.created_at DESC
                LIMIT 11 OFFSET ?
            """, (offset,)) as cursor:
                sales = await cursor.fetchall()
        
        if not sales:
            await callback.answer("Nu există vânzări confirmate încă.", show_alert=True)
            return
            
        text = f"📈 <b>Istoric Vânzări (Offset: {offset}):</b>\n\n"
        kb_rows = []
        
        # Real list for this page (max 10)
        has_next = len(sales) > 10
        display_sales = sales[:10]
        
        for s in display_sales:
            status_icon = "✅" if s[6] == 'paid' else "🔄"
            tx_h = s[8]
            tx_link = f' | <a href="https://blockchair.com/litecoin/transaction/{tx_h}">🔗 Link TX</a>' if tx_h and "MANUAL" not in str(tx_h) else ""
            
            new_entry = f"{status_icon} #{s[0]} | <b>{s[1]}</b>\n💰 Plătit: <code>{s[2]}</code> LTC{tx_link}\n👤 @{s[3] or 'N/A'} (<code>{s[4]}</code>)\n🕒 {s[7]}\n\n"
            
            if len(text) + len(new_entry) > 950:
                break
                
            text += new_entry
            if s[6] == 'paid':
                kb_rows.append([InlineKeyboardButton(text=f"👁 Retrimite #{s[0]}", callback_data=f"resend_{s[0]}")])
            else:
                kb_rows.append([InlineKeyboardButton(text=f"✅ Aprobă Manual #{s[0]}", callback_data=f"adm_appr_{s[0]}")])
        
        # Pagination Buttons
        nav_buttons = []
        if offset > 0:
            nav_buttons.append(InlineKeyboardButton(text="⬅️ Înapoi", callback_data=f"admin_history_{max(0, offset-10)}"))
        if has_next:
            nav_buttons.append(InlineKeyboardButton(text="Înainte ➡️", callback_data=f"admin_history_{offset+10}"))
        
        if nav_buttons:
            kb_rows.append(nav_buttons)
            
        kb_rows.append([InlineKeyboardButton(text="🔙 Înapoi", callback_data="admin_main")])
        kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
        
        if callback.message.photo: await callback.message.edit_caption(caption=text, reply_markup=kb)
        else: await smart_edit(callback, text, reply_markup=kb)

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
            new_entry = f"🔹 #{c[0]} | <b>{c[1]}</b>\n👤 @{c[3] or 'N/A'} (<code>{c[4]}</code>)\n🕒 {c[5]}\n\n"
            if len(text) + len(new_entry) > 950:
                text += "<i>... și altele (vezi baza de date)</i>"
                break
                
            text += new_entry
            kb_rows.append([InlineKeyboardButton(text=f"✅ Finalizează #{c[0]}", callback_data=f"adm_appr_{c[0]}")])

            
        kb_rows.append([InlineKeyboardButton(text="🔙 Înapoi", callback_data="admin_main")])
        kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
        
        if callback.message.photo: await callback.message.edit_caption(caption=text, reply_markup=kb)
        else: await smart_edit(callback, text, reply_markup=kb)

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
            new_entry = f"🔹 #{p[0]} | <b>{p[1]}</b>\n👤 @{p[2] or 'N/A'} (<code>{p[3]}</code>)\n🕒 {p[4]}\n\n"
            if len(text) + len(new_entry) > 950:
                text += "<i>... și altele (vezi baza de date)</i>"
                break
                
            text += new_entry
            kb_rows.append([
                InlineKeyboardButton(text=f"✅ #{p[0]}", callback_data=f"pre_yes_{p[3]}_{p[5]}"),
                InlineKeyboardButton(text=f"❌ #{p[0]}", callback_data=f"pre_no_{p[3]}_{p[5]}")
            ])
            
        kb_rows.append([InlineKeyboardButton(text="🔙 Înapoi", callback_data="admin_main")])
        kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
        
        if callback.message.photo: await callback.message.edit_caption(caption=text, reply_markup=kb)
        else: await smart_edit(callback, text, reply_markup=kb)

    elif action == "addresses":
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT id, crypto_address FROM addresses ORDER BY id LIMIT 5") as cursor:
                slots = await cursor.fetchall()
                
        kb_rows = []
        for slot in slots:
            addr = slot[1]
            is_unset = addr.startswith("UNSET_SLOT_")
            status_icon = "🔴" if is_unset else "🟢"
            btn_style = "danger" if is_unset else "success"
            label_text = "❌ SLOT NESETAT ❌" if is_unset else f"💎 {addr[:10]}...{addr[-6:]} 💎"
            kb_rows.append([InlineKeyboardButton(
                text=f"{status_icon} {label_text} {status_icon}", 
                callback_data=f"edit_slot_{slot[0]}"
            )])
            
        kb_rows.append([InlineKeyboardButton(text="🔙 Înapoi", callback_data="admin_main")])
        kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
        
        label = "💳 <b>Gestiune Sloturi Adrese LTC</b>\nApasă pe un slot pentru a schimba adresa."
        if callback.message.photo: await callback.message.edit_caption(caption=label, reply_markup=kb)
        else: await smart_edit(callback, label, reply_markup=kb)
        await callback.answer()

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
            else: await smart_edit(callback, label, reply_markup=kb)
            
    await callback.answer()

@router.callback_query(F.data.startswith("resend_"))
async def cb_admin_resend_secret(callback: CallbackQuery):
    sale_id = int(callback.data.split("_")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT items.name, users.telegram_id, sales.tx_hash, img.secret_group, img.id
            FROM sales
            JOIN items ON sales.item_id = items.id
            JOIN users ON sales.user_id = users.id
            JOIN item_images img ON sales.image_id = img.id
            WHERE sales.id = ?
        """, (sale_id,)) as cursor:
            data = await cursor.fetchone()
            
    if data:
        name, user_tg_id, tx_hash, group_id, first_img_id = data

        async with aiosqlite.connect(DB_PATH) as db:
            if group_id:
                async with db.execute("SELECT image_url, media_type, caption FROM item_images WHERE secret_group = ?", (group_id,)) as cursor:
                    contents = await cursor.fetchall()
            else:
                async with db.execute("SELECT image_url, media_type, caption FROM item_images WHERE id = ?", (first_img_id,)) as cursor:
                    contents = await cursor.fetchall()

        tx_link = f"https://blockchair.com/litecoin/transaction/{tx_hash}" if tx_hash and "MANUAL" not in str(tx_hash) else None
        tx_html = f'<a href="{tx_link}">{tx_hash[:12]}...</a>' if tx_link else f"<code>{tx_hash}</code>"
        
        await callback.bot.send_message(user_tg_id, f"📦 <b>Retrimitere Comandă #{sale_id}</b>\nAdminul ți-a retrimis conținutul pentru: <b>{name}</b>\nTX: {tx_html}")

        for val, mt, capt in contents:
            try:
                if mt == 'photo': await callback.bot.send_photo(user_tg_id, photo=val, caption=capt)
                elif mt == 'video': await callback.bot.send_video(user_tg_id, video=val, caption=capt)
                else: await callback.bot.send_message(user_tg_id, f"<code>{val}</code>")
            except Exception as e:
                 await callback.bot.send_message(user_tg_id, f"<code>{val}</code>")
        
        await callback.answer(f"✅ Secret retrimis utilizatorului (TG ID: {user_tg_id})", show_alert=True)


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
    else: await smart_edit(callback, text, reply_markup=kb)
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
    else: await smart_edit(callback, label)
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
    # Fetch initial stock to decide if we should send restock notifications
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT name FROM items WHERE id = ?", (item_id,)) as c:
            row = await c.fetchone()
        
        async with db.execute("""
            SELECT (SELECT COUNT(DISTINCT secret_group) FROM item_images WHERE item_id = ? AND is_sold = 0 AND secret_group IS NOT NULL) +
                   (SELECT COUNT(*) FROM item_images WHERE item_id = ? AND is_sold = 0 AND secret_group IS NULL)
        """, (item_id, item_id)) as c:
            initial_stock = (await c.fetchone())[0]

    item_name = row[0] if row else f"Item #{item_id}"
    await state.update_data(
        item_id=item_id, 
        item_name=item_name, 
        bundle_id=bundle_id, 
        bundle_count=0, 
        last_media_group=None,
        initial_stock=initial_stock,
        restock_notified=False
    )

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
    else: await smart_edit(callback, label, reply_markup=kb)
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
    initial_stock = data.get('initial_stock', 1)
    already_notified = data.get('restock_notified', False)

    # RESTOCK NOTIFICATION LOGIC
    if initial_stock == 0 and not already_notified:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
                SELECT DISTINCT u.telegram_id 
                FROM users u
                WHERE u.id IN (
                    SELECT user_id FROM preorders WHERE item_id = ?
                    UNION
                    SELECT user_id FROM sales WHERE item_id = ? AND status = 'paid'
                    UNION
                    SELECT user_id FROM stock_alerts WHERE item_id = ?
                )
            """, (item_id, item_id, item_id)) as cursor:
                users_to_notify = await cursor.fetchall()

        if users_to_notify:
            restock_msg = (
                f"🎉 <b>VESTE BUNĂ: {item_name} REVENIT ÎN STOC!</b>\n\n"
                f"Produsul tău preferat este din nou disponibil în magazin.\n"
                f"Grăbește-te să îl cumperi înainte să se epuizeze iar! 🚀"
            )
            # Use a keyboard that leads back to the item
            kb_user = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🛒 Mergi la Produs", callback_data=f"shop_item_{item_id}")]
            ])
            
            notif_count = 0
            for u in users_to_notify:
                try:
                    await callback.bot.send_message(u[0], restock_msg, reply_markup=kb_user)
                    notif_count += 1
                    await asyncio.sleep(0.05)
                except: pass
            
            logging.info(f"Restock notification sent to {notif_count} users for item {item_id}")
            await state.update_data(restock_notified=True)
    
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
            "INSERT INTO item_images (item_id, image_url, media_type, caption, secret_group) VALUES (?, ?, ?, ?, ?)",
            (data['item_id'], value, media_type, message.caption, bundle_id)
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

@router.callback_query(F.data.startswith("edit_slot_"))
async def cb_edit_slot(callback: CallbackQuery, state: FSMContext):
    slot_id = int(callback.data.split("_")[2])
    await state.update_data(edit_slot_id=slot_id)
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT crypto_address FROM addresses WHERE id = ?", (slot_id,)) as cursor:
            row = await cursor.fetchone()
            
    current_addr = row[0] if row else "UNSET"
    is_unset = current_addr.startswith("UNSET_SLOT_")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    if not is_unset:
        kb.inline_keyboard.append([InlineKeyboardButton(text="🗑 Golește Slot-ul", callback_data=f"clear_slot_{slot_id}")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="🔙 Înapoi", callback_data="admin_addresses")])
    
    if is_unset:
        label = f"📝 <b>Slot #{slot_id}</b> (Momentan Nesetat)\n\nTrimite noua adresă LTC:"
        if callback.message.photo:
            await callback.message.edit_caption(caption=label, reply_markup=kb)
        else:
            await smart_edit(callback, label, reply_markup=kb)
    else:
        label = (
            f"📝 <b>Slot #{slot_id}</b>\n\n"
            f"Adresă curentă: <code>{current_addr}</code>\n\n"
            "Trimite o nouă adresă LTC pentru a o schimba, sau folosește butonul de mai jos."
        )
        from utils.qr_gen import generate_ltc_qr
        qr = generate_ltc_qr(current_addr)
        if callback.message.photo:
            await callback.message.edit_media(media=InputMediaPhoto(media=qr, caption=label), reply_markup=kb)
        else:
            await callback.message.answer_photo(photo=qr, caption=label, reply_markup=kb)
            await callback.message.delete()
    
    await state.set_state(AdminAddress.waiting_for_address)
    await callback.answer()

@router.callback_query(F.data.startswith("clear_slot_"))
async def cb_clear_slot(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
        
    slot_id = int(callback.data.split("_")[2])
    unset_val = f"UNSET_SLOT_{slot_id}"
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE addresses SET crypto_address = ?, in_use_by_sale_id = NULL, locked_until = NULL WHERE id = ?", (unset_val, slot_id))
        await db.commit()
    
    await state.clear()
    await callback.answer("✅ Slot golit!", show_alert=True)
    
    # Refresh to address management view
    # Redirect to admin_addresses
    from handlers.admin import cb_admin_actions
    # Modify callback's data through model_copy because pydantic v2 freezes instances
    await cb_admin_actions(callback.model_copy(update={"data": "admin_addresses"}), state)

@router.message(AdminAddress.waiting_for_address)
async def process_new_address(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    new_addr = message.text.strip()
    if len(new_addr) < 20:
        await message.answer("❌ Adresa pare invalidă. Te rugăm să trimiți o adresă LTC validă.")
        return
        
    data = await state.get_data()
    slot_id = data.get('edit_slot_id')
    
    async with aiosqlite.connect(DB_PATH) as db:
        # Check for unique constraint manually to give user-friendly error
        async with db.execute("SELECT id FROM addresses WHERE crypto_address = ? AND id != ?", (new_addr, slot_id)) as cursor:
            existing = await cursor.fetchone()
            if existing:
                await message.answer("❌ Această adresă este deja folosită în alt slot!")
                return
                
        await db.execute("UPDATE addresses SET crypto_address = ?, in_use_by_sale_id = NULL, locked_until = NULL WHERE id = ?", (new_addr, slot_id))
        await db.commit()
        
    await message.answer(f"✅ Slotul #{slot_id} a fost actualizat cu succes!\n\nNoua adresă: <code>{new_addr}</code>")
    await state.clear()
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Înapoi la Adrese", callback_data="admin_addresses")]])
    await message.answer("Ce dorești să faci în continuare?", reply_markup=kb)

# ===== SUPPORT TICKETS REPLY =====

@router.callback_query(F.data.startswith("adm_reply_sup_"))
async def cb_admin_reply_ticket(callback: CallbackQuery, state: FSMContext):
    # Separate check for is_admin since it's a manual check here
    from config import ADMIN_IDS
    if callback.from_user.id not in ADMIN_IDS: return
    
    parts = callback.data.split("_")
    target_user_id = int(parts[3])
    sale_id = int(parts[4])
    
    await state.update_data(target_user_id=target_user_id, support_sale_id=sale_id)
    await state.set_state(AdminReplyState.waiting_for_reply)
    await callback.message.answer(f"💬 <b>RĂSPUNS SUPORT (# {sale_id})</b>\nTrimite mesajul de răspuns pentru client:")
    await callback.answer()

@router.message(AdminReplyState.waiting_for_reply)
async def process_admin_support_reply(message: Message, state: FSMContext):
    from config import ADMIN_IDS
    if message.from_user.id not in ADMIN_IDS: return
    
    data = await state.get_data()
    target_id = data.get("target_user_id")
    sale_id = data.get("support_sale_id")
    reply_text = message.text.strip() if message.text else None
    
    if not reply_text: return await message.answer("Trimite un mesaj text ca răspuns.")
    
    try:
        user_notif = (
            f"📩 <b>RĂSPUNS SUPORT (Comanda #{sale_id})</b>\n\n"
            f"{reply_text}"
        )
        await message.bot.send_message(target_id, user_notif)
        await message.answer(f"✅ Răspuns trimis cu succes către ID {target_id}!")
    except Exception as e:
        await message.answer(f"❌ Nu am putut trimite mesajul: {e}")
        
    await state.clear()
