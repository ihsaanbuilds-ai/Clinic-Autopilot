from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base

class Clinic(Base):
    __tablename__ = "clinics"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, default="City Dental Studio")
    phone_number_id = Column(String, nullable=True)

class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True, index=True)
    patient_name = Column(String, nullable=False)
    patient_phone = Column(String, nullable=False, index=True)
    doctor_name = Column(String, default="Dr. Kurian")
    appointment_time = Column(DateTime, nullable=False, index=True)
    status = Column(String, default="BOOKED")  # BOOKED, CONFIRMED, CANCELLED, NO_SHOW, ARRIVED, RESCHEDULE_REQUESTED
    reminder_24h_sent = Column(Boolean, default=False)
    reminder_2h_sent = Column(Boolean, default=False)
    opt_out = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class WaitlistEntry(Base):
    __tablename__ = "waitlist"

    id = Column(Integer, primary_key=True, index=True)
    patient_name = Column(String, nullable=False)
    patient_phone = Column(String, nullable=False)
    preferred_doctor = Column(String, default="Dr. Kurian")
    offered = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
