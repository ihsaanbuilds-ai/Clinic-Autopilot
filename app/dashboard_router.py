"""Clinic operator dashboard API.

All endpoints are admin-authenticated (``X-Admin-Secret`` header).
"""
import os
from datetime import date as dt_date

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from app import config
from app.database import get_sqlite_connection
from app.auth import verify_admin
from app.license_manager import get_license_status, renew_subscription
from app.receptionist import (
    promote_waitlist_candidate,
    get_matching_waitlisted_candidate,
    is_date_blocked,
    get_active_doctors,
)
from app.doctor_router import block_doctor_date
from app.escalation_manager import get_pending_escalations, resolve_escalation

dashboard_router = APIRouter(
    prefix="/api/dashboard",
    tags=["Clinic Dashboard"],
    dependencies=[Depends(verify_admin)],
)


def get_db():
    return get_sqlite_connection()


class BlockDatePayload(BaseModel):
    date: str
    doctor_id: int | None = None


class PromotePayload(BaseModel):
    waitlist_id: int
    slot_time: str | None = None


class ResolveEscalationPayload(BaseModel):
    phone: str


@dashboard_router.get("/metrics")
def get_dashboard_metrics(target_date: str = None):
    query_date = target_date or str(dt_date.today())
    conn = get_db()
    c = conn.cursor()

    c.execute(
        "SELECT COUNT(*) AS count FROM appointments WHERE appointment_time LIKE ? AND status = 'confirmed'",
        (f"{query_date}%",),
    )
    confirmed_count = c.fetchone()["count"]

    c.execute(
        "SELECT COUNT(*) AS count FROM appointments WHERE appointment_time LIKE ? AND status LIKE '%cancelled%'",
        (f"{query_date}%",),
    )
    cancelled_count = c.fetchone()["count"]

    c.execute(
        "SELECT COUNT(*) AS count FROM waitlist WHERE preferred_date = ? AND status = 'waiting'",
        (query_date,),
    )
    waitlist_count = c.fetchone()["count"]

    c.execute(
        """
        SELECT id, patient_name, patient_phone, appointment_time, status, doctor_id, doctor_name
        FROM appointments WHERE appointment_time LIKE ?
        ORDER BY appointment_time ASC
        """,
        (f"{query_date}%",),
    )
    appointments = [dict(r) for r in c.fetchall()]

    c.execute(
        """
        SELECT id, patient_name, patient_phone, preferred_date, preferred_time, status, doctor_id
        FROM waitlist WHERE preferred_date = ?
        ORDER BY id ASC
        """,
        (query_date,),
    )
    waitlist = [dict(r) for r in c.fetchall()]

    blocked, reason = is_date_blocked(query_date)
    conn.close()

    escalations = get_pending_escalations()

    return {
        "date": query_date,
        "clinic": config.CLINIC_NAME,
        "is_blocked": blocked,
        "block_reason": reason,
        "summary": {
            "confirmed": confirmed_count,
            "cancelled": cancelled_count,
            "waitlisted": waitlist_count,
            "escalations": len(escalations),
        },
        "appointments": appointments,
        "waitlist": waitlist,
        "escalations": escalations,
    }


@dashboard_router.get("/doctors")
def list_doctors():
    return {"doctors": get_active_doctors()}


@dashboard_router.post("/cancel/{appointment_id}")
def cancel_appointment(appointment_id: int):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM appointments WHERE id = ?", (appointment_id,))
    appt = c.fetchone()
    if not appt:
        conn.close()
        raise HTTPException(status_code=404, detail="Appointment not found")

    c.execute("UPDATE appointments SET status = 'cancelled' WHERE id = ?", (appointment_id,))
    conn.commit()
    conn.close()

    parts = appt["appointment_time"].split()
    target_date = parts[0]
    slot_time = parts[1][:5] if len(parts) > 1 else None

    candidate = get_matching_waitlisted_candidate(target_date, slot_time, appt["doctor_id"])
    promoted = False
    if candidate:
        ok, _, _ = promote_waitlist_candidate(candidate["id"], slot_time)
        promoted = ok

    return {"status": "success", "cancelled_id": appointment_id, "auto_promoted": promoted}


@dashboard_router.post("/promote")
def manual_promote(payload: PromotePayload):
    ok, msg, item = promote_waitlist_candidate(payload.waitlist_id, payload.slot_time)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "success", "message": msg, "candidate": item}


@dashboard_router.post("/block-date")
def block_date_endpoint(payload: BlockDatePayload):
    result = block_doctor_date(payload.date, payload.doctor_id)
    return {"status": "success", "message": result}


@dashboard_router.post("/resolve-escalation")
def resolve_escalation_endpoint(payload: ResolveEscalationPayload):
    resolve_escalation(payload.phone)
    return {"status": "success", "resolved_phone": payload.phone}


@dashboard_router.get("/subscription")
def get_subscription_endpoint():
    return get_license_status(config.CLINIC_SLUG)


@dashboard_router.post("/subscription/renew")
def renew_subscription_endpoint(days: int = 30):
    return renew_subscription(days, config.CLINIC_SLUG)
