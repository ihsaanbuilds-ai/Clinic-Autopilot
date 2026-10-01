import os
import sqlite3
from datetime import datetime
from twilio.rest import Client
from dotenv import load_dotenv

load_dotenv()

DB_PATH = "clinic.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_name TEXT,
            patient_phone TEXT,
            appointment_date TEXT,
            appointment_time TEXT,
            status TEXT DEFAULT 'CONFIRMED',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

init_db()

def check_doctor_availability(date_str: str, time_str: str) -> bool:
    """Checks if Dr. Kurian already has a confirmed booking for the given date and time."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(*) FROM appointments 
        WHERE appointment_date = ? AND appointment_time = ? AND status = 'CONFIRMED'
    """, (date_str.strip(), time_str.strip()))
    count = cursor.fetchone()[0]
    conn.close()
    return count == 0

def book_appointment_via_voice(patient_name: str, patient_phone: str, date_str: str, time_str: str) -> dict:
    """Saves the appointment and sends an SMS confirmation to the patient."""
    is_free = check_doctor_availability(date_str, time_str)
    if not is_free:
        return {
            "status": "unavailable",
            "message": f"Sorry, Dr. Kurian is already booked on {date_str} at {time_str}."
        }

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO appointments (patient_name, patient_phone, appointment_date, appointment_time, status)
        VALUES (?, ?, ?, ?, 'CONFIRMED')
    """, (patient_name.strip(), patient_phone.strip(), date_str.strip(), time_str.strip()))
    conn.commit()
    conn.close()

    # Send SMS Confirmation
    try:
        twilio_client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
        sms_text = (
            f"Hello {patient_name}, your appointment with Dr. Kurian is confirmed for "
            f"{date_str} at {time_str}. Clinic fee: 500 INR. Address: Dr. Kurian Clinic."
        )
        twilio_client.messages.create(
            to=patient_phone,
            from_=os.getenv("TWILIO_PHONE_NUMBER"),
            body=sms_text
        )
        print(f"📩 [SMS SENT] Confirmation sent to {patient_phone}")
    except Exception as e:
        print(f"⚠️ [SMS FAILED]: {e}")

    return {
        "status": "success",
        "message": f"Appointment successfully confirmed for {patient_name} on {date_str} at {time_str}."
    }
