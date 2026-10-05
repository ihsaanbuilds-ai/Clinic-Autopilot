import os
from dotenv import load_dotenv

load_dotenv()

CLINIC_NAME = os.getenv("CLINIC_NAME", "Dr. Kurian's Medical Clinic")
DOCTOR_NAME = os.getenv("DOCTOR_NAME", "Dr. Kurian")
DOCTOR_PHONE = os.getenv("DOCTOR_PHONE", "+918928740867")
CONSULTATION_FEE = os.getenv("CONSULTATION_FEE", "500")
FOLLOWUP_FEE = os.getenv("FOLLOWUP_FEE", "300")
CLINIC_LOCATION = os.getenv("CLINIC_LOCATION", "2nd Floor, Apex Health Centre, Main Road, Thiruvananthapuram")
GOOGLE_REVIEW_LINK = os.getenv("GOOGLE_REVIEW_LINK", "https://g.page/r/dr-kurian-clinic/review")
