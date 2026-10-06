from app.billing_router import billing_router

from fastapi import Header, status, Header, HTTPException, status
import os

ADMIN_SECRET = os.getenv("ADMIN_SECRET", "clinic-pilot-secure-key-2026")

def verify_admin_key(x_admin_secret: str = Header(None)):
    if x_admin_secret != ADMIN_SECRET:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: Invalid or missing X-Admin-Secret header"
        )

from app.voice_router import voice_router
import os
from fastapi import Header, status, FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from app.doctor_router import doctor_router
from app.dashboard_router import dashboard_router
from app.scheduler import start_scheduler, shutdown_scheduler


ADMIN_SECRET = os.getenv("ADMIN_SECRET", "pilot_secret_2026")

def verify_admin_access(
    x_admin_secret: str = Header(None),
    admin_token: str = None
):
    token = x_admin_secret or admin_token
    if not token or token != ADMIN_SECRET:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: Valid X-Admin-Secret header or admin_token parameter required."
        )
    return True

app = FastAPI(title="Dr. Kurian's Medical Clinic - Receptionist AI")

# Mount Routers
app.include_router(doctor_router)
app.include_router(dashboard_router)
app.include_router(voice_router)

# Mount Static UI
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
def serve_dashboard():
    index_file = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"status": "online", "clinic": "Dr. Kurian's Medical Clinic"}

@app.on_event("startup")
def on_startup():
    start_scheduler()

@app.on_event("shutdown")
def on_shutdown():
    shutdown_scheduler()

app.include_router(billing_router)


@app.post("/api/appointments/{appt_id}/status")
async def update_appointment_status(appt_id: int, status_update: dict):
    new_status = status_update.get("status")
    if new_status not in ["completed", "no_show", "confirmed", "cancelled"]:
        raise HTTPException(status_code=400, detail="Invalid appointment status")
    
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    c = conn.cursor()
    try:
        c.execute("UPDATE appointments SET status = ? WHERE id = ?", (new_status, appt_id))
        conn.commit()
        return {"status": "success", "appointment_id": appt_id, "new_status": new_status}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()
