import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "clinic.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def is_patient_escalated(phone: str) -> bool:
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT status FROM staff_escalations 
        WHERE patient_phone LIKE ? AND status = 'pending'
    """, (f"%{phone[-10:]}%",))
    row = c.fetchone()
    conn.close()
    return row is not None

def trigger_escalation(phone: str, name: str, reason: str = "Requested human staff") -> str:
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO staff_escalations (patient_phone, patient_name, reason, status)
        VALUES (?, ?, ?, 'pending')
        ON CONFLICT(patient_phone) DO UPDATE SET
            status = 'pending',
            reason = excluded.reason,
            created_at = CURRENT_TIMESTAMP
    """, (phone, name, reason))
    conn.commit()
    conn.close()
    return (
        f"Understood, {name}. I've paused the AI and forwarded your conversation to our clinic front-desk staff. "
        f"A team member will reach out to you shortly via this chat or call.\n\n"
        f"*(Reply 'resume bot' anytime to reactivate automated scheduling)*"
    )

def resolve_escalation(phone: str) -> bool:
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        UPDATE staff_escalations SET status = 'resolved'
        WHERE patient_phone LIKE ?
    """, (f"%{phone[-10:]}%",))
    conn.commit()
    conn.close()
    return True

def get_pending_escalations() -> list[dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT id, patient_name, patient_phone, reason, created_at, status
        FROM staff_escalations
        WHERE status = 'pending'
        ORDER BY created_at DESC
    """)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows
