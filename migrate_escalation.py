import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "clinic.db")

def init_escalations():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS staff_escalations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_phone TEXT UNIQUE,
            patient_name TEXT,
            reason TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()
    print("✅ staff_escalations table initialized in clinic.db")

if __name__ == "__main__":
    init_escalations()
