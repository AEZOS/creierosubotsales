import psycopg
from psycopg.rows import dict_row
import logging
import os
import asyncio
from config import DATABASE_URL, DB_PATH

# ⚡ Neon Persistence Helper
async def get_db_conn():
    """Returns an async connection to Postgres (Neon) or SQLite (LOCAL fallback)."""
    if DATABASE_URL:
        # PostgreSQL (Neon) 
        return await psycopg.AsyncConnection.connect(DATABASE_URL, row_factory=dict_row)
    else:
        # Local SQLite (Development only, no persistence on Render free)
        import aiosqlite
        conn = await aiosqlite.connect(DB_PATH)
        conn.row_factory = aiosqlite.Row
        return conn

from contextlib import asynccontextmanager

@asynccontextmanager
async def db_session():
    """Context manager for database sessions, compatible with both Postgres and SQLite."""
    conn = await get_db_conn()
    try:
        async with conn:
            yield conn
    finally:
        await conn.close()

async def init_db():
    conn = await get_db_conn()
    async with conn:
        # Settings table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        
        # Users table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE,
                username TEXT,
                profile_photo TEXT DEFAULT NULL,
                last_activity TEXT DEFAULT NULL,
                last_activity_at TIMESTAMP DEFAULT NULL,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Categories table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS categories (
                id SERIAL PRIMARY KEY,
                name TEXT UNIQUE,
                display_image TEXT DEFAULT NULL,
                description TEXT DEFAULT NULL,
                dedicated_address TEXT DEFAULT NULL,
                is_hidden BOOLEAN DEFAULT FALSE
            )
        ''')

        # Items table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS items (
                id SERIAL PRIMARY KEY,
                category_id INTEGER,
                name TEXT,
                description TEXT,
                price_ron REAL,
                price_ltc REAL,
                display_image TEXT DEFAULT NULL,
                dedicated_address TEXT DEFAULT NULL,
                is_hidden BOOLEAN DEFAULT FALSE,
                FOREIGN KEY (category_id) REFERENCES categories (id)
            )
        ''')

        # Item Images / Stock table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS item_images (
                id SERIAL PRIMARY KEY,
                item_id INTEGER,
                image_url TEXT,
                media_type TEXT DEFAULT 'photo',
                caption TEXT DEFAULT NULL,
                secret_group TEXT DEFAULT NULL,
                is_sold BOOLEAN DEFAULT FALSE,
                FOREIGN KEY (item_id) REFERENCES items (id)
            )
        ''')
        
        # Sales Table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS sales (
                id SERIAL PRIMARY KEY,
                user_id INTEGER,
                item_id INTEGER,
                image_id INTEGER DEFAULT NULL,
                amount_expected REAL,
                amount_paid REAL DEFAULT 0,
                address_used TEXT,
                tx_hash TEXT UNIQUE DEFAULT NULL,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP DEFAULT NULL,
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (item_id) REFERENCES items (id),
                FOREIGN KEY (image_id) REFERENCES item_images (id)
            )
        ''')
        
        # Addresses Pool Table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS addresses (
                id SERIAL PRIMARY KEY,
                crypto_address TEXT UNIQUE,
                in_use_by_sale_id INTEGER DEFAULT NULL,
                locked_until TIMESTAMP DEFAULT NULL,
                last_tx_hash TEXT DEFAULT NULL,
                last_amount REAL DEFAULT NULL
            )
        ''')
        
        # Preorders Table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS preorders (
                id SERIAL PRIMARY KEY,
                user_id INTEGER,
                item_id INTEGER,
                status TEXT DEFAULT 'pending',
                notified BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (item_id) REFERENCES items (id)
            )
        ''')

        # Support Tickets table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS tickets (
                id SERIAL PRIMARY KEY,
                user_id INTEGER,
                sale_id INTEGER,
                original_msg_id INTEGER,
                is_closed BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (sale_id) REFERENCES sales (id)
            )
        ''')
        
        # Reviews Table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS reviews (
                id SERIAL PRIMARY KEY,
                sale_id INTEGER UNIQUE,
                user_id INTEGER,
                rating INTEGER,
                comment TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (sale_id) REFERENCES sales (id),
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')

        # Activity Logs
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS user_activity_logs (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT,
                activity TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Stock Alerts
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS stock_alerts (
                id SERIAL PRIMARY KEY,
                user_id INTEGER,
                item_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, item_id),
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (item_id) REFERENCES items (id)
            )
        ''')

        await conn.commit()

        # Auto-seed if empty
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT COUNT(*) as count FROM categories")
            count = (await cursor.fetchone())['count']
        
        if count == 0:
            logging.info("🌱 Database empty. Seeding initial categories and items...")
            
            categories_data = [
                {"id": 1, "emoji": "❄️", "desc": "Super-flakey. Pentru zilele când vrei să fii fresh ca zăpada de munte.", "img": "assets/cocos.jpg"},
                {"id": 2, "emoji": "🐎", "desc": "Putere de cal. Pentru zilele în care vrei să treci de usi...", "img": "assets/kaluti.jpg"},
                {"id": 3, "emoji": "☘️", "desc": "Verdura maximă, top gazon.. ca sa ai Creier fresh toată ziua!", "img": "assets/gazon.jpg"},
                {"id": 4, "emoji": "🍾", "desc": "Vibrație de câștigător. Porți medeul, porți după tine.", "img": "assets/medalion.jpg"},
                {"id": 5, "emoji": "🍬", "desc": "Dreamer Diamonds. Mica, impact uriaș.", "img": "assets/bb.jpg"},
                {"id": 6, "emoji": "🏃", "desc": "Turbo ON. Când ceilalți se trezesc, tu... dai in ele, continui.. si continui...", "img": "assets/viteza.jpg"},
                {"id": 7, "emoji": "🍫", "desc": "Chocolate: Gust intens, plăcere garantată.", "img": "assets/shop.png"},
                {"id": 8, "emoji": "🔮", "desc": "Fara control, sudiaza-te...", "img": "assets/carton.jpg"},
                {"id": 9, "emoji": "💎", "desc": "Vezi în 4K. Decizii bune, prieteni mai putini, da altfel.", "img": "assets/cristi.jpg"}
            ]

            for cat in categories_data:
                await conn.execute(
                    "INSERT INTO categories (id, name, description, display_image) VALUES (%s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                    (cat["id"], cat["emoji"], cat["desc"], cat["img"])
                )

            # Standard Item Lists (Qty, RON)
            items_payload = {
                1: [(1, 500), (2, 900), (5, 2000), (10, 3650), (20, 7000)], # COCOS
                2: [(1, 200), (2, 300), (5, 600), (10, 1000), (30, 2400), (50, 3500), (100, 6000)], # KALUTI
                3: [(2, 100), (5, 200), (10, 375), (20, 700), (30, 1000), (50, 1500), (100, 2800)], # GAZON
                4: [(1, 200), (2, 300), (5, 600), (10, 1000), (30, 2400), (50, 3500), (100, 6000)], # MEDALION
                5: [(2, 100), (5, 200), (10, 375), (20, 700), (30, 1000), (50, 1400), (100, 2500)], # BB
                6: [(1, 100), (2, 200), (5, 400), (10, 700), (20, 1200), (30, 1600), (50, 2250), (100, 3700)], # VITEZA
                7: [(2, 100), (5, 225), (10, 400), (20, 700), (30, 1000), (50, 1500), (100, 2750)], # CHOCOLATE
                8: [(1, 100), (5, 400), (10, 700), (20, 1200), (30, 1500), (50, 2250), (100, 3250)], # CRISTAL
                9: [(1, 200), (2, 300), (5, 600), (10, 1000), (30, 2400), (50, 3000), (100, 5000)]  # DIAMANT
            }

            emoji_map = {c["id"]: c["emoji"] for c in categories_data}
            for cat_id, prices in items_payload.items():
                emoji = emoji_map[cat_id]
                for qty, price_ron in prices:
                    item_name = f"{emoji} {qty} = {price_ron} RON"
                    price_ltc = round(price_ron / 250.0, 4)
                    await conn.execute(
                        "INSERT INTO items (category_id, name, description, price_ron, price_ltc) VALUES (%s, %s, %s, %s, %s)",
                        (cat_id, item_name, f"Calitate premium garantată pentru {item_name}.", float(price_ron), price_ltc)
                    )
            
            await conn.commit()
            logging.info("✅ Database seeded with 9 emoji categories and all items.")

        await ensure_5_slots(conn)
        logging.info("Database initialized successfully.")

