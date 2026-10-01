from app.voice_router import voice_router
import os
from datetime import datetime, date
from typing import Optional
from fastapi import FastAPI, BackgroundTasks, Depends, Request, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from dotenv import load_dotenv

load_dotenv()

from app.database import engine, Base, get_db
from app.models import Appointment, WaitlistEntry
from app.sender import send_whatsapp_text, send_whatsapp_reminder, clean_phone_number
from app.scheduler import start_scheduler

Base.metadata.create_all(bind=engine)

app = FastAPI(
title="Clinic Autopilot")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "clinic_autopilot_verify_token_2026")

@app.on_event("startup")
async def startup_event():
    start_scheduler()

class AppointmentCreate(BaseModel):
    patient_name: str
    patient_phone: str
    doctor_name: Optional[str] = "Dr. Kurian"
    appointment_time: datetime

class WaitlistCreate(BaseModel):
    patient_name: str
    patient_phone: str
    preferred_doctor: Optional[str] = "Dr. Kurian"

@app.get("/webhook")
async def verify_webhook(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge")
):
    if hub_mode == "subscribe" and hub_verify_token == META_VERIFY_TOKEN:
        return HTMLResponse(content=hub_challenge, status_code=200)
    raise HTTPException(status_code=403, detail="Verification token mismatch")

@app.post("/webhook")
async def handle_whatsapp_webhook(request: Request, db: Session = Depends(get_db)):
    data = await request.json()

    try:
        entries = data.get("entry", [])
        for entry in entries:
            for change in entry.get("changes", []):
                val = change.get("value", {})
                messages = val.get("messages", [])
                for msg in messages:
                    from_raw = msg.get("from")
                    from_phone = clean_phone_number(from_raw)
                    msg_body = ""

                    if msg.get("type") == "text":
                        msg_body = msg.get("text", {}).get("body", "").strip().upper()
                    elif msg.get("type") == "button":
                        msg_body = msg.get("button", {}).get("text", "").strip().upper()
                    elif msg.get("type") == "interactive":
                        interactive = msg.get("interactive", {})
                        if interactive.get("type") == "button_reply":
                            msg_body = interactive.get("button_reply", {}).get("title", "").strip().upper()

                    print(f"📩 [INBOUND WHATSAPP] From: {from_phone} | Message: {msg_body}")

                    appt = db.query(Appointment).filter(
                        Appointment.patient_phone.like(f"%{from_phone[-10:]}%"),
                        Appointment.status.in_(["BOOKED", "CONFIRMED", "RESCHEDULE_REQUESTED", "NO_SHOW", "ARRIVED"])
                    ).order_by(Appointment.appointment_time.desc()).first()

                    if "CONFIRM" in msg_body:
                        if appt:
                            appt.status = "CONFIRMED"
                            db.commit()
                            await send_whatsapp_text(
                                from_phone,
                                f"✅ Thank you, {appt.patient_name}! Your appointment with {appt.doctor_name} is confirmed."
                            )
                        else:
                            await send_whatsapp_text(from_phone, "✅ Received! No pending unconfirmed appointment found.")

                    elif "CANCEL" in msg_body:
                        if appt:
                            appt.status = "CANCELLED"
                            db.commit()
                            await send_whatsapp_text(
                                from_phone,
                                f"❌ Your appointment with {appt.doctor_name} has been cancelled. Please call the front desk to reschedule."
                            )

                            waitlist_patient = db.query(WaitlistEntry).filter(
                                WaitlistEntry.offered == False
                            ).order_by(WaitlistEntry.id.asc()).first()

                            if waitlist_patient:
                                waitlist_patient.offered = True
                                db.commit()
                                slot_str = appt.appointment_time.strftime("%I:%M %p")
                                recovery_msg = (
                                    f"Hi {waitlist_patient.patient_name}, an earlier slot with "
                                    f"{appt.doctor_name} opened up today at {slot_str}! "
                                    f"Reply CONFIRM within 15 minutes to claim it."
                                )
                                await send_whatsapp_text(waitlist_patient.patient_phone, recovery_msg)
                                print(f"🚀 [CHAIR RECOVERY TRIGGERED] Slot offered to {waitlist_patient.patient_name}")

                    elif "STOP" in msg_body:
                        if appt:
                            appt.opt_out = True
                            db.commit()
                        await send_whatsapp_text(from_phone, "You have been unsubscribed from appointment reminders.")

        return JSONResponse(status_code=200, content={"status": "processed"})
    except Exception as e:
        print(f"❌ [WEBHOOK PROCESSING ERROR] {e}")
        return JSONResponse(status_code=200, content={"status": "error_logged"})

from app.sender import send_whatsapp_reminder

@app.post("/api/appointments")
async def create_appointment(data: AppointmentCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    clean_phone = clean_phone_number(data.patient_phone)
    appt = Appointment(
        patient_name=data.patient_name,
        patient_phone=clean_phone,
        doctor_name=data.doctor_name,
        appointment_time=data.appointment_time,
        status="BOOKED"
    )
    db.add(appt)
    db.commit()
    db.refresh(appt)
    
    # Automatically dispatch WhatsApp reminder without touching the Meta dashboard
    background_tasks.add_task(send_whatsapp_template, clean_phone, "hello_world")
    
    return {"status": "success", "id": appt.id}

@app.post("/api/waitlist")
def add_to_waitlist(data: WaitlistCreate, db: Session = Depends(get_db)):
    clean_phone = clean_phone_number(data.patient_phone)
    entry = WaitlistEntry(
        patient_name=data.patient_name,
        patient_phone=clean_phone,
        preferred_doctor=data.preferred_doctor
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return {"status": "success", "id": entry.id}

@app.patch("/api/appointments/{appt_id}/status")
def update_status(appt_id: int, new_status: str, db: Session = Depends(get_db)):
    appt = db.query(Appointment).filter(Appointment.id == appt_id).first()
    if not appt:
        raise HTTPException(status_code=404, detail="Appointment not found")
    appt.status = new_status
    db.commit()
    return {"status": "success", "new_status": appt.status}

@app.get("/api/appointments/today")
def get_today_appointments(db: Session = Depends(get_db)):
    today_start = datetime.combine(date.today(), datetime.min.time())
    today_end = datetime.combine(date.today(), datetime.max.time())
    return db.query(Appointment).filter(
        Appointment.appointment_time >= today_start,
        Appointment.appointment_time <= today_end
    ).order_by(Appointment.appointment_time.asc()).all()

@app.get("/", response_class=HTMLResponse)
def index():
    try:
        with open("app/templates/index.html", "r") as f:
            return f.read()
    except Exception:
        return "<h3>Dashboard template loading... Ensure app/templates/index.html exists.</h3>"

app.include_router(voice_router)
