import sqlite3
import os
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "clinic.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_license_status(clinic_slug: str = "dr-kurian-clinic") -> dict:
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT plan_name, price_inr, active, valid_until 
        FROM clinic_license 
        WHERE clinic_slug = ?
    """, (clinic_slug,))
    row = c.fetchone()
    conn.close()

    if not row:
        return {"active": False, "reason": "No active license found"}

    now = datetime.now()
    valid_until = datetime.strptime(row["valid_until"], "%Y-%m-%d %H:%M:%S")
    is_valid = bool(row["active"]) and (valid_until > now)

    days_remaining = (valid_until - now).days if is_valid else 0

    return {
        "active": is_valid,
        "plan_name": row["plan_name"],
        "price_inr": row["price_inr"],
        "valid_until": row["valid_until"],
        "days_remaining": max(0, days_remaining),
        "is_expired": not is_valid
    }

def renew_subscription(days: int = 30, clinic_slug: str = "dr-kurian-clinic") -> dict:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT valid_until FROM clinic_license WHERE clinic_slug = ?", (clinic_slug,))
    row = c.fetchone()

    now = datetime.now()
    if row and row["valid_until"]:
        current_expiry = datetime.strptime(row["valid_until"], "%Y-%m-%d %H:%M:%S")
        start_time = max(now, current_expiry)
    else:
        start_time = now

    new_expiry = start_time + timedelta(days=days)
    new_expiry_str = new_expiry.strftime("%Y-%m-%d %H:%M:%S")

    c.execute("""
        UPDATE clinic_license 
        SET active = 1, valid_until = ? 
        WHERE clinic_slug = ?
    """, (new_expiry_str, clinic_slug))
    conn.commit()
    conn.close()

    return {
        "status": "success",
        "renewed_for_days": days,
        "valid_until": new_expiry_str
    }
