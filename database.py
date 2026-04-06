import aiosqlite
import logging
import os
from config import DB_PATH

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("PRAGMA busy_timeout=5000") # 5 seconds
        
        # Settings table
        await db.execute('''
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        
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
                description TEXT DEFAULT NULL,
                dedicated_address TEXT DEFAULT NULL,
                is_hidden BOOLEAN DEFAULT 0 -- For toggleable 'special' categories
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
                dedicated_address TEXT DEFAULT NULL, -- Overrides category's address
                is_hidden BOOLEAN DEFAULT 0,         -- For secret drop items
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
                caption TEXT DEFAULT NULL,       -- Caption for images/videos
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
                locked_until TIMESTAMP DEFAULT NULL,
                last_tx_hash TEXT DEFAULT NULL,
                last_amount REAL DEFAULT NULL
            )
        ''')
        
        # Migrations for existing DB
        try:
            await db.execute("ALTER TABLE addresses ADD COLUMN last_tx_hash TEXT DEFAULT NULL")
        except: pass
        try:
            await db.execute("ALTER TABLE addresses ADD COLUMN last_amount REAL DEFAULT NULL")
        except: pass

        # Preorders Table
        await db.execute('''
            CREATE TABLE IF NOT EXISTS preorders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                item_id INTEGER,
                status TEXT DEFAULT 'pending', -- 'pending', 'confirmed'
                notified BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (item_id) REFERENCES items (id)
            )
        ''')

        # Support Tickets table
        await db.execute('''
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                sale_id INTEGER,
                original_msg_id INTEGER, -- User's first msg id (for tracking)
                is_closed BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (sale_id) REFERENCES sales (id)
            )
        ''')
        
        # Migrations
        try:
            await db.execute("ALTER TABLE sales ADD COLUMN completed_at TIMESTAMP DEFAULT NULL")
        except: pass

        # Reviews Table
        await db.execute('''
            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sale_id INTEGER UNIQUE,
                user_id INTEGER,
                rating INTEGER,
                comment TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (sale_id) REFERENCES sales (id),
                FOREIGN KEY (user_id) REFERENCES users (id)
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
        try:
            await db.execute("ALTER TABLE item_images ADD COLUMN caption TEXT DEFAULT NULL")
        except: pass
        try:
            await db.execute("ALTER TABLE preorders ADD COLUMN status TEXT DEFAULT 'pending'")
        except: pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN last_activity TEXT DEFAULT NULL")
        except: pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN last_activity_at TIMESTAMP DEFAULT NULL")
        except: pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN profile_photo TEXT DEFAULT NULL")
        except: pass

        await db.execute('''
            CREATE TABLE IF NOT EXISTS user_activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER,
                activity TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        await db.execute('''
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        
        # Performance Indexes
        await db.execute("CREATE INDEX IF NOT EXISTS idx_sales_user_status ON sales(user_id, status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_sales_item ON sales(item_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_item_images_item_sold ON item_images(item_id, is_sold)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_items_cat ON items(category_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_preorders_status ON preorders(status)")
        
        # Purge admin activity and blacklisted commands from logs on every boot
        from config import ADMIN_IDS
        _blacklist_patterns = [
            '%adm_%', '%admin_%', '%/admin%', '%/silent%',
            '%/pending%', '%/all%', '%/info%', '%/setdropwallet%', '%/specialdrop%',
            '%/link%', '%/check%'
        ]
        if ADMIN_IDS:
            placeholders = ','.join(['?' for _ in ADMIN_IDS])
            await db.execute(f"DELETE FROM user_activity_logs WHERE telegram_id IN ({placeholders})", ADMIN_IDS)
            await db.execute(f"UPDATE users SET last_activity = NULL, last_activity_at = NULL WHERE telegram_id IN ({placeholders})", ADMIN_IDS)
        for pattern in _blacklist_patterns:
            await db.execute("DELETE FROM user_activity_logs WHERE activity LIKE ?", (pattern,))

        await db.execute('''
            CREATE TABLE IF NOT EXISTS stock_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                item_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, item_id),
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (item_id) REFERENCES items (id)
            )
        ''')
        
        # Migrations
        try:
            await db.execute("ALTER TABLE categories ADD COLUMN dedicated_address TEXT DEFAULT NULL")
        except: pass
        try:
            await db.execute("ALTER TABLE items ADD COLUMN dedicated_address TEXT DEFAULT NULL")
        except: pass
        try:
            await db.execute("ALTER TABLE categories ADD COLUMN is_hidden BOOLEAN DEFAULT 0")
        except: pass
        try:
            await db.execute("ALTER TABLE items ADD COLUMN is_hidden BOOLEAN DEFAULT 0")
        except: pass

        # Auto-seed if empty
        async with db.execute("SELECT COUNT(*) FROM categories") as cursor:
            count = (await cursor.fetchone())[0]
        
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
                await db.execute(
                    "INSERT INTO categories (id, name, description, display_image) VALUES (?, ?, ?, ?)",
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
                    price_ltc = round(price_ron / 250.0, 4) # Default static conversion for seeding
                    await db.execute(
                        "INSERT INTO items (category_id, name, description, price_ron, price_ltc) VALUES (?, ?, ?, ?, ?)",
                        (cat_id, item_name, f"Calitate premium garantată pentru {item_name}.", float(price_ron), price_ltc)
                    )
            
            await db.commit()
            logging.info("✅ Database seeded with 9 emoji categories and all items.")

        await db.commit()
        await ensure_5_slots()

        logging.info("Database initialized successfully.")

async def ensure_5_slots():
    """Maintain exactly 5 rows in the addresses table."""
    async with aiosqlite.connect(DB_PATH) as db:
        # 1. Explicitly remove the deprecated hardcoded address
        await db.execute("DELETE FROM addresses WHERE crypto_address = 'LWfgoZoeHQqyCf7MX5mLNp41o2vuEaEyT7'")
        await db.commit()

        # 2. Check total row count
        async with db.execute("SELECT id FROM addresses ORDER BY id ASC") as cursor:
            rows = await cursor.fetchall()
        
        count = len(rows)
        # 3. Handle excess slots
        if count > 5:
            for i in range(5, count):
                await db.execute("DELETE FROM addresses WHERE id = ?", (rows[i][0],))
            await db.commit()
        # 4. Handle missing slots
        elif count < 5:
            for i in range(5 - count):
                # Try to use a clean name
                await db.execute("INSERT INTO addresses (crypto_address) VALUES (?)", (f"UNSET_SLOT_NEW_{i+1}",))
            await db.commit()

# --- Repository functions ---

async def get_and_create_sale(user_tg_id: int, item_id: int, base_amount: float, timeout_minutes: int):
    """
    Finds an address and creates a pending sale. 
    Supports dedicated addresses for items/categories with amount incrementing for uniqueness.
    Returns (address, final_amount, sale_id).
    """
    from datetime import datetime, timedelta
    now = datetime.now()
    now_str = now.strftime('%Y-%m-%d %H:%M:%S')
    expires_at = now + timedelta(minutes=timeout_minutes)
    expires_str = expires_at.strftime('%Y-%m-%d %H:%M:%S')

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN IMMEDIATE")
        try:
            # 0. Check for dedicated address (Item first, then Category)
            async with db.execute("""
                SELECT i.dedicated_address as item_addr, c.dedicated_address as cat_addr 
                FROM items i
                JOIN categories c ON i.category_id = c.id
                WHERE i.id = ?
            """, (item_id,)) as cursor:
                dedi_row = await cursor.fetchone()
            
            dedicated_address = (dedi_row['item_addr'] or dedi_row['cat_addr']) if dedi_row else None
            
            if dedicated_address:
                address = dedicated_address
                # Handle unique amount increments for active sales on this fixed address
                async with db.execute("""
                    SELECT amount_expected FROM sales 
                    WHERE address_used = ? AND status = 'pending'
                """, (address,)) as cursor:
                    active_rows = await cursor.fetchall()
                
                final_amount = round(base_amount, 5)
                used_amounts = {round(r[0], 5) for r in active_rows}
                while final_amount in used_amounts:
                    final_amount = round(final_amount + 0.0001, 5)
            else:
                # 1. Standard 5-slot rotation logic
                async with db.execute("""
                    SELECT crypto_address FROM addresses 
                    WHERE crypto_address NOT LIKE 'UNSET_SLOT_%'
                      AND in_use_by_sale_id IS NULL
                      AND (locked_until IS NULL OR locked_until < ?)
                    ORDER BY locked_until ASC
                """, (now_str,)) as cursor:
                    row = await cursor.fetchone()
                    
                if not row:
                    logging.warning("No free LTC addresses available (all in use or cooldown)")
                    await db.execute("ROLLBACK")
                    return None, None, None

                address = row['crypto_address']
                final_amount = round(base_amount, 5)

            # 2. Create Sale
            cursor = await db.execute("""
                INSERT INTO sales (user_id, item_id, amount_expected, address_used, created_at, status) 
                VALUES ((SELECT id FROM users WHERE telegram_id=?), ?, ?, ?, ?, 'pending')
            """, (user_tg_id, item_id, final_amount, address, now_str))
            sale_id = cursor.lastrowid

            # 3. Mark Address as in use (if it's one of the managed slots)
            await db.execute("""
                UPDATE addresses SET in_use_by_sale_id = ?, locked_until = ? 
                WHERE crypto_address = ?
            """, (sale_id, expires_str, address))
            
            await db.commit()
            return address, final_amount, sale_id
        except Exception as e:
            await db.execute("ROLLBACK")
            logging.error(f"Error in get_and_create_sale: {e}")
            return None, None, None

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

async def log_activity(telegram_id: int, username: str, activity_text: str):
    from datetime import datetime
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO user_activity_logs (telegram_id, activity, created_at) VALUES (?, ?, ?)",
            (telegram_id, activity_text, now_str)
        )
        await db.execute(
            "UPDATE users SET last_activity = ?, last_activity_at = ?, username = COALESCE(?, username) WHERE telegram_id = ?",
            (activity_text, now_str, username, telegram_id)
        )
        await db.commit()

async def is_silent_mode():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM bot_settings WHERE key = 'silent_mode'") as cursor:
            row = await cursor.fetchone()
            return row and row[0] == 'on'

async def set_silent_mode(enabled: bool):
    val = 'on' if enabled else 'off'
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES ('silent_mode', ?)", (val,))
        await db.commit()

async def is_blackmagic_on():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM bot_settings WHERE key = 'blackmagic'") as cursor:
            row = await cursor.fetchone()
            return row and row[0] == 'on'

async def set_blackmagic(enabled: bool):
    val = 'on' if enabled else 'off'
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES ('blackmagic', ?)", (val,))
        await db.commit()

async def cleanup_completed_orders():
    """
    Deletes all successful sales and marks their associated images as unsold (put the secret back up).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        # Mark all images belonging to the same secrets as unsold
        await db.execute("""
            UPDATE item_images 
            SET is_sold = 0 
            WHERE id IN (
                SELECT img.id 
                FROM item_images img
                JOIN item_images img_ref ON (img.secret_group = img_ref.secret_group OR img.id = img_ref.id)
                WHERE img_ref.id IN (SELECT image_id FROM sales WHERE status IN ('completed', 'paid', 'delivered'))
            )
        """)
        # 2. Delete the sales
        await db.execute("DELETE FROM sales WHERE status IN ('completed', 'paid', 'delivered')")
        await db.commit()

async def get_last_completed_sales(limit=5):
    """Returns the last N successful sales (completed, paid, or delivered)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT s.id, i.name, s.amount_expected, s.created_at, u.username, s.status, s.image_id
            FROM sales s
            JOIN items i ON s.item_id = i.id
            JOIN users u ON s.user_id = u.id
            WHERE s.status IN ('completed', 'paid', 'delivered')
            ORDER BY s.created_at DESC
            LIMIT ?
        """, (limit,)) as cursor:
            return await cursor.fetchall()

async def restore_secret_and_delete_sale(sale_id: int):
    """
    Deletes a specific sale and marks its associated image (and its group) as unsold.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT image_id FROM sales WHERE id = ?", (sale_id,)) as cursor:
            row = await cursor.fetchone()
            if row and row[0]:
                image_id = row[0]
                # Restore the whole group if it was part of one
                await db.execute("""
                    UPDATE item_images 
                    SET is_sold = 0 
                    WHERE secret_group = (SELECT secret_group FROM item_images WHERE id = ?)
                       OR id = ?
                """, (image_id, image_id))
        # 2. Delete the sale
        await db.execute("DELETE FROM sales WHERE id = ?", (sale_id,))
        await db.commit()

async def get_item_stats(item_id):
    """
    Returns (item_name, total_bought, best_buyer_info, current_stock)
    best_buyer_info is (username, tg_id, count) or None
    """
    async with aiosqlite.connect(DB_PATH) as db:
        # Total bought
        async with db.execute("SELECT COUNT(*) FROM sales WHERE item_id = ? AND status = 'paid'", (item_id,)) as c:
            total_bought = (await c.fetchone())[0]
            
        # Best buyer
        async with db.execute("""
            SELECT u.username, u.telegram_id, COUNT(s.id) as count
            FROM sales s
            JOIN users u ON s.user_id = u.id
            WHERE s.item_id = ? AND s.status = 'paid'
            GROUP BY s.user_id
            ORDER BY count DESC
            LIMIT 1
        """, (item_id,)) as c:
            best_buyer = await c.fetchone()
            
        # Item Name
        async with db.execute("SELECT name FROM items WHERE id = ?", (item_id,)) as c:
            name_row = await c.fetchone()
            item_name = name_row[0] if name_row else "Unknown"
            
        # Current Stock
        async with db.execute("""
            SELECT 
                (SELECT COUNT(DISTINCT secret_group) FROM item_images WHERE item_id = ? AND is_sold = 0 AND secret_group IS NOT NULL) +
                (SELECT COUNT(*) FROM item_images WHERE item_id = ? AND is_sold = 0 AND secret_group IS NULL)
        """, (item_id, item_id)) as c:
            current_stock = (await c.fetchone())[0]
            
        return item_name, total_bought, best_buyer, current_stock

async def get_user_total_sales(telegram_id):
    """Returns the total number of paid/completed sales for a user."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT COUNT(*) FROM sales 
            WHERE user_id = (SELECT id FROM users WHERE telegram_id = ?) 
              AND status IN ('paid', 'completed')
        """, (telegram_id,)) as c:
            row = await c.fetchone()
            return row[0] if row else 0


