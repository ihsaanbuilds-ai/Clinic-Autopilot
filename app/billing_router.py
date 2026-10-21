"""SaaS subscription billing for the clinic operator.

Admin-authenticated. Renewal is a manual UPI-UTR verification flow suitable for
a pilot (no payment-gateway dependency required to start earning).
"""
import os
import sqlite3
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Form, Depends

from app import config
from app.auth import verify_admin
from app.database import get_sqlite_connection

billing_router = APIRouter(
    prefix="/api/subscription",
    tags=["SaaS Billing"],
    dependencies=[Depends(verify_admin)],
)

SAAS_UPI_ID = os.getenv("SAAS_UPI_ID", "ihsaan@upi")
SAAS_UPI_NAME = os.getenv("SAAS_UPI_NAME", "ReceptionistAI SaaS")
SAAS_MONTHLY_FEE = int(os.getenv("SAAS_MONTHLY_FEE", "4999"))


@billing_router.get("/status")
def get_subscription_status(clinic_slug: str = None):
    clinic_slug = clinic_slug or config.CLINIC_SLUG
    conn = get_sqlite_connection()
    c = conn.cursor()
    c.execute(
        "SELECT plan_name, price_inr, active, valid_until FROM clinic_license WHERE clinic_slug = ?",
        (clinic_slug,),
    )
    row = c.fetchone()
    conn.close()

    if not row:
        return {"status": "inactive", "days_remaining": 0, "clinic_slug": clinic_slug}

    valid_until = datetime.strptime(row["valid_until"], "%Y-%m-%d %H:%M:%S")
    days_remaining = max(0, (valid_until - datetime.now()).days)

    upi_intent = (
        f"upi://pay?pa={SAAS_UPI_ID}"
        f"&pn={SAAS_UPI_NAME.replace(' ', '%20')}"
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
        "upi_id": SAAS_UPI_ID,
    }


@billing_router.post("/submit-payment")
def submit_renewal_utr(clinic_slug: str = Form(None), utr_number: str = Form(...)):
    clinic_slug = clinic_slug or config.CLINIC_SLUG
    utr_clean = utr_number.strip()
    if len(utr_clean) < 8:
        raise HTTPException(status_code=400, detail="Please enter a valid UPI UTR/Transaction ID.")

    conn = get_sqlite_connection()
    c = conn.cursor()

    try:
        # Enforce uniqueness per UTR to prevent replay attacks BEFORE modifying license
        c.execute("""
            CREATE TABLE IF NOT EXISTS payment_utrs (
                utr_number TEXT PRIMARY KEY,
                submitted_at TIMESTAMP
            );
        """)
        
        existing = c.execute("SELECT utr_number FROM payment_utrs WHERE utr_number = ?", (utr_clean,)).fetchone()
        if existing:
            conn.close()
            raise HTTPException(status_code=409, detail="This UTR has already been recorded.")

        c.execute("SELECT valid_until FROM clinic_license WHERE clinic_slug = ?", (clinic_slug,))
        row = c.fetchone()

        current_expiry = datetime.now()
        if row and row["valid_until"]:
            existing_date = datetime.strptime(row["valid_until"], "%Y-%m-%d %H:%M:%S")
            if existing_date > current_expiry:
                current_expiry = existing_date

        new_expiry = (current_expiry + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

        c.execute(
            "UPDATE clinic_license SET valid_until = ?, active = 1 WHERE clinic_slug = ?",
            (new_expiry, clinic_slug),
        )
        if c.rowcount == 0:
            c.execute(
                "INSERT INTO clinic_license (clinic_slug, plan_name, price_inr, active, valid_until) "
                "VALUES (?, 'Monthly Pro', ?, 1, ?)",
                (clinic_slug, SAAS_MONTHLY_FEE, new_expiry),
            )

        c.execute("INSERT INTO payment_utrs (utr_number, submitted_at) VALUES (?, datetime('now'))", (utr_clean,))
        c.execute(
            """
            INSERT INTO payments (patient_phone, amount, razorpay_order_id, status)
            VALUES (?, ?, ?, 'verified')
            """,
            (f"UTR:{utr_clean}", SAAS_MONTHLY_FEE, f"SUB_{clinic_slug}_{int(datetime.now().timestamp())}"),
        )
        conn.commit()
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return {"status": "success", "message": "Subscription extended by 30 days!", "valid_until": new_expiry}
