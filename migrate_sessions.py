import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "clinic.db")

def init_sessions():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS patient_sessions (
            phone TEXT PRIMARY KEY,
            last_date TEXT,
            pending_intent TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()
    print("✅ Sessions table created in clinic.db")

if __name__ == "__main__":
    init_sessions()
