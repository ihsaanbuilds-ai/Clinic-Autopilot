"""Canonical schema for a fresh Clinic Autopilot install.

Keep this in sync with `migrate_mumbai_v2.py`; the two must produce an identical
schema for a fresh database and an upgraded one.
"""
import sqlite3
import os
from datetime import datetime, timedelta

DB_PATH = os.getenv("CLINIC_DB_PATH") or os.path.join(
    os.path.dirname(__file__), "clinic.db"
)

def init_all():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS clinics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            phone_number_id TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS doctors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            specialty TEXT NOT NULL,
            phone TEXT UNIQUE,
            consultation_fee INTEGER DEFAULT 500,
            active INTEGER DEFAULT 1
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS doctor_schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doctor_id INTEGER NOT NULL,
            day_of_week INTEGER NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            slot_duration_minutes INTEGER DEFAULT 30,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (doctor_id) REFERENCES doctors(id),
            UNIQUE(doctor_id, day_of_week, start_time)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_name TEXT NOT NULL,
            patient_phone TEXT NOT NULL,
            doctor_name TEXT,
            appointment_time TIMESTAMP NOT NULL,
            status TEXT DEFAULT 'confirmed',
            reminder_24h_sent INTEGER DEFAULT 0,
            reminder_2h_sent INTEGER DEFAULT 0,
            opt_out INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            doctor_id INTEGER DEFAULT 1
        )
    """)

    c.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_appointments_doctor_slot
        ON appointments(doctor_id, appointment_time)
        WHERE status IN ('confirmed', 'completed', 'arrived')
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS blocked_dates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doctor_id INTEGER DEFAULT 1,
            blocked_date DATE NOT NULL,
            reason TEXT DEFAULT 'Doctor on leave',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(doctor_id, blocked_date)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS waitlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_name TEXT NOT NULL,
            patient_phone TEXT NOT NULL,
            preferred_date DATE NOT NULL,
            preferred_time TEXT,
            status TEXT DEFAULT 'waiting',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            doctor_id INTEGER DEFAULT 1
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS patient_sessions (
            phone TEXT PRIMARY KEY,
            last_date TEXT,
            pending_intent TEXT,
            doctor_id INTEGER DEFAULT 1,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS staff_escalations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_phone TEXT NOT NULL,
            patient_name TEXT,
            reason TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS post_consultations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            appointment_id INTEGER,
            patient_phone TEXT NOT NULL,
            patient_name TEXT,
            consultation_date DATE,
            followup_sent_at TIMESTAMP,
            feedback_score INTEGER,
            review_link_sent INTEGER DEFAULT 0,
            status TEXT DEFAULT 'sent',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            appointment_id INTEGER,
            patient_phone TEXT,
            amount INTEGER,
            razorpay_order_id TEXT UNIQUE,
            razorpay_payment_id TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS payment_utrs (
            utr_number TEXT PRIMARY KEY,
            submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS clinic_license (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            clinic_slug TEXT UNIQUE,
            plan_name TEXT DEFAULT 'Monthly Pro',
            price_inr INTEGER DEFAULT 4999,
            active INTEGER DEFAULT 1,
            valid_until TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        INSERT OR IGNORE INTO clinics (id, name, phone_number_id)
        VALUES (1, 'CityCare Multispeciality Clinic', NULL)
    """)

    c.execute("""
        INSERT OR IGNORE INTO doctors (id, name, specialty, phone, consultation_fee, active)
        VALUES
            (1, 'Dr. Rohan Mehta', 'General Medicine', '918928740867', 600, 1),
            (2, 'Dr. Priya Nair', 'Dental Surgeon', '919876543201', 800, 1)
    """)

    expiry = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    c.execute("""
        INSERT OR IGNORE INTO clinic_license (id, clinic_slug, plan_name, price_inr, active, valid_until)
        VALUES (1, 'citycare-clinic', 'Monthly Pro', 4999, 1, ?)
    """, (expiry,))

    conn.commit()
    conn.close()
    print("✅ Canonical schema applied successfully.")

if __name__ == "__main__":
    init_all()
