import os
from datetime import datetime, date
from fastapi import FastAPI, Request, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.database import engine, Base, get_db
from app.models import Clinic, Appointment
from app.sender import clean_phone_number
from app.scheduler import start_scheduler

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Receptionist Autopilot")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "clinic_autopilot_verify_token_2026")

@app.on_event("startup")
def startup_event():
    db = next(get_db())
    if not db.query(Clinic).first():
        sample_clinic = Clinic(
            name="City Dental Studio",
            phone="919847000000",
            city="Kochi",
            language="en"
        )
        db.add(sample_clinic)
        db.commit()
    start_scheduler()

class AppointmentCreate(BaseModel):
    patient_name: str
    patient_phone: str
    doctor_name: str
    appointment_time: datetime

@app.get("/", response_class=HTMLResponse)
async def serve_reception_ui():
    return FileResponse("app/static/index.html")

@app.post("/api/appointments")
def create_appointment(data: AppointmentCreate, db: Session = Depends(get_db)):
    clean_phone = clean_phone_number(data.patient_phone)
    appt = Appointment(
        patient_name=data.patient_name.strip(),
        patient_phone=clean_phone,
        doctor_name=data.doctor_name.strip(),
        appointment_time=data.appointment_time,
        status="BOOKED"
    )
    db.add(appt)
    db.commit()
    db.refresh(appt)
    return {"status": "success", "id": appt.id}

@app.get("/api/appointments/today")
def get_today_board(db: Session = Depends(get_db)):
    today_start = datetime.combine(date.today(), datetime.min.time())
    today_end = datetime.combine(date.today(), datetime.max.time())
    
    appts = db.query(Appointment).filter(
        Appointment.appointment_time >= today_start,
        Appointment.appointment_time <= today_end
    ).order_by(Appointment.appointment_time.asc()).all()
    
    return [
        {
            "id": a.id,
            "patient_name": a.patient_name,
            "patient_phone": a.patient_phone,
            "doctor_name": a.doctor_name,
            "time": a.appointment_time.strftime("%I:%M %p"),
            "status": a.status
        }
        for a in appts
    ]

@app.patch("/api/appointments/{appt_id}/status")
def update_status(appt_id: int, new_status: str, db: Session = Depends(get_db)):
    appt = db.query(Appointment).filter(Appointment.id == appt_id).first()
    if not appt:
        raise HTTPException(status_code=404, detail="Not found")
    appt.status = new_status.upper()
    db.commit()
    return {"status": "updated", "new_status": appt.status}

@app.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token")
):
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        return Response(content=hub_challenge, media_type="text/plain", status_code=200)
    raise HTTPException(status_code=403, detail="Verification token mismatch")

@app.post("/webhook")
async def handle_whatsapp_webhook(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    try:
        entry = data.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])
        
        if not messages:
            return {"status": "ignored"}

        msg = messages[0]
        from_phone = clean_phone_number(msg.get("from", ""))
        msg_type = msg.get("type")

        appt = db.query(Appointment).filter(
            Appointment.patient_phone == from_phone,
            Appointment.status.in_(["BOOKED", "CONFIRMED", "RESCHEDULE_REQUESTED"])
        ).order_by(Appointment.appointment_time.desc()).first()

        action = None
        if msg_type == "button":
            action = msg.get("button", {}).get("payload", "").upper()
        elif msg_type == "text":
            text = msg.get("text", {}).get("body", "").strip().upper()
            if text == "STOP":
                if appt:
                    appt.opt_out = True
                    db.commit()
                return {"status": "opted_out"}
            action = text

        if appt and action:
            if "CONFIRM" in action:
                appt.status = "CONFIRMED"
            elif "CANCEL" in action:
                appt.status = "CANCELLED"
            elif "RESCHEDULE" in action:
                appt.status = "RESCHEDULE_REQUESTED"
            db.commit()

    except Exception as e:
        print(f"[WEBHOOK ERROR] {e}")

    return {"status": "success"}
