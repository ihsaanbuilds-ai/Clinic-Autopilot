"""Gemini-backed extras: Multilingual WhatsApp voice-note transcription + Receptionist AI.

Languages supported: English, Hindi, Tamil, Malayalam, Marathi (both native script and Romanized/chat script).
Medical guardrails are strictly enforced: no diagnosis, no prescriptions, immediate emergency escalation.
"""
import os
import re
import time
import logging

from dotenv import load_dotenv

load_dotenv()

from google import genai
from google.genai import types

from app import config

logger = logging.getLogger("clinic_ai")

api_key = os.getenv("GEMINI_API_KEY")
client = None

if api_key:
    try:
        client = genai.Client(api_key=api_key)
    except Exception as e:
        logger.error(f"failed to initialize gemini client: {e}")
else:
    logger.warning("GEMINI_API_KEY is not set; voice notes and AI Q&A are disabled.")

MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

CLINIC_SYSTEM_INSTRUCTION = f"""
You are the warm, polite, and attentive front desk receptionist at {config.CLINIC_NAME}, located at {config.CLINIC_ADDRESS}.

Clinic details:
- Primary doctor: {config.DOCTOR_NAME}
- Working hours: {config.CLINIC_HOURS_TEXT}
- Consultation fee: ₹{config.CONSULTATION_FEE}. Follow-up within 7 days is ₹{config.FOLLOWUP_FEE}.
- Building amenities: Wheelchair access available, lift/elevator present, basement parking for two-wheelers and cars.
- Payment modes: UPI (GPay, PhonePe, Paytm), cash, and cards at counter.

Language & Receptionist Persona Rules:
1. Multilingual Fluency: You speak fluent English, Hindi, Tamil, Malayalam, and Marathi.
2. Mirror the Patient:
   - Always reply in the exact language the patient used.
   - If the patient types in Romanized script (Hinglish, Manglish, Tanglish, Roman Marathi), reply in natural conversational Romanized script.
   - If the patient types in native script (Devanagari, Tamil, Malayalam), reply in that native script.
   - For English queries, respond in warm Indian English.
3. Natural Receptionist Etiquette:
   - Act like a real person at the clinic counter taking a call or chat.
   - Be empathetic, welcoming, and reassuring.
   - Keep replies short, conversational, and helpful (2 to 4 sentences max). Never sound like an automated robotic IVR menu.
   - Do not ask more than one question at a time. Guide the patient smoothly to pick a date or slot.
4. Strict Medical Boundary:
   - If a patient asks for a prescription, medicine recommendation, dosage, or medical opinion, decline gently in the language they used.
   - Explain that only the doctor can assess their symptoms and prescribe treatment safely during an in-person visit.
   - Suggest checking open appointment slots immediately.
5. Emergency Triage:
   - If a patient mentions severe chest pain, breathing difficulty, sudden paralysis, severe bleeding, or unconsciousness, tell them urgently to call {config.EMERGENCY_NUMBERS} or rush to the nearest emergency room.
"""

MULTILINGUAL_PRESCRIPTION_NOTICES = {
    "en": (
        "I understand you are feeling unwell, but as the clinic receptionist, I cannot prescribe medicines or suggest dosages. "
        f"Only {config.DOCTOR_NAME} can evaluate you safely. Would you like to book an appointment to see the doctor today or tomorrow?"
    ),
    "ml": (
        "സുഖമില്ലാത്തതിൽ വിഷമമുണ്ട്. എന്നാൽ റിസപ്ഷനിസ്റ്റ് ആയതുകൊണ്ട് എനിക്ക് മരുന്നുകൾ നിർദേശിക്കാൻ കഴിയില്ല. "
        f"{config.DOCTOR_NAME} നേരിട്ട് കണ്ട് പരിശോധിച്ച ശേഷം മാത്രമേ മരുന്ന് നൽകാൻ സാധിക്കൂ. നാളത്തേക്കോ മറ്റോ ഒരു അപ്പോയിന്റ്മെന്റ് ബുക്ക് ചെയ്യണോ?"
    ),
    "hi": (
        "आपकी तबियत ठीक नहीं है यह सुनकर दुख हुआ, लेकिन क्लिनिक रिसेप्शनिस्ट होने के नाते मैं आपको दवाइयां या खुराक नहीं बता सकती। "
        f"{config.DOCTOR_NAME} से मिलकर जांच करवाना ही सुरक्षित रहेगा। क्या मैं आपके लिए आज या कल का अपॉइंटमेंट बुक कर दूं?"
    ),
    "ta": (
        "உங்களுக்கு உடல்நிலை சரியில்லை என்பதை புரிந்து கொள்ள முடிகிறது, ஆனால் கிளினிக் வரவேற்பாளராக என்னால் மருந்துகளை பரிந்துரைக்க முடியாது. "
        f"{config.DOCTOR_NAME} நேரில் பார்த்து பரிசோதித்த பின்னரே மருந்து தர முடியும். உங்களுக்கு இன்றோ அல்லது நாளைக்கோ அப்பாயின்ட்மென்ட் புக் செய்யவா?"
    ),
    "mr": (
        "तुमची तब्येत बरी नाही हे ऐकून वाईट वाटले, पण क्लिनिक रिसेप्शनिस्ट म्हणून मी औषधे लिहून देऊ शकत नाही. "
        f"{config.DOCTOR_NAME} कडे प्रत्यक्ष तपासणी करून घेणेच योग्य ठरेल. मी तुमच्यासाठी आज किंवा उद्याची अपॉइंटमेंट बुक करू का?"
    )
}

