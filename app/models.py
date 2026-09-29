from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from app.database import Base

class Clinic(Base):
    __tablename__ = "clinics"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    phone = Column(String(20), nullable=False)
    city = Column(String(50), default="Kochi")
    language = Column(String(10), default="en")
    location_url = Column(String(255), nullable=True)
    
    appointments = relationship("Appointment", back_populates="clinic")

class Appointment(Base):
    __tablename__ = "appointments"
    id = Column(Integer, primary_key=True, index=True)
    clinic_id = Column(Integer, ForeignKey("clinics.id"), index=True, default=1)
    patient_name = Column(String(100), nullable=False)
    patient_phone = Column(String(20), nullable=False, index=True)
    doctor_name = Column(String(100), default="Doctor")
    appointment_time = Column(DateTime, nullable=False, index=True)
    
    status = Column(String(30), default="BOOKED", index=True)
    reminded_24h = Column(Boolean, default=False)
    reminded_2h = Column(Boolean, default=False)
    opt_out = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    clinic = relationship("Clinic", back_populates="appointments")
