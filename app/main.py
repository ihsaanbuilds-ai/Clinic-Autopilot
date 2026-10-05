from app.evolution_router import evolution_router
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
app.include_router(evolution_router)

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
