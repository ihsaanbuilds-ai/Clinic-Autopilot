from apscheduler.schedulers.background import BackgroundScheduler
from app.reminders import dispatch_day_before_reminders
from app.followup_engine import dispatch_post_consultation_followups
from app.doctor_router import trigger_doctor_briefing
import logging

logger = logging.getLogger("clinic_scheduler")
scheduler = BackgroundScheduler()

def daily_morning_briefing_job():
    logger.info("Triggering doctor morning daily briefing...")
    trigger_doctor_briefing()

def daily_followup_job():
    logger.info("Triggering patient 2-day recovery check-ins...")
    dispatch_post_consultation_followups(days_ago=2)

def daily_evening_reminders_job():
    logger.info("Triggering automated patient day-before reminders...")
    dispatch_day_before_reminders()

def start_scheduler():
    scheduler.add_job(daily_morning_briefing_job, "cron", hour=8, minute=0, id="doctor_briefing")
    scheduler.add_job(daily_followup_job, "cron", hour=11, minute=0, id="patient_followups")
    scheduler.add_job(daily_evening_reminders_job, "cron", hour=18, minute=0, id="patient_reminders")
    scheduler.start()
    logger.info("Scheduler initialized with morning briefing, recovery follow-ups, and reminders.")

def shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown()
