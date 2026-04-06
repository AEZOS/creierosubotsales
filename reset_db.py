import asyncio
import psycopg
from database import db_session, init_db
import logging

# Standard standard values for conversion if no external API
RON_TO_LTC_RATE = 280.0 

async def reset_and_seed():
    print("🚀 Re-initializing Neon Database...")
    async with db_session() as db:
        async with db.cursor() as cursor:
            # We must drop tables in correct order or ignore errors
            tables = ["user_activity_logs", "stock_alerts", "reviews", "tickets", "preorders", "sales", "item_images", "items", "categories", "addresses", "bot_settings", "users"]
            for table in tables:
                try:
                    await cursor.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
                    print(f"Dropped {table}")
                except:
                    pass
        await db.commit()
    
    print("📜 Creating schemas and auto-seeding...")
    await init_db()
    
    # We can add more explicit seeding here if database.py is not enough
    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("SELECT COUNT(*) as count FROM items")
            row = await cursor.fetchone()
            print(f"✅ Seeding finished. Total items in DB: {row['count']}")
            
    print("\n✨ Neon Database Reset & Seeding Complete!")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(reset_and_seed())
