"""Single source of truth for clinic (tenant) identity and contact details.

All patient-facing copy reads from here so onboarding a new clinic is a matter
of setting environment variables, not editing Python strings. Defaults are a
generic Mumbai outpatient template.
"""
import os
from dotenv import load_dotenv

load_dotenv()

TIMEZONE = os.getenv("CLINIC_TIMEZONE", "Asia/Kolkata")

CLINIC_SLUG = os.getenv("CLINIC_SLUG", "citycare-clinic")
CLINIC_NAME = os.getenv("CLINIC_NAME", "CityCare Multispeciality Clinic")

# Primary consulting doctor (legacy single-doctor copy). Multi-doctor clinics
# store their real roster in the `doctors` table.
DOCTOR_NAME = os.getenv("DOCTOR_NAME", "Dr. Rohan Mehta")

CLINIC_ADDRESS = os.getenv(
    "CLINIC_ADDRESS",
    "2nd Floor, Skyline Plaza, Linking Road, Bandra West, Mumbai 400050",
)
CLINIC_LANDMARK = os.getenv("CLINIC_LANDMARK", "Opposite Bandra Talkies")
CLINIC_MAPS_URL = os.getenv(
    "CLINIC_MAPS_URL", "https://maps.google.com/?q=Bandra+West+Mumbai"
)
CLINIC_PHONE = os.getenv("CLINIC_PHONE", "+918928740867")

CONSULTATION_FEE = os.getenv("CONSULTATION_FEE", "600")
FOLLOWUP_FEE = os.getenv("FOLLOWUP_FEE", "300")

CLINIC_HOURS_TEXT = os.getenv(
    "CLINIC_HOURS_TEXT",
    "Mon – Sat: 9:00 AM – 1:00 PM & 2:00 PM – 5:30 PM (Closed Sundays)",
)

# Collecting a clinic-owned UPI ID is how the clinic keeps consultation revenue.
CLINIC_UPI_ID = os.getenv("CLINIC_UPI_ID", "citycare@upi")
CLINIC_UPI_NAME = os.getenv("CLINIC_UPI_NAME", "CityCare Clinic")

GOOGLE_REVIEW_LINK = os.getenv(
    "GOOGLE_REVIEW_LINK", "https://g.page/r/citycare-clinic/review"
)

# India national emergency + ambulance numbers.
EMERGENCY_NUMBERS = os.getenv("EMERGENCY_NUMBERS", "112 / 108")

# WhatsApp sender identity used by the Baileys bridge (informational only).
WHATSAPP_NUMBER = os.getenv("WHATSAPP_NUMBER", "+918928740867")
