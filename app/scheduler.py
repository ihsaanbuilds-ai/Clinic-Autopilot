from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.database import SessionLocal
from app.models import Appointment
from app.sender import send_whatsapp_reminder

scheduler = AsyncIOScheduler()

async def poll_and_send_reminders():
    db = SessionLocal()
    now = datetime.now()
    try:
        window_start = now + timedelta(hours=23, minutes=30)
        window_end = now + timedelta(hours=25)

        upcoming_24h = db.query(Appointment).filter(
            Appointment.appointment_time >= window_start,
            Appointment.appointment_time <= window_end,
            Appointment.status == "BOOKED",
            Appointment.reminder_24h_sent == False,
            Appointment.opt_out == False
        ).all()

        for appt in upcoming_24h:
            success = await send_whatsapp_reminder(
                appt.patient_phone,
                appt.patient_name,
                appt.doctor_name,
                appt.appointment_time
            )
            if success:
                appt.reminder_24h_sent = True
                db.commit()
                print(f"⏰ [24h REMINDER] Sent to {appt.patient_name} ({appt.patient_phone})")

    except Exception as e:
        print(f"[SCHEDULER ERROR] {e}")
    finally:
        db.close()

def start_scheduler():
    if not scheduler.running:
        scheduler.add_job(poll_and_send_reminders, 'interval', minutes=1)
        scheduler.start()
        print("🕒 [SCHEDULER ACTIVE] Checking appointments every 60 seconds.")
