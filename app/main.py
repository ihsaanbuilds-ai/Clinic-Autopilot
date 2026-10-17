"""FastAPI application entry point for the clinic receptionist.

Wires together the routers, serves the operator dashboard, and manages the
reminder scheduler lifecycle. Admin-only routes are protected by ``verify_admin``.
"""
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import config
from app.auth import verify_admin
from app.billing_router import billing_router
from app.dashboard_router import dashboard_router
from app.database import get_sqlite_connection
from app.doctor_router import doctor_router
from app.scheduler import shutdown_scheduler, start_scheduler
from app.voice_router import voice_router

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()
    try:
        yield
    finally:
        shutdown_scheduler()


app = FastAPI(title=f"{config.CLINIC_NAME} - Receptionist AI", lifespan=lifespan)

# Mount routers
app.include_router(doctor_router)
app.include_router(dashboard_router)
app.include_router(voice_router)
app.include_router(billing_router)

# Mount static UI
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def serve_dashboard():
    index_file = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"status": "online", "clinic": config.CLINIC_NAME}


@app.post("/api/appointments/{appt_id}/status", dependencies=[Depends(verify_admin)])
def update_appointment_status(appt_id: int, status_update: dict):
    new_status = status_update.get("status")
    allowed = {"completed", "no_show", "confirmed", "cancelled"}
    if new_status not in allowed:
        raise HTTPException(status_code=400, detail="Invalid appointment status")

    conn = get_sqlite_connection()
    try:
        c = conn.cursor()
        c.execute("UPDATE appointments SET status = ? WHERE id = ?", (new_status, appt_id))
        if c.rowcount == 0:
            raise HTTPException(status_code=404, detail=f"Appointment #{appt_id} not found")
        conn.commit()
        return {"status": "success", "appointment_id": appt_id, "new_status": new_status}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()
