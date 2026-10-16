import sqlite3
import os
from datetime import datetime, date as dt_date, timedelta
from app.notifier import send_whatsapp_message

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "clinic.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def dispatch_day_before_reminders(target_date: str = None) -> dict:
    """Scans for confirmed appointments on target_date and dispatches WhatsApp 24h reminders."""
    date_to_remind = target_date or str(dt_date.today() + timedelta(days=1))
    conn = get_db()
    c = conn.cursor()
    
    c.execute("""
        SELECT id, patient_name, patient_phone, appointment_time
        FROM appointments
        WHERE appointment_time LIKE ? 
          AND status = 'confirmed'
          AND (reminder_24h_sent IS NULL OR reminder_24h_sent = 0)
          AND (opt_out IS NULL OR opt_out = 0)
    """, (f"{date_to_remind}%",))
    appts = [dict(r) for r in c.fetchall()]

    sent_count = 0
    for a in appts:
        time_part = a["appointment_time"].split()[1][:5]
        msg = (
            f"🔔 *Appointment Reminder - Dr. Kurian's Clinic*\n\n"
            f"Hello {a['patient_name']}! You have an appointment tomorrow:\n"
            f"📅 Date: *{date_to_remind}*\n"
            f"⏰ Time: *{time_part}*\n"
            f"📋 Appointment ID: *#{a['id']}*\n\n"
            f"Please reply with:\n"
            f"• *1* - To CONFIRM you are attending\n"
            f"• *cancel* - To cancel (frees slot for waitlist)"
        )
        if send_whatsapp_message(a["patient_phone"], msg):
            c.execute("UPDATE appointments SET reminder_24h_sent = 1 WHERE id = ?", (a["id"],))
            conn.commit()
            sent_count += 1

    conn.close()
    return {
        "target_date": date_to_remind,
        "total_eligible": len(appts),
        "reminders_sent": sent_count
    }

def dispatch_same_day_reminders() -> dict:
    """Scans for appointments starting in the next 1-3 hours and sends the final reminder."""
    conn = get_db()
    c = conn.cursor()
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")

    c.execute("""
        SELECT id, patient_name, patient_phone, appointment_time
        FROM appointments
        WHERE appointment_time LIKE ?
          AND status = 'confirmed'
          AND (reminder_2h_sent IS NULL OR reminder_2h_sent = 0)
          AND (opt_out IS NULL OR opt_out = 0)
    """, (f"{today_str}%",))
    
    appts = [dict(r) for r in c.fetchall()]
    sent_count = 0

    for a in appts:
        try:
            appt_dt = datetime.strptime(a["appointment_time"][:16], "%Y-%m-%d %H:%M")
            diff_hours = (appt_dt - now).total_seconds() / 3600.0
            
            # Target window: appointment is between 0.5 and 2.5 hours away
            if 0.5 <= diff_hours <= 2.5:
                time_part = a["appointment_time"].split()[1][:5]
                msg = (
                    f"⏰ *Clinic Visit Today - Dr. Kurian's Clinic*\n\n"
                    f"Hello {a['patient_name']}! Your appointment is today in about 2 hours at *{time_part}*.\n"
                    f"📍 Location: MG Road, Kochi (Opp. Metro Pillar 412)\n"
                    f"🗺️ Map: https://maps.app.goo.gl/clinic-kurian-kochi\n\n"
                    f"Please arrive 10 minutes early. Reply *'cancel'* if you cannot make it."
                )
                if send_whatsapp_message(a["patient_phone"], msg):
                    c.execute("UPDATE appointments SET reminder_2h_sent = 1 WHERE id = ?", (a["id"],))
                    conn.commit()
                    sent_count += 1
        except Exception:
            continue

    conn.close()
    return {
        "today": today_str,
        "total_eligible": len(appts),
        "same_day_sent": sent_count
    }
