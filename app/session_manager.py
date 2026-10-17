import sqlite3
from datetime import datetime, timedelta

from app.database import get_sqlite_connection

SESSION_TTL_MINUTES = 30


def get_db():
    return get_sqlite_connection()


def get_session(phone: str) -> dict:
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "SELECT phone, last_date, pending_intent, doctor_id, updated_at FROM patient_sessions WHERE phone = ?",
        (phone,),
    )
    row = c.fetchone()
    conn.close()
    if not row:
        return {"phone": phone, "last_date": None, "pending_intent": None, "doctor_id": None}

    # Invalidate session if older than 30 minutes.
    try:
        updated = datetime.strptime(row["updated_at"], "%Y-%m-%d %H:%M:%S")
        if datetime.now() - updated > timedelta(minutes=SESSION_TTL_MINUTES):
            clear_session(phone)
            return {"phone": phone, "last_date": None, "pending_intent": None, "doctor_id": None}
    except (TypeError, ValueError):
        pass

    return dict(row)


def update_session(phone: str, last_date: str = None, pending_intent: str = None, doctor_id: int = None):
    conn = get_db()
    c = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute(
        """
        INSERT INTO patient_sessions (phone, last_date, pending_intent, doctor_id, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(phone) DO UPDATE SET
            last_date = COALESCE(excluded.last_date, patient_sessions.last_date),
            pending_intent = COALESCE(excluded.pending_intent, patient_sessions.pending_intent),
            doctor_id = COALESCE(excluded.doctor_id, patient_sessions.doctor_id),
            updated_at = excluded.updated_at
        """,
        (phone, last_date, pending_intent, doctor_id, now_str),
    )
    conn.commit()
    conn.close()


def clear_session(phone: str):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM patient_sessions WHERE phone = ?", (phone,))
    conn.commit()
    conn.close()
