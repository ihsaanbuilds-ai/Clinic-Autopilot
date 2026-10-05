import sqlite3
import os
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), "clinic.db")

def init_licenses():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS clinic_license (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            clinic_slug TEXT UNIQUE,
            plan_name TEXT DEFAULT 'Monthly Professional',
            price_inr INTEGER DEFAULT 4999,
            active INTEGER DEFAULT 1,
            valid_until TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Initialize a 30-day active license for Dr. Kurian's clinic
    expiry = datetime.now() + timedelta(days=30)
    c.execute("""
        INSERT OR IGNORE INTO clinic_license (id, clinic_slug, plan_name, price_inr, active, valid_until)
        VALUES (1, 'dr-kurian-clinic', 'Monthly Pro', 4999, 1, ?)
    """, (expiry.strftime("%Y-%m-%d %H:%M:%S"),))
    
    conn.commit()
    conn.close()
    print("✅ clinic_license table initialized.")

if __name__ == "__main__":
    init_licenses()
