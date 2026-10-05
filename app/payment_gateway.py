import os
import uuid
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "clinic.db")
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "rzp_test_mock")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def create_payment_link(patient_phone: str, patient_name: str, amount_inr: int, appt_id: int) -> dict:
    order_id = f"order_{uuid.uuid4().hex[:12]}"
    payment_link = f"https://rzp.io/i/{order_id[-8:]}"

    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO payments (appointment_id, patient_phone, amount, razorpay_order_id, status)
        VALUES (?, ?, ?, ?, 'pending')
    """, (appt_id, patient_phone, amount_inr, order_id))
    conn.commit()
    conn.close()

    return {
        "order_id": order_id,
        "amount": amount_inr,
        "payment_url": payment_link
    }

def verify_payment_webhook(order_id: str, payment_id: str) -> bool:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, appointment_id FROM payments WHERE razorpay_order_id = ?", (order_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False

    c.execute("""
        UPDATE payments 
        SET razorpay_payment_id = ?, status = 'paid'
        WHERE id = ?
    """, (payment_id, row["id"]))

    if row["appointment_id"]:
        c.execute("UPDATE appointments SET status = 'confirmed' WHERE id = ?", (row["appointment_id"],))

    conn.commit()
    conn.close()
    return True
