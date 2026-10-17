from datetime import datetime, date as dt_date, timedelta

from app.database import get_sqlite_connection
from app.notifier import send_whatsapp_message
from app.session_manager import update_session
from app import config


def get_db():
    return get_sqlite_connection()


def dispatch_day_before_reminders(target_date: str = None) -> dict:
    """Send 24h reminders and arm the confirm/cancel reply session."""
    date_to_remind = target_date or str(dt_date.today() + timedelta(days=1))
    conn = get_db()
    c = conn.cursor()

    c.execute(
        """
        SELECT id, patient_name, patient_phone, appointment_time, doctor_name
        FROM appointments
        WHERE appointment_time LIKE ?
          AND status = 'confirmed'
          AND (reminder_24h_sent IS NULL OR reminder_24h_sent = 0)
          AND (opt_out IS NULL OR opt_out = 0)
        """,
        (f"{date_to_remind}%",),
    )
    appts = [dict(r) for r in c.fetchall()]

    sent_count = 0
    for a in appts:
        time_part = a["appointment_time"].split()[1][:5]
        msg = (
            f"🔔 *Appointment Reminder - {config.CLINIC_NAME}*\n\n"
            f"Hello {a['patient_name']}! You have an appointment tomorrow:\n"
            f"📅 Date: *{date_to_remind}*\n"
            f"⏰ Time: *{time_part}*\n"
            f"📋 Appointment ID: *#{a['id']}*\n\n"
            f"Please reply with:\n"
            f"• *1* - To CONFIRM you are attending\n"
            f"• *cancel* - To cancel (frees the slot for our waitlist)"
        )
        if send_whatsapp_message(a["patient_phone"], msg):
            c.execute("UPDATE appointments SET reminder_24h_sent = 1 WHERE id = ?", (a["id"],))
            conn.commit()
            # Arm the confirmation reply so a bare "1" is understood.
            update_session(a["patient_phone"], pending_intent="awaiting_reminder_ack")
            sent_count += 1

    conn.close()
    return {
        "target_date": date_to_remind,
        "total_eligible": len(appts),
        "reminders_sent": sent_count,
    }


def dispatch_same_day_reminders() -> dict:
    """Send a final reminder to appointments starting in the next 0.5–2.5 hours."""
    conn = get_db()
    c = conn.cursor()
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")

    c.execute(
        """
        SELECT id, patient_name, patient_phone, appointment_time
        FROM appointments
        WHERE appointment_time LIKE ?
          AND status = 'confirmed'
          AND (reminder_2h_sent IS NULL OR reminder_2h_sent = 0)
          AND (opt_out IS NULL OR opt_out = 0)
        """,
        (f"{today_str}%",),
    )

    appts = [dict(r) for r in c.fetchall()]
    sent_count = 0

    for a in appts:
        try:
            appt_dt = datetime.strptime(a["appointment_time"][:16], "%Y-%m-%d %H:%M")
            diff_hours = (appt_dt - now).total_seconds() / 3600.0

            if 0.5 <= diff_hours <= 2.5:
                time_part = a["appointment_time"].split()[1][:5]
                msg = (
                    f"⏰ *Clinic Visit Today - {config.CLINIC_NAME}*\n\n"
                    f"Hello {a['patient_name']}! Your appointment is today in about 2 hours at *{time_part}*.\n"
                    f"📍 Location: {config.CLINIC_ADDRESS}\n"
                    f"🗺️ Map: {config.CLINIC_MAPS_URL}\n\n"
                    f"Please arrive 10 minutes early. Reply *'cancel'* if you cannot make it."
                )
                if send_whatsapp_message(a["patient_phone"], msg):
                    c.execute("UPDATE appointments SET reminder_2h_sent = 1 WHERE id = ?", (a["id"],))
                    conn.commit()
                    sent_count += 1
        except (ValueError, IndexError):
            continue

    conn.close()
    return {
        "today": today_str,
        "total_eligible": len(appts),
        "same_day_sent": sent_count,
    }
