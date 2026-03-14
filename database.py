import aiosqlite
import logging

DB_PATH = "bot_database.sqlite"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("PRAGMA busy_timeout=5000") # 5 seconds
        
        # Users table
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE,
                username TEXT,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Categories table (Max 9 logically handled in code)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                display_image TEXT DEFAULT NULL,
                description TEXT DEFAULT NULL
            )
        ''')

        # Items table
        await db.execute('''
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id INTEGER,
                name TEXT,
                description TEXT,
                price_ron REAL,
                price_ltc REAL,
                display_image TEXT DEFAULT NULL,
                FOREIGN KEY (category_id) REFERENCES categories (id)
            )
        ''')

        # Item Images / Stock table (Now supports multiple media per secret)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS item_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER,
                image_url TEXT,
                media_type TEXT DEFAULT 'photo', -- 'photo', 'video', 'text'
                secret_group TEXT DEFAULT NULL,   -- Groups multiple items into one 'secret'
                is_sold BOOLEAN DEFAULT 0,
                FOREIGN KEY (item_id) REFERENCES items (id)
            )
        ''')
        
        # Sales Table
        await db.execute('''
            CREATE TABLE IF NOT EXISTS sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                item_id INTEGER,
                image_id INTEGER DEFAULT NULL,
                amount_expected REAL,
                amount_paid REAL DEFAULT 0,
                address_used TEXT,
                tx_hash TEXT UNIQUE DEFAULT NULL,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (item_id) REFERENCES items (id),
                FOREIGN KEY (image_id) REFERENCES item_images (id)
            )
        ''')
        
        # Addresses Pool Table
        await db.execute('''
            CREATE TABLE IF NOT EXISTS addresses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                crypto_address TEXT UNIQUE,
                in_use_by_sale_id INTEGER DEFAULT NULL,
                locked_until TIMESTAMP DEFAULT NULL
            )
        ''')

        # Preorders Table
        await db.execute('''
            CREATE TABLE IF NOT EXISTS preorders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                item_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (item_id) REFERENCES items (id)
            )
        ''')

        # Migrations
        try:
            await db.execute("ALTER TABLE categories ADD COLUMN description TEXT DEFAULT NULL")
        except: pass
        try:
            await db.execute("ALTER TABLE sales ADD COLUMN tx_hash TEXT UNIQUE DEFAULT NULL")
        except: pass
        try:
            await db.execute("ALTER TABLE item_images ADD COLUMN media_type TEXT DEFAULT 'photo'")
        except: pass
        try:
            await db.execute("ALTER TABLE item_images ADD COLUMN secret_group TEXT DEFAULT NULL")
        except: pass

        
        await db.commit()

        logging.info("Database initialized successfully.")

# --- Repository functions ---

async def get_and_create_sale(user_tg_id: int, item_id: int, base_amount: float, timeout_minutes: int):
    """
    Finds an address and creates a pending sale. 
    If all addresses are "locked", it reuses one but adds a small increment (0.0001 LTC) 
    to the amount to stay unique.
    Returns (address, final_amount, sale_id).
    """
    from datetime import datetime, timedelta
    now = datetime.now()
    now_str = now.strftime('%Y-%m-%d %H:%M:%S')
    expires_at = now + timedelta(minutes=timeout_minutes)
    expires_str = expires_at.strftime('%Y-%m-%d %H:%M:%S')

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        
        # 1. Get all addresses
        async with db.execute("SELECT crypto_address FROM addresses") as cursor:
            all_addrs = await cursor.fetchall()
            
        if not all_addrs:
            return None, None, None

        # 2. Find the "best" address (the one with fewest active PENDING sales)
        # We also check which addresses are truly free (no paid/confirming/pending sales currently active there)
        # Actually, let's just count active sales (status IN ('pending', 'confirming'))
        addr_usage = {}
        for row in all_addrs:
            addr = row['crypto_address']
            async with db.execute("""
                SELECT COUNT(*) FROM sales 
                WHERE address_used = ? AND status IN ('pending', 'confirming')
            """, (addr,)) as cnt_cursor:
                count = (await cnt_cursor.fetchone())[0]
                addr_usage[addr] = count

        # Sort addresses by usage count, pick the lowest
        sorted_addrs = sorted(addr_usage.items(), key=lambda x: x[1])
        address, current_active_on_addr = sorted_addrs[0]

        # 3. Calculate final amount: base_amount + (offset * 0.0001)
        # To be extra safe, we'll check if any other active sale on this address HAS this exact amount
        offset = 0
        while True:
            final_amount = round(base_amount + (offset * 0.0001), 5)
            async with db.execute("""
                SELECT 1 FROM sales 
                WHERE address_used = ? AND amount_expected = ? AND status IN ('pending', 'confirming')
            """, (address, final_amount)) as ex_cursor:
                if not await ex_cursor.fetchone():
                    break
                offset += 1

        # 4. Create Sale
        cursor = await db.execute("""
            INSERT INTO sales (user_id, item_id, amount_expected, address_used, created_at, status) 
            VALUES ((SELECT id FROM users WHERE telegram_id=?), ?, ?, ?, ?, 'pending')
        """, (user_tg_id, item_id, final_amount, address, now_str))
        sale_id = cursor.lastrowid

        # 5. Lock Address record (optional since we multiplex now, but good for tracking)
        await db.execute("""
            UPDATE addresses SET in_use_by_sale_id = ?, locked_until = ? 
            WHERE crypto_address = ?
        """, (sale_id, expires_str, address))
        
        await db.commit()
        return address, final_amount, sale_id

async def seed_addresses(addresses_list: list):
    async with aiosqlite.connect(DB_PATH) as db:
        for addr in addresses_list:
            await db.execute("INSERT OR IGNORE INTO addresses (crypto_address) VALUES (?)", (addr,))
        await db.commit()

async def add_user(telegram_id: int, username: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (telegram_id, username) VALUES (?, ?)",
            (telegram_id, username)
        )
        await db.commit()


