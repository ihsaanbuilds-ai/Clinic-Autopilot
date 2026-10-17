"""Staff escalation + human-takeover bookkeeping.

A single vocabulary for escalation status is used everywhere:
``pending`` (needs staff attention / bot muted) and ``resolved`` (handled).
"""
from app.database import get_sqlite_connection

STATUS_PENDING = "pending"
STATUS_RESOLVED = "resolved"


def _phone_tail(phone: str) -> str:
    return str(phone).replace("whatsapp:", "").replace("+", "").strip()[-10:]


def is_patient_escalated(phone: str) -> bool:
    """True when the bot is muted for this patient (a pending escalation)."""
    conn = get_sqlite_connection()
    c = conn.cursor()
    c.execute(
        "SELECT 1 FROM staff_escalations WHERE patient_phone LIKE ? AND status = ? LIMIT 1",
        (f"%{_phone_tail(phone)}%", STATUS_PENDING),
    )
    row = c.fetchone()
    conn.close()
    return row is not None


def log_escalation(phone: str, name: str, reason: str) -> int:
    """Record (or refresh) a pending escalation. Returns the escalation id.

    Implemented as an explicit select-then-write to avoid relying on a UNIQUE
    constraint that the schema does not guarantee.
    """
    tail = _phone_tail(phone)
    conn = get_sqlite_connection()
    c = conn.cursor()
    c.execute(
        "SELECT id FROM staff_escalations WHERE patient_phone LIKE ? AND status = ? "
        "ORDER BY id DESC LIMIT 1",
        (f"%{tail}%", STATUS_PENDING),
    )
    row = c.fetchone()
    if row:
        c.execute(
            "UPDATE staff_escalations SET reason = ?, patient_name = COALESCE(?, patient_name) "
            "WHERE id = ?",
            (reason, name, row["id"]),
        )
        escalation_id = row["id"]
    else:
        c.execute(
            "INSERT INTO staff_escalations (patient_phone, patient_name, reason, status) "
            "VALUES (?, ?, ?, ?)",
            (phone, name, reason, STATUS_PENDING),
        )
        escalation_id = c.lastrowid
    conn.commit()
    conn.close()
    return escalation_id


def trigger_escalation(phone: str, name: str, reason: str = "Requested human staff") -> str:
    """Log a human-takeover request and return the patient-facing message."""
    log_escalation(phone, name, reason)
    return (
        f"Understood, {name}. I've paused the AI and forwarded your conversation to our "
        f"clinic front-desk staff. A team member will reach out to you shortly via this "
        f"chat or call.\n\n"
        f"*(Reply 'resume bot' anytime to reactivate automated scheduling)*"
    )


def trigger_emergency_escalation(phone: str, name: str, reason: str) -> str:
    """Log a red-flag emergency for staff follow-up and return the patient message."""
    from app.config import EMERGENCY_NUMBERS

    log_escalation(phone, name, f"EMERGENCY: {reason}")
    return (
        "🚨 *EMERGENCY MEDICAL NOTICE*:\n"
        "If you or the patient are experiencing acute symptoms like severe chest pain, "
        "breathing difficulty, or heavy bleeding, please call "
        f"*{EMERGENCY_NUMBERS}* immediately or visit the nearest Hospital Emergency Room.\n\n"
        "📍 *Emergency Map*: https://maps.google.com/?q=Hospital+Emergency+Near+Me\n"
        "Our clinic staff has been alerted to your message."
    )


def resolve_escalation(phone: str) -> bool:
    conn = get_sqlite_connection()
    c = conn.cursor()
    c.execute(
        "UPDATE staff_escalations SET status = ?, resolved_at = CURRENT_TIMESTAMP "
        "WHERE patient_phone LIKE ? AND status = ?",
        (STATUS_RESOLVED, f"%{_phone_tail(phone)}%", STATUS_PENDING),
    )
    conn.commit()
    conn.close()
    return True


def get_pending_escalations() -> list[dict]:
    conn = get_sqlite_connection()
    c = conn.cursor()
    c.execute(
        "SELECT id, patient_name, patient_phone, reason, created_at, status "
        "FROM staff_escalations WHERE status = ? ORDER BY created_at DESC",
        (STATUS_PENDING,),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows
