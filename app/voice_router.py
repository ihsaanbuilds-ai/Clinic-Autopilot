import os
import re
import logging
from fastapi import APIRouter, Request, Form
from fastapi.responses import Response
from twilio.twiml.voice_response import VoiceResponse, Gather, Dial
from twilio.rest import Client
from google import genai
from google.genai import types
from dotenv import load_dotenv
from app.voice_tools import check_doctor_availability, book_appointment_via_voice

load_dotenv()

logger = logging.getLogger("voice_router")
voice_router = APIRouter(prefix="/voice", tags=["Voice AI Receptionist"])

ACTIVE_MODEL = "gemini-3.5-flash"
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY", "").strip())
BASE_URL = os.getenv("PUBLIC_URL", "https://synthesis-paternity-slot.ngrok-free.dev")
DOCTOR_PHONE = os.getenv("DOCTOR_PHONE", "+919876543210")
TWILIO_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")

# Critical emergency patterns for instant deterministic detection
EMERGENCY_REGEX = re.compile(
    r"\b(chest pain|heart attack|can\'?t breathe|cannot breathe|shortness of breath|"
    r"stroke|unconscious|fainted|heavy bleeding|bleeding heavily|severe seizure|"
    r"anaphylaxis|swallowed poison|overdose|emergency)\b",
    re.IGNORECASE
)

SYSTEM_PROMPT = """You are Maya, a warm, polite, and professional medical receptionist for Dr. Kurian's clinic.
Clinic Details:
- Hours: Monday to Saturday, 9:00 AM to 6:00 PM.
- Walk-ins: Always welcomed anytime during working hours.
- Consultation fee: 500 INR.

STRICT MEDICAL SAFETY RULES (NON-NEGOTIABLE):
1. NO MEDICAL ADVICE: You are an AI receptionist, NOT a doctor. You must NEVER diagnose conditions, assess symptoms, or recommend/prescribe medications (not even Paracetamol, Ibuprofen, or cough syrup). If a patient asks for medical advice, state politely that you cannot provide medical advice and offer to book an in-person consultation with Dr. Kurian.
2. SCHEDULING: Collect patient name, desired date, and desired time. Use the book_appointment_tool to book slots.
3. CONCISENESS: Keep answers strictly within 1 to 2 spoken sentences. Do not use asterisks, markdown, or lists."""

def check_availability_tool(date: str, time: str) -> str:
    """Checks if Dr. Kurian is available at the given date and time."""
    is_available = check_doctor_availability(date, time)
    return "Available" if is_available else "Not available"

def book_appointment_tool(name: str, phone: str, date: str, time: str) -> str:
    """Books a clinic appointment for the patient."""
    res = book_appointment_via_voice(name, phone, date, time)
    return res.get("message", "Appointment confirmed.")

TOOL_MAP = {
    "check_availability_tool": check_availability_tool,
    "book_appointment_tool": book_appointment_tool
}

def trigger_doctor_emergency_sms(patient_phone: str, caller_statement: str):
    """Sends immediate SMS to Dr. Kurian alerting him of an emergency call attempt."""
    try:
        twilio_client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
        twilio_client.messages.create(
            to=DOCTOR_PHONE,
            from_=TWILIO_NUMBER,
            body=(
                f"🚨 [URGENT EMERGENCY CALL DETECTED]\n"
                f"Caller: {patient_phone}\n"
                f"Said: \"{caller_statement}\"\n"
                f"The caller was transferred or advised to call emergency services."
            )
        )
        print(f"🚨 [EMERGENCY SMS SENT] Alert dispatched to {DOCTOR_PHONE}")
    except Exception as e:
        print(f"⚠️ [EMERGENCY SMS ERROR]: {e}")

@voice_router.post("/incoming")
async def incoming_call(request: Request):
    """Greets caller and captures speech."""
    response = VoiceResponse()
    gather = Gather(
        input="speech",
        action=f"{BASE_URL}/voice/respond",
        method="POST",
        speech_timeout="auto",
        language="en-US"
    )
    gather.say(
        "Hello! Thank you for calling Dr. Kurian's clinic. I am Maya, your virtual receptionist. "
        "How can I help you today? You can schedule a visit or check our walk-in hours.",
        voice="Polly.Danielle-Neural"
    )
    response.append(gather)
    response.redirect(f"{BASE_URL}/voice/incoming")
    return Response(content=str(response), media_type="application/xml")

@voice_router.post("/respond")
async def respond_call(
    SpeechResult: str = Form(None),
    From: str = Form(None)
):
    """Handles caller dialogue with deterministic emergency triage and LLM safety checks."""
    print(f"🎙️ [VOICE RECEIVED] From: {From} | Said: {SpeechResult}")
    response = VoiceResponse()

    if not SpeechResult:
        gather = Gather(
            input="speech",
            action=f"{BASE_URL}/voice/respond",
            method="POST",
            speech_timeout="auto",
            language="en-US"
        )
        gather.say("I didn't quite catch that. Could you please say that again?", voice="Polly.Danielle-Neural")
        response.append(gather)
        return Response(content=str(response), media_type="application/xml")

    # LEVEL 1 SAFETY: Instant Emergency Keyword Detection
    if EMERGENCY_REGEX.search(SpeechResult):
        print(f"🚨 [EMERGENCY TRIGGERED] Caller reported: {SpeechResult}")
        trigger_doctor_emergency_sms(From, SpeechResult)

        response.say(
            "This sounds like a medical emergency. I am transferring you directly to Dr. Kurian right now. "
            "If this call does not connect immediately, please hang up and dial 1 0 8 or 1 1 2 without delay.",
            voice="Polly.Danielle-Neural"
        )
        dial = Dial(caller_id=TWILIO_NUMBER, timeout=20)
        dial.number(DOCTOR_PHONE)
        response.append(dial)
        return Response(content=str(response), media_type="application/xml")

    # LEVEL 2 SAFETY: Gemini with strict guardrails + Tool execution
    try:
        user_prompt = f"Caller phone: {From}. Caller said: {SpeechResult}"
        chat = client.chats.create(
            model=ACTIVE_MODEL,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                tools=[check_availability_tool, book_appointment_tool]
            )
        )
        gemini_response = chat.send_message(user_prompt)

        reply_text = ""
        if gemini_response.function_calls:
            for call in gemini_response.function_calls:
                fn_name = call.name
                fn_args = call.args or {}
                if fn_name == "book_appointment_tool" and "phone" not in fn_args:
                    fn_args["phone"] = From or DOCTOR_PHONE

                print(f"⚙️ [TOOL CALL]: {fn_name} with args {fn_args}")
                tool_func = TOOL_MAP.get(fn_name)
                if tool_func:
                    tool_result = tool_func(**fn_args)
                    follow_up = chat.send_message(f"Tool {fn_name} returned: {tool_result}")
                    reply_text = follow_up.text.strip()
        else:
            reply_text = gemini_response.text.strip() if gemini_response.text else "I am here to help. Would you like to schedule an appointment with Dr. Kurian?"

        print(f"🤖 [MAYA REPLY]: {reply_text}")
    except Exception as e:
        print(f"❌ [GEMINI CALL ERROR]: {e}")
        reply_text = "Dr. Kurian's clinic is open Monday to Saturday, 9 AM to 6 PM. How may I assist you with your visit?"

    gather = Gather(
        input="speech",
        action=f"{BASE_URL}/voice/respond",
        method="POST",
        speech_timeout="auto",
        language="en-US"
    )
    gather.say(reply_text, voice="Polly.Danielle-Neural")
    response.append(gather)

    return Response(content=str(response), media_type="application/xml")
