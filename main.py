import asyncio
import logging
import sys
import uvicorn
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from database import init_db, ensure_5_slots
from config import BOT_TOKEN
from web_dashboard import app as web_app
from handlers.user import router as user_router
from handlers.admin import router as admin_router

# --- BOT SETUP ---
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from database import log_activity
from config import ADMIN_IDS, BOT_TOKEN

# Admin-only patterns to NEVER log (internal commands, management buttons)
_BLACKLIST_PREFIXES = (
    'adm_', 'admin_', 'silent_', 'silent',
    '/admin', '/silent', '/check',
    '/pending', '/all', '/info', '/setdropwallet', '/specialdrop', '/link',
)

class ActivityMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        result = await handler(event, data)
        
        try:
            user = None
            activity_text = None
            if isinstance(event, Message):
                user = event.from_user
                # Skip admins entirely — they run the dashboard
                if user.id in ADMIN_IDS:
                    return result
                text = event.text or ''
                # Skip blacklisted commands
                if any(text.startswith(p) for p in _BLACKLIST_PREFIXES):
                    return result
                activity_text = f"Mesaj: {text[:60]}" if text else "[Media]"
                
            elif isinstance(event, CallbackQuery):
                user = event.from_user
                if user.id in ADMIN_IDS:
                    return result
                cb = event.data or ''
                # Skip admin callback patterns
                if any(cb.startswith(p) for p in _BLACKLIST_PREFIXES):
                    return result
                # Resolve category/item IDs to human names
                activity_text = await _resolve_callback_label(cb)
                
            if user and activity_text:
                asyncio.create_task(_log_and_cache_user(user, activity_text))
        except Exception as e:
            logging.warning(f"ActivityMiddleware error: {e}")
            
        return result

async def _resolve_callback_label(cb: str) -> str:
    """Converts technical callback data to a human-readable Romanian label."""
    if not cb: return "Buton: [Fără date]"
    
    from database import DB_PATH
    import aiosqlite as _aio
    
    # Category viewing
    if cb.startswith('shop_cat_'):
        try:
            parts = cb.split('_')
            cat_id = parts[2] if len(parts) > 2 else None
            if cat_id:
                async with _aio.connect(DB_PATH) as db:
                    row = await (await db.execute("SELECT name FROM categories WHERE id = ?", (cat_id,))).fetchone()
                    if row: return f"🛍 Categorie: {row[0]}"
        except: pass
        return f"🛍 Categorie #{cb.split('_')[2]}" if '_' in cb else f"Buton: {cb}"
        
    # Item viewing
    if cb.startswith('shop_item_'):
        try:
            parts = cb.split('_')
            item_id = parts[2] if len(parts) > 2 else None
            if item_id:
                async with _aio.connect(DB_PATH) as db:
                    row = await (await db.execute("SELECT name FROM items WHERE id = ?", (item_id,))).fetchone()
                    if row: return f"📦 Produs: {row[0]}"
        except: pass
        return f"📦 Produs #{cb.split('_')[2]}" if '_' in cb else f"Buton: {cb}"

    # Other common patterns
    if cb == 'menu_shop': return "🛍 Deschide Magazin"
    if cb == 'menu_profile': return "👤 Profil"
    if cb == 'menu_support': return "💬 Suport"
    if cb == 'menu_main' or cb == 'menu_start': return "🏠 Meniu Principal"
    if cb.startswith('buy_item_'): return "🛒 Start Achiziție"
    if cb.startswith('verify_pay_'): return "✅ Verifică Plată"
    if cb.startswith('cancel_order_'): return "❌ Anulează Comandă"
    if cb.startswith('preorder_'): return "⏳ Precomandă"
    
    return f"Buton: {cb[:30]}"

