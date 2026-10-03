import sqlite3
import os
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "clinic.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_session(phone: str) -> dict:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT phone, last_date, pending_intent, updated_at FROM patient_sessions WHERE phone = ?", (phone,))
    row = c.fetchone()
    conn.close()
    if not row:
        return {"phone": phone, "last_date": None, "pending_intent": None}
    
    # Invalidate session if older than 30 minutes
    try:
        updated = datetime.strptime(row["updated_at"], "%Y-%m-%d %H:%M:%S")
        if datetime.now() - updated > timedelta(minutes=30):
            clear_session(phone)
            return {"phone": phone, "last_date": None, "pending_intent": None}
    except Exception:
        pass

    return dict(row)

def update_session(phone: str, last_date: str = None, pending_intent: str = None):
    conn = get_db()
    c = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("""
        INSERT INTO patient_sessions (phone, last_date, pending_intent, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(phone) DO UPDATE SET
            last_date = COALESCE(excluded.last_date, patient_sessions.last_date),
            pending_intent = COALESCE(excluded.pending_intent, patient_sessions.pending_intent),
            updated_at = excluded.updated_at
    """, (phone, last_date, pending_intent, now_str))
    conn.commit()
    conn.close()

def clear_session(phone: str):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM patient_sessions WHERE phone = ?", (phone,))
    conn.commit()
    conn.close()
