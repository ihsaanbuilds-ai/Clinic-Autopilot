import sqlite3
import os
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), "clinic.db")

def init_all():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # 1. Base appointments table
    c.execute("""
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_name TEXT NOT NULL,
            patient_phone TEXT NOT NULL,
            appointment_time TIMESTAMP NOT NULL UNIQUE,
            status TEXT DEFAULT 'confirmed',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            doctor_id INTEGER DEFAULT 1
        )
    """)

    # 2. Base blocked dates table
    c.execute("""
        CREATE TABLE IF NOT EXISTS blocked_dates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            blocked_date DATE NOT NULL UNIQUE,
            reason TEXT DEFAULT 'Doctor on leave'
        )
    """)

    # 3. Waitlist table
    c.execute("""
        CREATE TABLE IF NOT EXISTS waitlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_name TEXT NOT NULL,
            patient_phone TEXT NOT NULL,
            preferred_date DATE NOT NULL,
            preferred_time TEXT NOT NULL,
            status TEXT DEFAULT 'waiting',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            doctor_id INTEGER DEFAULT 1
        )
    """)

    # 4. Patient Sessions table
    c.execute("""
        CREATE TABLE IF NOT EXISTS patient_sessions (
            phone TEXT PRIMARY KEY,
            last_date TEXT,
            pending_intent TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Column safety check for patient_sessions
    c.execute("PRAGMA table_info(patient_sessions)")
    columns = [col[1] for col in c.fetchall()]
    if "patient_phone" in columns and "phone" not in columns:
        try:
            c.execute("ALTER TABLE patient_sessions RENAME COLUMN patient_phone TO phone")
        except Exception:
            c.execute("DROP TABLE patient_sessions")
            c.execute("""
                CREATE TABLE patient_sessions (
                    phone TEXT PRIMARY KEY,
                    last_date TEXT,
                    pending_intent TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

    # 5. Staff Escalations table
    c.execute("""
        CREATE TABLE IF NOT EXISTS staff_escalations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_phone TEXT NOT NULL,
            patient_name TEXT,
            reason TEXT,
            status TEXT DEFAULT 'open',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMP
        )
    """)

    # 6. Post Consultations table
    c.execute("""
        CREATE TABLE IF NOT EXISTS post_consultations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            appointment_id INTEGER,
            patient_phone TEXT NOT NULL,
            patient_name TEXT,
            consultation_date DATE,
            status TEXT DEFAULT 'pending',
            rating INTEGER,
            feedback TEXT,
            followup_sent_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 7. Doctors table
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
        INSERT OR IGNORE INTO doctors (id, name, specialty, phone, consultation_fee)
        VALUES 
            (1, 'Dr. Kurian', 'General Medicine', '918928740867', 500),
            (2, 'Dr. Ananya', 'Dental Surgeon', '919876543201', 700)
    """)

    # 8. Payments table
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

    # 9. Clinic license table
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

    expiry = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    c.execute("""
        INSERT OR IGNORE INTO clinic_license (id, clinic_slug, plan_name, price_inr, active, valid_until)
        VALUES (1, 'dr-kurian-clinic', 'Monthly Pro', 4999, 1, ?)
    """, (expiry,))

    conn.commit()
    conn.close()
    print("✅ Canonical schema applied successfully.")

if __name__ == "__main__":
    init_all()
