import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "clinic.db")

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def check_doctor_availability(date: str, time: str) -> bool:
    conn = get_connection()
    c = conn.cursor()
    target = f"{date} {time}"
    c.execute("""
        SELECT COUNT(*) FROM appointments 
        WHERE appointment_time LIKE ? AND status != 'cancelled'
    """, (f"%{target}%",))
    count = c.fetchone()[0]
    conn.close()
    return count == 0

def book_appointment_via_voice(name: str, phone: str, date: str, time: str) -> dict:
    conn = get_connection()
    c = conn.cursor()
    datetime_val = f"{date} {time}"
    c.execute("""
        INSERT INTO appointments (patient_name, patient_phone, doctor_name, appointment_time, status)
        VALUES (?, ?, 'Dr. Kurian', ?, 'confirmed')
    """, (name, phone, datetime_val))
    conn.commit()
    conn.close()
    return {
        "status": "confirmed",
        "message": f"Appointment successfully confirmed for {name} on {date} at {time}."
    }

def add_to_waitlist(patient_name: str, patient_phone: str, preferred_date: str, preferred_time: str = None) -> dict:
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO waitlist (patient_name, patient_phone, preferred_date, preferred_time, status)
        VALUES (?, ?, ?, ?, 'waiting')
    """, (patient_name, patient_phone, preferred_date, preferred_time))
    conn.commit()
    conn.close()
    return {
        "status": "success",
        "message": f"{patient_name} has been added to Dr. Kurian's priority waitlist for {preferred_date}."
    }

def auto_bump_next_patient(freed_date: str, freed_time: str) -> dict:
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT id, patient_name, patient_phone 
        FROM waitlist 
        WHERE preferred_date = ? AND status = 'waiting'
        ORDER BY id ASC LIMIT 1
    """, (freed_date,))
    row = c.fetchone()

    if row:
        waitlist_id = row["id"]
        name = row["patient_name"]
        phone = row["patient_phone"]
        
        datetime_val = f"{freed_date} {freed_time}"
        c.execute("""
            INSERT INTO appointments (patient_name, patient_phone, doctor_name, appointment_time, status)
            VALUES (?, ?, 'Dr. Kurian', ?, 'confirmed')
        """, (name, phone, datetime_val))
        
        c.execute("UPDATE waitlist SET status = 'bumped' WHERE id = ?", (waitlist_id,))
        conn.commit()
        conn.close()
        return {"status": "promoted", "patient_name": name, "patient_phone": phone}

    conn.close()
    return {"status": "none_waiting"}

def cancel_appointment_via_voice(patient_phone: str, date: str = None) -> dict:
    conn = get_connection()
    c = conn.cursor()
    
    if date:
        c.execute("""
            SELECT id, patient_name, appointment_time 
            FROM appointments 
            WHERE patient_phone = ? AND appointment_time LIKE ? AND status != 'cancelled'
            LIMIT 1
        """, (patient_phone, f"%{date}%"))
    else:
        c.execute("""
            SELECT id, patient_name, appointment_time 
            FROM appointments 
            WHERE patient_phone = ? AND status != 'cancelled'
            ORDER BY id DESC LIMIT 1
        """, (patient_phone,))
        
    booking = c.fetchone()
    if not booking:
        conn.close()
        return {"status": "not_found", "message": "No active booking found under this contact number."}

    booking_id = booking["id"]
    patient_name = booking["patient_name"]
    appointment_time = booking["appointment_time"]

    c.execute("UPDATE appointments SET status = 'cancelled' WHERE id = ?", (booking_id,))
    conn.commit()
    conn.close()

    parts = appointment_time.split(" ")
    b_date = parts[0] if len(parts) > 0 else date
    b_time = parts[1] if len(parts) > 1 else ""

    bump_res = auto_bump_next_patient(b_date, b_time)
    bump_msg = ""
    if bump_res.get("status") == "promoted":
        bump_msg = f" Slot was immediately reallocated to waitlisted patient {bump_res.get('patient_name')}."

    return {
        "status": "cancelled",
        "message": f"Appointment for {patient_name} at {appointment_time} has been cancelled.{bump_msg}"
    }