async def _log_and_cache_user(user, activity_text: str):
    """Logs activity and fetches+caches the profile photo."""
    try:
        await log_activity(user.id, user.username, activity_text)
    except Exception as e:
        logging.error(f"Failed to log activity for {user.id}: {e}")
    
    # Cache profile photo if not already set
    try:
        from database import DB_PATH
        import aiosqlite as _aio
        async with _aio.connect(DB_PATH) as db:
            row = await (await db.execute(
                "SELECT profile_photo FROM users WHERE telegram_id = ?",
                (user.id,)
            )).fetchone()
        
        if row and not row[0] and _bot_ref:
            photos = await _bot_ref.get_user_profile_photos(user_id=user.id, limit=1)
            if photos and photos.total_count > 0:
                best = photos.photos[0][-1]  # largest size
                file_info = await _bot_ref.get_file(best.file_id)
                photo_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
                async with _aio.connect(DB_PATH) as db:
                    await db.execute(
                        "UPDATE users SET profile_photo = ? WHERE telegram_id = ?",
                        (photo_url, user.id)
                    )
                    await db.commit()
                logging.info(f"Cached profile photo for {user.id}")
    except Exception as e:
        logging.debug(f"Photo cache skip for {user.id}: {e}")

_bot_ref = None  # Will be set in start_bot

async def start_bot():
    """Logic to start the Telegram Bot polling."""
    global _bot_ref
    _bot_ref = bot
    await init_db()
    await ensure_5_slots()
    
    dp.message.middleware(ActivityMiddleware())
    dp.callback_query.middleware(ActivityMiddleware())
    
    # Inject bot into dashboard state for web interaction
    web_app.state.bot = bot
    
    dp.include_router(admin_router)
    dp.include_router(user_router)

    # Fixed global error handler signature for aiogram 3.x
    @dp.errors()
    async def global_error_handler(event):
        logging.error(f"Global Update Error: {event.exception}")
        return True # Swallow exception to keep bot alive

    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("🚀 Telegram Bot starting...")
    await dp.start_polling(bot)

async def run_serveo_tunnel(port: int):
    """Start an SSH tunnel to serveo.net and keep it alive."""
    import subprocess
    logging.info("🌍 Starting Serveo SSH tunnel...")
    process = await asyncio.create_subprocess_exec(
        "ssh", "-o", "StrictHostKeyChecking=no", "-R", f"80:127.0.0.1:{port}", "serveo.net",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT
    )
    
    # Read the first few lines to find the Serveo URL and log it
    while True:
        line = await process.stdout.readline()
        if not line:
            break
        decoded_line = line.decode('utf-8').strip()
        if 'Forwarding HTTP traffic from' in decoded_line:
            url = decoded_line.split("from")[1].strip()
            logging.info(f"✨ PUBLIC DASHBOARD URL: {url}")
            # Save the URL to DB for the /link command
            import aiosqlite
            from database import DB_PATH
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES ('dashboard_url', ?)", (url,))
                await db.commit()
        elif decoded_line:
            logging.info(f"Serveo: {decoded_line}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start the bot in the background when the web server starts
    bot_task = asyncio.create_task(start_bot())
    
    port = int(os.getenv("PORT", 8888))
    # Start the Serveo tunnel ONLY if not in production or explicitly requested
    tunnel_task = None
    if os.getenv("USE_SERVEO", "false").lower() == "true":
        tunnel_task = asyncio.create_task(run_serveo_tunnel(port))
    
    yield
    # Cleanup
    bot_task.cancel()
    if tunnel_task:
        tunnel_task.cancel()
    
    try:
        await bot_task
    except asyncio.CancelledError:
        pass
    
    if tunnel_task:
        try:
            await tunnel_task
        except asyncio.CancelledError:
            pass

# Wrap the existing web_app with lifespan
web_app.router.lifespan_context = lifespan

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    
    port = int(os.getenv("PORT", 8888))
    # Run the unified server
    logging.info(f"🌐 Dashboard available locally at http://localhost:{port}")
    uvicorn.run(web_app, host="0.0.0.0", port=port)
