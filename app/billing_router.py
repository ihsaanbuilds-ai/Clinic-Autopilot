import os
import hmac
import hashlib
import json
import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse
import razorpay

from app.database import get_sqlite_connection

logger = logging.getLogger(__name__)

billing_router = APIRouter(prefix="/api/billing", tags=["Billing"])

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "rzp_test_placeholder")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "placeholder_secret")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "placeholder_webhook_secret")

def get_razorpay_client():
    return razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

def update_doctor_subscription(doctor_id: int, status: str, plan_type: str, period_end: str, sub_id: str = None):
    conn = get_sqlite_connection()
    c = conn.cursor()
    c.execute(
        """
        UPDATE doctors
        SET subscription_status = ?,
            plan_type = ?,
            current_period_end = ?,
            razorpay_subscription_id = COALESCE(?, razorpay_subscription_id)
        WHERE id = ?
        """,
        (status, plan_type, period_end, sub_id, doctor_id)
    )
    conn.commit()
    conn.close()
    logger.info(f"Updated doctor {doctor_id} subscription: status={status}, valid_until={period_end}")

@billing_router.get("/status/{doctor_id}")
def check_status(doctor_id: int):
    conn = get_sqlite_connection()
    c = conn.cursor()
    c.execute(
        "SELECT id, name, subscription_status, plan_type, current_period_end FROM doctors WHERE id = ?",
        (doctor_id,)
    )
    row = c.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Doctor not found")
    
    return {
        "doctor_id": row[0],
        "name": row[1],
        "subscription_status": row[2] or "inactive",
        "plan_type": row[3] or "trial",
        "current_period_end": row[4]
    }

@billing_router.post("/create-order")
def create_one_time_order(doctor_id: int, amount_inr: int = 1999):
    """
    Creates a standard one-time Razorpay payment order (e.g. ₹1,999 for 30 days clinic access).
    """
    client = get_razorpay_client()
    try:
        order_data = {
            "amount": amount_inr * 100,  # Razorpay amounts are in paise
            "currency": "INR",
            "receipt": f"clinic_doc_{doctor_id}_{int(datetime.now().timestamp())}",
            "notes": {
                "doctor_id": str(doctor_id),
                "plan_type": "monthly_pro"
            }
        }
        order = client.order.create(data=order_data)
        return {
            "order_id": order["id"],
            "amount": order["amount"],
            "currency": order["currency"],
            "key_id": RAZORPAY_KEY_ID
        }
    except Exception as e:
        logger.error(f"Error creating Razorpay order: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@billing_router.get("/checkout/{doctor_id}", response_class=HTMLResponse)
def get_checkout_page(doctor_id: int):
    """
    Hosted checkout page for the doctor/clinic to complete payment via UPI / Card.
    """
    return f"""<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Clinic Autopilot - Subscription</title>
    <script src="https://checkout.razorpay.com/v1/checkout.js"></script>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #f8fafc;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            margin: 0;
            padding: 16px;
        }}
        .card {{
            background: white;
            padding: 32px;
            border-radius: 16px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.08);
            max-width: 420px;
            width: 100%;
            text-align: center;
        }}
        h2 {{ margin-top: 0; color: #0f172a; }}
        .badge {{
            display: inline-block;
            background: #e0f2fe;
            color: #0369a1;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 13px;
            font-weight: 600;
            margin-bottom: 16px;
        }}
        .price {{
            font-size: 38px;
            font-weight: 700;
            color: #0f172a;
            margin: 16px 0;
        }}
        .price span {{ font-size: 16px; font-weight: 400; color: #64748b; }}
        ul {{
            text-align: left;
            padding-left: 20px;
            color: #334155;
            line-height: 1.8;
            margin-bottom: 24px;
        }}
        button {{
            background: #2563eb;
            color: white;
            border: none;
            padding: 14px 28px;
            font-size: 16px;
            font-weight: 600;
            border-radius: 8px;
            cursor: pointer;
            width: 100%;
            transition: background 0.2s;
        }}
        button:hover {{ background: #1d4ed8; }}
        .note {{ font-size: 12px; color: #94a3b8; margin-top: 14px; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="badge">Clinic Autopilot Pro</div>
        <h2>Automated Receptionist</h2>
        <div class="price">₹1,999 <span>/ month</span></div>
        <ul>
            <li>WhatsApp appointment scheduling</li>
            <li>Voice note audio transcription</li>
            <li>Gemini clinic Q&A agent</li>
            <li>Automated IST patient reminders & briefings</li>
        </ul>
        <button id="pay-btn">Activate Clinic Access</button>
        <p class="note">Instant activation via UPI, Debit/Credit Card & Netbanking</p>
    </div>

    <script>
        document.getElementById('pay-btn').onclick = async function() {{
            const res = await fetch('/api/billing/create-order?doctor_id={doctor_id}&amount_inr=1999', {{
                method: 'POST'
            }});
            const data = await res.json();
            if (!data.order_id) {{
                alert('Could not initialize payment gateway.');
                return;
            }}

            const options = {{
                key: data.key_id,
                amount: data.amount,
                currency: data.currency,
                name: "Clinic Autopilot",
                description: "Monthly Clinic Receptionist Subscription",
                order_id: data.order_id,
                handler: function (response) {{
                    alert("Payment successful! Clinic activation will reflect momentarily.");
                    window.location.reload();
                }},
                theme: {{ color: "#2563eb" }}
            }};
            const rzp = new Razorpay(options);
            rzp.open();
        }};
    </script>
</body>
</html>"""

@billing_router.post("/webhook")
async def handle_razorpay_webhook(request: Request):
    """
    Receives automated notifications from Razorpay when payments succeed or renew.
    """
    body_bytes = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")

    # In local testing without active webhook secret configured, allow processing if secret is placeholder
    if RAZORPAY_WEBHOOK_SECRET != "placeholder_webhook_secret":
        generated_sig = hmac.new(
            RAZORPAY_WEBHOOK_SECRET.encode(),
            body_bytes,
            hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(generated_sig, signature):
            raise HTTPException(status_code=400, detail="Invalid webhook signature")

    try:
        event_payload = json.loads(body_bytes.decode())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event_type = event_payload.get("event")
    logger.info(f"Received Razorpay event: {event_type}")

    if event_type in ["order.paid", "payment.captured"]:
        payment = event_payload.get("payload", {}).get("payment", {}).get("entity", {})
        notes = payment.get("notes", {})
        raw_doc_id = notes.get("doctor_id")

        if raw_doc_id:
            doctor_id = int(raw_doc_id)
            plan_type = notes.get("plan_type", "monthly_pro")
            new_period_end = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
            update_doctor_subscription(
                doctor_id=doctor_id,
                status="active",
                plan_type=plan_type,
                period_end=new_period_end
            )

    return {"status": "ok"}
