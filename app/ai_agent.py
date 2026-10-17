import os
import re
import time
import logging
from dotenv import load_dotenv

load_dotenv()

from google import genai
from google.genai import types

logger = logging.getLogger("clinic_ai")

api_key = os.getenv("GEMINI_API_KEY")
client = None

if api_key:
    try:
        client = genai.Client(api_key=api_key)
    except Exception as e:
        logger.error(f"failed to initialize gemini client: {e}")
else:
    logger.warning("GEMINI_API_KEY is not set in environment or .env file.")

# The active model supported by your key
MODEL_NAME = "gemini-3.8-flash"

CLINIC_SYSTEM_INSTRUCTION = """
You are the AI Front Desk Receptionist for Dr. Kurian's Medical Clinic located in MG Road, Kochi, Kerala.

Clinic facts:
- Doctor: Dr. Kurian, MBBS, MD (General Physician & Internal Medicine). Over 15 years experience.
- Location: 2nd Floor, Apex Health Centre, MG Road, Central Junction, Kochi.
- Timings: Monday to Saturday, 9:00 AM to 1:00 PM and 2:00 PM to 6:00 PM. Closed on Sundays.
- Consultation fee: ₹500 for general consultation. Follow-up within 7 days is ₹300.
- Payment modes: UPI (GPay, PhonePe, Paytm), cash, and debit/credit cards at the clinic counter.
- Insurance: The clinic provides formal printed bills with diagnosis and registration numbers for insurance reimbursement claims. Direct cashless facility depends on the patient's TPA/policy network.
- Facilities: Lift/elevator access available, two-wheeler and four-wheeler basement parking available at Apex Health Centre. Wheelchair friendly.
- Pediatric policy: Children above 3 years are seen for general illnesses (fever, cold, cough). For neonates or specialized pediatric conditions, recommend consulting a dedicated pediatrician.

Guardrails:
1. Strict medical boundary: NEVER diagnose conditions, recommend specific medicines, or suggest dosages. Always advise booking an in-person consultation with Dr. Kurian.
2. Emergencies: For severe chest pain, breathing difficulty, or unconsciousness, tell them to call 108 or 112 immediately.
3. Tone: Warm, helpful, professional, and concise (under 3 sentences whenever possible). Use WhatsApp friendly formatting with bolding where appropriate. Always guide them to check open slots or book an appointment.
"""

def _call_gemini_with_retry(contents, system_instruction=None, max_retries=2):
    for attempt in range(max_retries + 1):
        try:
            config = types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.3,
                max_output_tokens=300
            ) if system_instruction else types.GenerateContentConfig(temperature=0.2)

            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=contents,
                config=config
            )
            if response and response.text:
                return response.text.strip()
        except Exception as e:
            err_str = str(e)
            if ("503" in err_str or "429" in err_str) and attempt < max_retries:
                time.sleep(2 * (attempt + 1))
                continue
            logger.error(f"gemini error: {e}")
            break
    return None

def answer_clinic_query(user_message: str, patient_name: str = "Patient") -> str:
    msg_lower = user_message.lower()

    # deterministic safety guardrail for prescriptions and medical advice
    if re.search(r"\b(prescribe|prescription|medicine|antibiotic|antibiotics|dosage|diagnose|fever medicine|painkiller)\b", msg_lower):
        return (
            "⚠️ *Medical Notice*:\n"
            "As an AI clinic assistant, I cannot diagnose medical conditions or prescribe medications.\n\n"
            "Please book an in-person consultation with Dr. Kurian so you can be thoroughly evaluated. "
            "Reply *'Available slots tomorrow'* to schedule an appointment."
        )

    if not client:
        return "I am Dr. Kurian's clinic assistant. How can I assist you with your appointment or visit today?"

    reply = _call_gemini_with_retry(user_message, CLINIC_SYSTEM_INSTRUCTION)
    if reply:
        return reply

    return (
        "I can help answer questions regarding Dr. Kurian's clinic timings, parking, consultation fees, or booking an appointment. "
        "Reply *'Available slots tomorrow'* to check open timings."
    )

def transcribe_and_process_voice(audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
    if not client:
        return ""
    try:
        prompt = (
            "Listen to this patient's WhatsApp voice message carefully. "
            "Transcribe exactly what they said into text. "
            "Output only the transcribed text, nothing else."
        )
        contents = [
            types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
            prompt
        ]
        text = _call_gemini_with_retry(contents)
        return text or ""
    except Exception as e:
        logger.error(f"gemini voice transcription error: {e}")
        return ""
