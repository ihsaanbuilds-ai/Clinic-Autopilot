import sqlite3
import os
from datetime import date as dt_date, timedelta
from app.notifier import send_whatsapp_message

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "clinic.db")
GOOGLE_REVIEW_LINK = os.getenv("GOOGLE_REVIEW_LINK", "https://g.page/r/dr-kurian-clinic/review")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def dispatch_post_consultation_followups(days_ago: int = 2) -> dict:
    target_date = str(dt_date.today() - timedelta(days=days_ago))
    conn = get_db()
    c = conn.cursor()

    c.execute("""
        SELECT a.id, a.patient_name, a.patient_phone, a.appointment_time
        FROM appointments a
        LEFT JOIN post_consultations p ON a.id = p.appointment_id
        WHERE a.appointment_time LIKE ? 
          AND a.status = 'confirmed'
          AND p.id IS NULL
    """, (f"{target_date}%",))
    appts = [dict(r) for r in c.fetchall()]

    sent_count = 0
    for a in appts:
        msg = (
            f"👋 Hello {a['patient_name']},\n\n"
            f"This is Dr. Kurian's clinic checking in on your recovery following your visit on *{target_date}*.\n\n"
            f"How are you feeling today? Please reply with a rating from *1 to 5*:\n"
            f"• *5* - Feeling great / Fully recovered\n"
            f"• *4* - Much better\n"
            f"• *3* - About the same\n"
            f"• *1-2* - Still unwell / Need follow-up"
        )
        send_whatsapp_message(a["patient_phone"], msg)
        c.execute("""
            INSERT INTO post_consultations (appointment_id, patient_phone, patient_name, consultation_date, followup_sent_at, status)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, 'sent')
        """, (a["id"], a["patient_phone"], a["patient_name"], target_date))
        conn.commit()
        sent_count += 1

    conn.close()
    return {"consultation_date": target_date, "targeted": len(appts), "sent": sent_count}

def handle_feedback_response(phone: str, text: str) -> str | None:
    clean = text.strip()
    if clean not in ["1", "2", "3", "4", "5"]:
        return None

    score = int(clean)
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT id, patient_name, review_link_sent FROM post_consultations
        WHERE patient_phone LIKE ? AND status = 'sent'
        ORDER BY id DESC LIMIT 1
    """, (f"%{phone[-10:]}%",))
    record = c.fetchone()

    if not record:
        conn.close()
        return None

    c.execute("""
        UPDATE post_consultations 
        SET feedback_score = ?, status = 'completed'
        WHERE id = ?
    """, (score, record["id"]))
    conn.commit()

    if score >= 4:
        c.execute("UPDATE post_consultations SET review_link_sent = 1 WHERE id = ?", (record["id"],))
        conn.commit()
        conn.close()
        return (
            f"🌟 We're so glad to hear you're feeling better, {record['patient_name']}!\n\n"
            f"If you had a good experience with Dr. Kurian, please take 30 seconds to support our clinic with a Google review:\n"
            f"🔗 {GOOGLE_REVIEW_LINK}\n\n"
            f"Thank you and stay healthy! 🩺"
        )
    else:
        conn.close()
        return (
            f"We're sorry to hear you're still not feeling well, {record['patient_name']}.\n\n"
            f"Dr. Kurian recommends scheduling a follow-up visit. Consultation within 7 days is just ₹300.\n"
            f"Would you like to book a follow-up slot this week? Reply with a preferred day/time (e.g., *'Book follow-up tomorrow 10am'*)."
        )
