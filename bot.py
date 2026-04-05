import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from database import init_db, seed_addresses
from config import BOT_TOKEN, LTC_ADDRESSES
    
# Configure logging
logging.basicConfig(level=logging.INFO, stream=sys.stdout)

# Initialize Bot and Dispatcher
# Using DefaultBotProperties for the newer aiogram versions
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

from handlers.user import router as user_router
from handlers.admin import router as admin_router

async def main():
    # Initialize the database
    await init_db()
    
    from database import ensure_5_slots
    await ensure_5_slots()
    
    dp.include_router(admin_router)
    dp.include_router(user_router)

    # Fixed global error handler signature for aiogram 3.x
    @dp.errors()
    async def global_error_handler(event):
        logging.error(f"Global Update Error: {event.exception}")
        return True # Swallow exception to keep bot alive
    
    # Start polling
    logging.info("Starting bot...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped!")
