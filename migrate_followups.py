import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "clinic.db")

def init_followups():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS post_consultations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            appointment_id INTEGER UNIQUE,
            patient_phone TEXT,
            patient_name TEXT,
            consultation_date TEXT,
            followup_sent_at TIMESTAMP,
            feedback_score INTEGER,
            review_link_sent INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending'
        )
    """)
    conn.commit()
    conn.close()
    print("✅ post_consultations table created.")

if __name__ == "__main__":
    init_followups()
