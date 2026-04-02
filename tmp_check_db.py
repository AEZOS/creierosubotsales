import sqlite3
import os

DB_PATH = "bot_database.sqlite"

if os.path.exists(DB_PATH):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT image_url, media_type FROM item_images LIMIT 5;")
        rows = cursor.fetchall()
        for i, row in enumerate(rows):
            print(f"Row {i}: url={row[0][:100]}... , type={row[1]}")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        conn.close()
else:
    print("DB not found")
