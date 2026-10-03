import sqlite3
import os
from datetime import date as dt_date, timedelta
from app.notifier import send_whatsapp_message

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "clinic.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def dispatch_day_before_reminders(target_date: str = None) -> dict:
    """Scans for confirmed appointments on target_date and dispatches WhatsApp reminders."""
    date_to_remind = target_date or str(dt_date.today() + timedelta(days=1))
    conn = get_db()
    c = conn.cursor()
    
    c.execute("""
        SELECT id, patient_name, patient_phone, appointment_time
        FROM appointments
        WHERE appointment_time LIKE ? AND status = 'confirmed'
    """, (f"{date_to_remind}%",))
    appts = [dict(r) for r in c.fetchall()]
    conn.close()

    sent_count = 0
    for a in appts:
        time_part = a["appointment_time"].split()[1][:5]
        msg = (
            f"🔔 *Appointment Reminder - Dr. Kurian's Clinic*\n\n"
            f"Hello {a['patient_name']}! You have an upcoming appointment scheduled for tomorrow:\n"
            f"📅 Date: *{date_to_remind}*\n"
            f"⏰ Time: *{time_part}*\n"
            f"📋 Appointment ID: *#{a['id']}*\n\n"
            f"Please reply to this message:\n"
            f"• *1* - To CONFIRM your attendance\n"
            f"• *2* - To CANCEL (opens slot to waitlist)"
        )
        if send_whatsapp_message(a["patient_phone"], msg):
            sent_count += 1

    return {
        "target_date": date_to_remind,
        "total_appointments": len(appts),
        "reminders_sent": sent_count
    }