async def ensure_5_slots(conn):
    """Maintain exactly 5 rows in the addresses table."""
    async with conn.cursor() as cursor:
        await cursor.execute("DELETE FROM addresses WHERE crypto_address = 'LWfgoZoeHQqyCf7MX5mLNp41o2vuEaEyT7'")
        await cursor.execute("SELECT id FROM addresses ORDER BY id ASC")
        rows = await cursor.fetchall()
        count = len(rows)
        if count > 5:
            for i in range(5, count):
                await cursor.execute("DELETE FROM addresses WHERE id = %s", (rows[i]['id'],))
        elif count < 5:
            for i in range(5 - count):
                await cursor.execute("INSERT INTO addresses (crypto_address) VALUES (%s) ON CONFLICT DO NOTHING", (f"UNSET_SLOT_NEW_{i+count+1}",))
    await conn.commit()

async def get_and_create_sale(user_tg_id: int, item_id: int, base_amount: float, timeout_minutes: int):
    from datetime import datetime, timedelta
    now = datetime.now()
    expires_at = now + timedelta(minutes=timeout_minutes)

    conn = await get_db_conn()
    async with conn:
        try:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT i.dedicated_address as item_addr, c.dedicated_address as cat_addr 
                    FROM items i
                    JOIN categories c ON i.category_id = c.id
                    WHERE i.id = %s
                """, (item_id,))
                dedi_row = await cur.fetchone()
                
                dedicated_address = (dedi_row['item_addr'] or dedi_row['cat_addr']) if dedi_row else None
                
                if dedicated_address:
                    address = dedicated_address
                    await cur.execute("""
                        SELECT amount_expected FROM sales 
                        WHERE address_used = %s AND status = 'pending'
                    """, (address,))
                    active_rows = await cur.fetchall()
                    final_amount = round(base_amount, 5)
                    used_amounts = {round(r['amount_expected'], 5) for r in active_rows}
                    while final_amount in used_amounts:
                        final_amount = round(final_amount + 0.0001, 5)
                else:
                    await cur.execute("""
                        SELECT crypto_address FROM addresses 
                        WHERE crypto_address NOT LIKE 'UNSET_SLOT_%%'
                          AND in_use_by_sale_id IS NULL
                          AND (locked_until IS NULL OR locked_until < %s)
                        ORDER BY locked_until ASC
                    """, (now,))
                    row = await cur.fetchone()
                    if not row:
                        return None, None, None
                    address = row['crypto_address']
                    final_amount = round(base_amount, 5)

                await cur.execute("""
                    INSERT INTO sales (user_id, item_id, amount_expected, address_used, created_at, status) 
                    VALUES ((SELECT id FROM users WHERE telegram_id=%s), %s, %s, %s, %s, 'pending')
                    RETURNING id
                """, (user_tg_id, item_id, final_amount, address, now))
                sale_id = (await cur.fetchone())['id']

                await cur.execute("""
                    UPDATE addresses SET in_use_by_sale_id = %s, locked_until = %s 
                    WHERE crypto_address = %s
                """, (sale_id, expires_at, address))
                
                await conn.commit()
                return address, final_amount, sale_id
        except Exception as e:
            logging.error(f"Error in get_and_create_sale: {e}")
            return None, None, None

async def seed_addresses(addresses_list: list):
    conn = await get_db_conn()
    async with conn:
        for addr in addresses_list:
            await conn.execute("INSERT INTO addresses (crypto_address) VALUES (%s) ON CONFLICT (crypto_address) DO NOTHING", (addr,))
        await conn.commit()

async def add_user(telegram_id: int, username: str):
    conn = await get_db_conn()
    async with conn:
        await conn.execute(
            "INSERT INTO users (telegram_id, username) VALUES (%s, %s) ON CONFLICT (telegram_id) DO UPDATE SET username = EXCLUDED.username",
            (telegram_id, username)
        )
        await conn.commit()

async def log_activity(telegram_id: int, username: str, activity_text: str):
    from datetime import datetime
    now = datetime.now()
    conn = await get_db_conn()
    async with conn:
        await conn.execute(
            "INSERT INTO user_activity_logs (telegram_id, activity, created_at) VALUES (%s, %s, %s)",
            (telegram_id, activity_text, now)
        )
        await conn.execute(
            "UPDATE users SET last_activity = %s, last_activity_at = %s, username = COALESCE(%s, username) WHERE telegram_id = %s",
            (activity_text, now, username, telegram_id)
        )
        await conn.commit()

async def is_silent_mode():
    conn = await get_db_conn()
    async with conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT value FROM bot_settings WHERE key = 'silent_mode'")
            row = await cur.fetchone()
            return row and row['value'] == 'on'

async def set_silent_mode(enabled: bool):
    val = 'on' if enabled else 'off'
    conn = await get_db_conn()
    async with conn:
        await conn.execute("INSERT INTO bot_settings (key, value) VALUES ('silent_mode', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (val,))
        await conn.commit()

async def is_blackmagic_on():
    conn = await get_db_conn()
    async with conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT value FROM bot_settings WHERE key = 'blackmagic'")
            row = await cur.fetchone()
            return row and row['value'] == 'on'

async def set_blackmagic(enabled: bool):
    val = 'on' if enabled else 'off'
    conn = await get_db_conn()
    async with conn:
        await conn.execute("INSERT INTO bot_settings (key, value) VALUES ('blackmagic', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (val,))
        await conn.commit()

async def cleanup_completed_orders():
    conn = await get_db_conn()
    async with conn:
        await conn.execute("""
            UPDATE item_images 
            SET is_sold = FALSE 
            WHERE id IN (
                SELECT img.id 
                FROM item_images img
                JOIN item_images img_ref ON (img.secret_group = img_ref.secret_group OR img.id = img_ref.id)
                WHERE img_ref.id IN (SELECT image_id FROM sales WHERE status IN ('completed', 'paid', 'delivered'))
            )
        """)
        await conn.execute("DELETE FROM sales WHERE status IN ('completed', 'paid', 'delivered')")
        await conn.commit()

async def get_last_completed_sales(limit=5):
    conn = await get_db_conn()
    async with conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT s.id, i.name as item_name, s.amount_expected, s.created_at, u.username, s.status, s.image_id
                FROM sales s
                JOIN items i ON s.item_id = i.id
                JOIN users u ON s.user_id = u.id
                WHERE s.status IN ('completed', 'paid', 'delivered')
                ORDER BY s.created_at DESC
                LIMIT %s
            """, (limit,))
            return await cur.fetchall()

