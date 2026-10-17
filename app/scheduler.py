import logging
from zoneinfo import ZoneInfo
from apscheduler.schedulers.background import BackgroundScheduler
from app.reminders import dispatch_day_before_reminders, dispatch_same_day_reminders
from app.followup_engine import dispatch_post_consultation_followups
from app.doctor_router import trigger_doctor_briefing

logger = logging.getLogger("clinic_scheduler")
KOLKATA_TZ = ZoneInfo("Asia/Kolkata")

scheduler = BackgroundScheduler(timezone=KOLKATA_TZ)

def daily_morning_briefing_job():
    try:
        logger.info("Triggering doctor morning briefing...")
        trigger_doctor_briefing()
    except Exception as e:
        logger.error(f"Error running doctor morning briefing: {e}")

def daily_followup_job():
    try:
        logger.info("Triggering patient 2-day recovery check-ins...")
        dispatch_post_consultation_followups(days_ago=2)
    except Exception as e:
        logger.error(f"Error running recovery check-ins: {e}")

def daily_advance_reminders_job():
    try:
        logger.info("Triggering automated 24h advance reminders...")
        res = dispatch_day_before_reminders()
        logger.info(f"24h reminders dispatched: {res}")
    except Exception as e:
        logger.error(f"Error running 24h reminders: {e}")

def intraday_same_day_reminders_job():
    try:
        logger.info("Scanning for upcoming visits within next 2 hours...")
        res = dispatch_same_day_reminders()
        logger.info(f"2-hour alerts dispatched: {res}")
    except Exception as e:
        logger.error(f"Error running intraday reminders: {e}")

def start_scheduler():
    if not scheduler.running:
        scheduler.add_job(
            daily_morning_briefing_job,
            "cron",
            hour=7,
            minute=30,
            timezone=KOLKATA_TZ,
            id="doctor_briefing",
            replace_existing=True
        )

        scheduler.add_job(
            daily_advance_reminders_job,
            "cron",
            hour=8,
            minute=0,
            timezone=KOLKATA_TZ,
            id="patient_24h_reminders",
            replace_existing=True
        )

        scheduler.add_job(
            daily_followup_job,
            "cron",
            hour=11,
            minute=0,
            timezone=KOLKATA_TZ,
            id="patient_followups",
            replace_existing=True
        )

        scheduler.add_job(
            intraday_same_day_reminders_job,
            "cron",
            hour="8-19",
            minute="0,30",
            timezone=KOLKATA_TZ,
            id="patient_same_day_alerts",
            replace_existing=True
        )

        scheduler.start()
        logger.info("Scheduler initialized with IST jobs: briefing, 24h reminders, recovery follow-ups, and 30m alerts.")

def shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler shut down.")
