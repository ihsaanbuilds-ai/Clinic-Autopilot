import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "clinic.db")
conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

tables = ["appointments", "waitlist", "patient_sessions", "staff_escalations", "post_consultations"]
print("🔍 System Audit:")
for t in tables:
    c.execute(f"SELECT COUNT(*) FROM {t}")
    count = c.fetchone()[0]
    print(f"  • Table '{t}': {count} records")

conn.close()

import app.main
import app.receptionist
import app.doctor_router
import app.dashboard_router
import app.voice_router
import app.nlp_parser
import app.followup_engine
import app.escalation_manager
import app.scheduler

print("\n✅ All 9 backend modules imported successfully with zero syntax errors.")
