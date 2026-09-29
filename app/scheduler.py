import os
import pytz
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.database import SessionLocal
from app.models import Appointment, Clinic
from app.sender import send_whatsapp_template

TIMEZONE = pytz.timezone(os.getenv("TIMEZONE", "Asia/Kolkata"))
scheduler = AsyncIOScheduler(timezone=TIMEZONE)

async def check_and_send_reminders():
    now = datetime.now(TIMEZONE).replace(tzinfo=None)
    db = SessionLocal()
    try:
        start_24h = now + timedelta(hours=23)
        end_24h = now + timedelta(hours=25)
        
        pending_24h = db.query(Appointment).filter(
            Appointment.appointment_time >= start_24h,
            Appointment.appointment_time <= end_24h,
            Appointment.reminded_24h == False,
            Appointment.opt_out == False,
            Appointment.status.in_(["BOOKED", "CONFIRMED"])
        ).all()

        for appt in pending_24h:
            clinic = db.query(Clinic).filter(Clinic.id == appt.clinic_id).first()
            clinic_name = clinic.name if clinic else "the Clinic"
            lang = clinic.language if clinic else "en"
            date_str = appt.appointment_time.strftime("%d-%b-%Y")
            time_str = appt.appointment_time.strftime("%I:%M %p")
            
            await send_whatsapp_template(
                to_phone=appt.patient_phone,
                template_name="appointment_reminder_24h",
                lang_code=lang,
                body_parameters=[appt.patient_name, appt.doctor_name, clinic_name, date_str, time_str]
            )
            appt.reminded_24h = True
            db.commit()

        start_2h = now + timedelta(minutes=90)
        end_2h = now + timedelta(minutes=150)

        pending_2h = db.query(Appointment).filter(
            Appointment.appointment_time >= start_2h,
            Appointment.appointment_time <= end_2h,
            Appointment.reminded_2h == False,
            Appointment.opt_out == False,
            Appointment.status.in_(["BOOKED", "CONFIRMED"])
        ).all()

        for appt in pending_2h:
            clinic = db.query(Clinic).filter(Clinic.id == appt.clinic_id).first()
            clinic_name = clinic.name if clinic else "the Clinic"
            lang = clinic.language if clinic else "en"
            time_str = appt.appointment_time.strftime("%I:%M %p")
            
            await send_whatsapp_template(
                to_phone=appt.patient_phone,
                template_name="appointment_reminder_2h",
                lang_code=lang,
                body_parameters=[appt.patient_name, appt.doctor_name, time_str, clinic_name]
            )
            appt.reminded_2h = True
            db.commit()

    except Exception as e:
        print(f"[SCHEDULER ERROR] {e}")
    finally:
        db.close()

def start_scheduler():
    scheduler.add_job(check_and_send_reminders, "interval", minutes=15)
    scheduler.start()
