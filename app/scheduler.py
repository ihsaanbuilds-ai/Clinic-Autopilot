from apscheduler.schedulers.background import BackgroundScheduler
from app.reminders import dispatch_day_before_reminders
from app.doctor_router import trigger_doctor_briefing
import logging

logger = logging.getLogger("clinic_scheduler")
scheduler = BackgroundScheduler()

def daily_morning_briefing_job():
    logger.info("Triggering doctor morning daily briefing...")
    trigger_doctor_briefing()

def daily_evening_reminders_job():
    logger.info("Triggering automated patient day-before reminders...")
    dispatch_day_before_reminders()

def start_scheduler():
    # Morning briefing to Dr. Kurian at 08:00 daily
    scheduler.add_job(daily_morning_briefing_job, "cron", hour=8, minute=0, id="doctor_briefing")
    # Evening appointment reminders to patients at 18:00 daily
    scheduler.add_job(daily_evening_reminders_job, "cron", hour=18, minute=0, id="patient_reminders")
    scheduler.start()
    logger.info("Scheduler initialized with morning briefing and evening reminder jobs.")

def shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown()