async def restore_secret_and_delete_sale(sale_id: int):
    conn = await get_db_conn()
    async with conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT image_id FROM sales WHERE id = %s", (sale_id,))
            row = await cur.fetchone()
            if row and row['image_id']:
                image_id = row['image_id']
                await cur.execute("""
                    UPDATE item_images 
                    SET is_sold = FALSE 
                    WHERE secret_group = (SELECT secret_group FROM item_images WHERE id = %s)
                       OR id = %s
                """, (image_id, image_id))
        await conn.execute("DELETE FROM sales WHERE id = %s", (sale_id,))
        await conn.commit()

async def get_item_stats(item_id):
    conn = await get_db_conn()
    async with conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT COUNT(*) as count FROM sales WHERE item_id = %s AND status = 'paid'", (item_id,))
            total_bought = (await cur.fetchone())['count']
            await cur.execute("""
                SELECT u.username, u.telegram_id, COUNT(s.id) as count
                FROM sales s
                JOIN users u ON s.user_id = u.id
                WHERE s.item_id = %s AND s.status = 'paid'
                GROUP BY u.username, u.telegram_id
                ORDER BY count DESC
                LIMIT 1
            """, (item_id,))
            best_buyer = await cur.fetchone()
            await cur.execute("SELECT name FROM items WHERE id = %s", (item_id,))
            name_row = await cur.fetchone()
            item_name = name_row['name'] if name_row else "Unknown"
            await cur.execute("""
                SELECT 
                    (SELECT COUNT(DISTINCT secret_group) FROM item_images WHERE item_id = %s AND is_sold = FALSE AND secret_group IS NOT NULL) +
                    (SELECT COUNT(*) FROM item_images WHERE item_id = %s AND is_sold = FALSE AND secret_group IS NULL) as stock
            """, (item_id, item_id))
            current_stock = (await cur.fetchone())['stock']
            return item_name, total_bought, best_buyer, current_stock

async def get_user_total_sales(telegram_id):
    conn = await get_db_conn()
    async with conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT COUNT(*) as count FROM sales 
                WHERE user_id = (SELECT id FROM users WHERE telegram_id = %s) 
                  AND status IN ('paid', 'completed')
            """, (telegram_id,))
            row = await cur.fetchone()
            return row['count'] if row else 0
