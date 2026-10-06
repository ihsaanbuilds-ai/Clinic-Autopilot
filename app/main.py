from app.billing_router import billing_router

from fastapi import Header, HTTPException, status
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
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from app.doctor_router import doctor_router
from app.dashboard_router import dashboard_router
from app.scheduler import start_scheduler, shutdown_scheduler

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
