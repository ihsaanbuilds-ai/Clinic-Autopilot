import sqlite3
import os
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Form
from pydantic import BaseModel

billing_router = APIRouter(prefix="/api/subscription", tags=["SaaS Billing"])
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "clinic.db")

YOUR_SAAS_UPI_ID = "ihsaan@upi"  # Replace with your personal/business UPI ID
SAAS_PLAN_NAME = "ReceptionistAI Pro Pilot"
SAAS_MONTHLY_FEE = 4999

@billing_router.get("/status")
def get_subscription_status(clinic_slug: str = "dr-kurian-clinic"):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT plan_name, price_inr, active, valid_until FROM clinic_license WHERE clinic_slug = ?", (clinic_slug,))
    row = c.fetchone()
    conn.close()

    if not row:
        return {"status": "inactive", "days_remaining": 0}

    valid_until = datetime.strptime(row["valid_until"], "%Y-%m-%d %H:%M:%S")
    days_remaining = max(0, (valid_until - datetime.now()).days)

    upi_intent = (
        f"upi://pay?pa={YOUR_SAAS_UPI_ID}"
        f"&pn=ReceptionistAI%20SaaS"
        f"&am={row['price_inr']}"
        f"&cu=INR"
        f"&tn=Subscription%20Renewal%20{clinic_slug}"
    )

    return {
        "clinic_slug": clinic_slug,
        "plan_name": row["plan_name"],
        "price_inr": row["price_inr"],
        "active": bool(row["active"] and days_remaining > 0),
        "valid_until": row["valid_until"],
        "days_remaining": days_remaining,
        "upi_intent": upi_intent,
        "upi_id": YOUR_SAAS_UPI_ID
    }

@billing_router.post("/submit-payment")
def submit_renewal_utr(clinic_slug: str = Form(...), utr_number: str = Form(...)):
    utr_clean = utr_number.strip()
    if len(utr_clean) < 8:
        raise HTTPException(status_code=400, detail="Please enter a valid 12-digit UPI UTR/Transaction ID.")

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Fetch existing validity
    c.execute("SELECT valid_until FROM clinic_license WHERE clinic_slug = ?", (clinic_slug,))
    row = c.fetchone()
    
    current_expiry = datetime.now()
    if row and row[0]:
        existing_date = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
        if existing_date > current_expiry:
            current_expiry = existing_date

    # Add 30 days
    new_expiry = (current_expiry + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

    c.execute("""
        UPDATE clinic_license 
        SET valid_until = ?, active = 1 
        WHERE clinic_slug = ?
    """, (new_expiry, clinic_slug))

    # Log to payments table
    c.execute("""
        INSERT INTO payments (patient_phone, amount, razorpay_order_id, status)
        VALUES (?, ?, ?, 'verified')
    """, (f"UTR:{utr_clean}", SAAS_MONTHLY_FEE, f"SUB_{clinic_slug}_{int(datetime.now().timestamp())}"))

    conn.commit()
    conn.close()

    return {"status": "success", "message": "Subscription extended by 30 days!", "valid_until": new_expiry}