PRESCRIPTION_REGEX = re.compile(
    r"\b(prescribe|prescription|medicine|medicines|tablet|tablets|syrup|antibiotic|antibiotics|dosage|dose|"
    r"marunnu|marunnukal|davai|dawa|goli|mathirai|aushadh|aushadhi)\b",
    re.IGNORECASE
)


def _call_gemini_with_retry(contents, system_instruction=None, max_retries=3):
    if not client:
        return None
    for attempt in range(max_retries + 1):
        try:
            cfg = (
                types.GenerateContentConfig(system_instruction=system_instruction)
                if system_instruction
                else None
            )
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=contents,
                config=cfg,
            )
            if response and response.text:
                return response.text.strip()
        except Exception as e:
            err_msg = str(e)
            logger.warning(f"gemini call failed (attempt {attempt + 1}/{max_retries + 1}): {err_msg}")
            if attempt < max_retries:
                # If rate-limited (429), back off appropriately
                sleep_sec = 2.5 * (attempt + 1) if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg else 0.8 * (attempt + 1)
                time.sleep(sleep_sec)
    return None


def transcribe_voice_note(audio_bytes: bytes, mime_type: str = "audio/ogg") -> str | None:
    """Transcribes patient voice notes in English, Malayalam, Tamil, Hindi, Marathi, etc."""
    if not client:
        logger.warning("gemini client unavailable for transcription")
        return None

    instruction = (
        "Transcribe this clinic patient's voice message accurately. "
        "The audio may be in English, Malayalam, Tamil, Hindi, Marathi, or code-mixed Indian English. "
        "Output ONLY the exact spoken transcription text. Do not add any commentary, notes, or explanations."
    )

    try:
        part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)
        return _call_gemini_with_retry(
            contents=[part, "Transcribe the audio accurately."],
            system_instruction=instruction,
        )
    except Exception as e:
        logger.error(f"voice note transcription failed: {e}")
        return None


def answer_clinic_query(patient_query: str, doctor_name: str = None) -> str:
    """Answers patient queries conversationally as an empathetic multilingual clinic receptionist."""
    # Check for prescription requests
    if PRESCRIPTION_REGEX.search(patient_query):
        # Quick language script heuristic for refusal
        if any('\u0D00' <= char <= '\u0D7F' for char in patient_query):
            return MULTILINGUAL_PRESCRIPTION_NOTICES["ml"]
        elif any('\u0B80' <= char <= '\u0BFF' for char in patient_query):
            return MULTILINGUAL_PRESCRIPTION_NOTICES["ta"]
        elif any('\u0900' <= char <= '\u097F' for char in patient_query):
            if any(w in patient_query.lower() for w in ["aushadh", "aahe", "karu", "kay"]):
                return MULTILINGUAL_PRESCRIPTION_NOTICES["mr"]
            return MULTILINGUAL_PRESCRIPTION_NOTICES["hi"]
        elif any(w in patient_query.lower() for w in ["marunnu", "tharaamo", "kazhikkam"]):
            return "Kshemikkanam, clinic receptionist aaya enikku marunnu nirdheshikkan kazhiyilla. Doctorodu nerittu samsaarikkaan oru appointment edukkaamo?"
        elif any(w in patient_query.lower() for w in ["dawa", "goli", "batao", "de do"]):
            return "Mujhe khed hai, par receptionist hone ke naate main koi dawa suggest nahi kar sakti. Doctor se consult karne ke liye kya main slot check karu?"
        return MULTILINGUAL_PRESCRIPTION_NOTICES["en"]

    effective_instruction = CLINIC_SYSTEM_INSTRUCTION
    if doctor_name:
        effective_instruction += f"\nNote: The patient is inquiring with or regarding {doctor_name}."

    prompt = f"Patient asks: \"{patient_query}\"\n\nReply warmly and helpfully as the receptionist in the patient's language and style."
    response = _call_gemini_with_retry(contents=prompt, system_instruction=effective_instruction)

    if response:
        return response

    return (
        f"Hello! This is the front desk at {config.CLINIC_NAME}. "
        f"Dr. Kurian is available for consultations. "
        "Would you like to check open slots for today or tomorrow?"
    )
