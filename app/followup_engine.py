"""Post-consultation recovery check-ins and the Google-review funnel.

Strictly non-clinical: the bot asks how the patient feels (1–5) and either
routes them to the clinic's Google review page or flags the clinic front desk
to follow up. It never gives medical advice.
"""
from datetime import date as dt_date, timedelta

from app.database import get_sqlite_connection
from app.notifier import send_whatsapp_message
from app import config

VALID_SCORES = {"1", "2", "3", "4", "5"}


def get_db():
    return get_sqlite_connection()


def dispatch_post_consultation_followups(days_ago: int = 2) -> dict:
    target_date = str(dt_date.today() - timedelta(days=days_ago))
    conn = get_db()
    c = conn.cursor()

    c.execute(
        """
        SELECT a.id, a.patient_name, a.patient_phone
        FROM appointments a
        LEFT JOIN post_consultations p ON a.id = p.appointment_id
        WHERE a.appointment_time LIKE ?
          AND a.status IN ('confirmed', 'completed', 'arrived')
          AND p.id IS NULL
        """,
        (f"{target_date}%",),
    )
    appts = [dict(r) for r in c.fetchall()]

    sent_count = 0
    for a in appts:
        msg = (
            f"👋 Hello {a['patient_name']},\n\n"
            f"This is {config.CLINIC_NAME} checking in after your visit on *{target_date}*.\n\n"
            "How are you feeling today? Please reply with a rating from *1 to 5*:\n"
            "• *5* - Feeling great / Fully recovered\n"
            "• *4* - Much better\n"
            "• *3* - About the same\n"
            "• *1-2* - Still unwell / Need a follow-up"
        )
        if send_whatsapp_message(a["patient_phone"], msg):
            c.execute(
                """
                INSERT INTO post_consultations
                    (appointment_id, patient_phone, patient_name, consultation_date, followup_sent_at, status)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, 'sent')
                """,
                (a["id"], a["patient_phone"], a["patient_name"], target_date),
            )
            conn.commit()
            sent_count += 1

    conn.close()
    return {"consultation_date": target_date, "targeted": len(appts), "sent": sent_count}


def handle_feedback_response(phone: str, text: str) -> str | None:
    """Interpret a 1–5 reply to a follow-up. Returns a reply, or None if not applicable."""
    clean = text.strip()
    if clean not in VALID_SCORES:
        return None

    score = int(clean)
    conn = get_db()
    c = conn.cursor()
    c.execute(
        """
        SELECT id, patient_name, review_link_sent
        FROM post_consultations
        WHERE patient_phone LIKE ? AND status = 'sent'
        ORDER BY id DESC LIMIT 1
        """,
        (f"%{str(phone)[-10:]}%",),
    )
    record = c.fetchone()
    if not record:
        conn.close()
        return None

    c.execute(
        "UPDATE post_consultations SET feedback_score = ?, status = 'completed' WHERE id = ?",
        (score, record["id"]),
    )
    conn.commit()
    conn.close()

    if score >= 4:
        return (
            f"🌟 We're so glad to hear you're feeling better, {record['patient_name']}!\n\n"
            f"If you had a good experience with {config.DOCTOR_NAME}, please take 30 seconds to "
            f"support our clinic with a Google review:\n"
            f"🔗 {config.GOOGLE_REVIEW_LINK}\n\n"
            "Thank you and stay healthy! 🩺"
        )

    # Poor outcome → hand to front desk (non-clinical follow-up), never diagnose.
    from app.escalation_manager import log_escalation

    log_escalation(phone, record["patient_name"], f"Post-consult feedback score {score} (needs follow-up)")
    return (
        f"We're sorry to hear you're still not feeling well, {record['patient_name']}.\n\n"
        "Our clinic front desk has been notified and will reach out to arrange a follow-up visit. "
        "Would you like to book a slot now? Reply with a preferred day/time "
        "(e.g., *'Book follow-up tomorrow 10am'*)."
    )
