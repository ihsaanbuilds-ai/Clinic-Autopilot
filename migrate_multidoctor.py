import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "clinic.db")

def init_multidoctor():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Doctors Table
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

    # Seed default clinic practitioners
    c.execute("""
        INSERT OR IGNORE INTO doctors (id, name, specialty, phone, consultation_fee)
        VALUES 
            (1, 'Dr. Kurian', 'General Medicine', '918928740867', 500),
            (2, 'Dr. Ananya', 'Dental Surgeon', '919876543201', 700)
    """)

    # Add doctor_id column to appointments and waitlist if missing
    try:
        c.execute("ALTER TABLE appointments ADD COLUMN doctor_id INTEGER DEFAULT 1 REFERENCES doctors(id)")
    except sqlite3.OperationalError:
        pass

    try:
        c.execute("ALTER TABLE waitlist ADD COLUMN doctor_id INTEGER DEFAULT 1 REFERENCES doctors(id)")
    except sqlite3.OperationalError:
        pass

    # Payments table for deposits / consultation fees
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

    conn.commit()
    conn.close()
    print("✅ Multi-doctor and payments schema configured successfully.")

if __name__ == "__main__":
    init_multidoctor()
