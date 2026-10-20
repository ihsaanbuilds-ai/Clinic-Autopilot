import os
import logging
import requests
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse
from app.ai_agent import answer_clinic_query
from app.receptionist import get_available_slots, book_appointment
from app.sarvam_service import text_to_speech_sarvam, transcribe_audio_sarvam

logger = logging.getLogger("exotel_voice")

exotel_router = APIRouter(prefix="/api/voice/exotel", tags=["Exotel Voice"])

# Cache audio buffers for playback serving
audio_cache = {}

@exotel_router.get("/audio/{audio_id}.wav")
def serve_audio(audio_id: str):
    data = audio_cache.get(audio_id)
    if not data:
        return Response(status_code=404)
    return Response(content=data, media_type="audio/wav")

@exotel_router.api_route("/incoming-call", methods=["GET", "POST"])
async def exotel_incoming_call(request: Request):
    """
    Exotel hits this URL when the patient first calls.
    Returns the initial greeting audio URL or Passthru instructions.
    """
    greeting = "Hello, thank you for calling Dr. Kurian Medical Clinic. How can I help you today?"
    audio_bytes = text_to_speech_sarvam(greeting, language_code="en-IN", speaker="kavya")
    
    audio_id = f"greet_{int(datetime.now().timestamp())}"
    if audio_bytes:
        audio_cache[audio_id] = audio_bytes
        audio_url = f"https://clinic-autopilot.onrender.com/api/voice/exotel/audio/{audio_id}.wav"
        xml_resp = f'''<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n    <Play>{audio_url}</Play>\n</Response>'''
        return Response(content=xml_resp, media_type="application/xml")

    xml_fallback = f'''<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n    <Say>{greeting}</Say>\n</Response>'''
    return Response(content=xml_fallback, media_type="application/xml")

@exotel_router.api_route("/process-response", methods=["GET", "POST"])
async def exotel_process_response(request: Request):
    """
    Exotel forwards caller recording or transcription here.
    """
    if request.method == "POST":
        form_data = await request.form()
        params = dict(form_data)
    else:
        params = dict(request.query_params)

    caller_phone = params.get("From", "")
    recording_url = params.get("RecordingUrl", "")
    spoken_text = params.get("digits", "") or params.get("Speech", "")

    # If Exotel sends a recording URL of the caller speaking
    if recording_url and not spoken_text:
        try:
            r = requests.get(recording_url, timeout=10)
            if r.status_code == 200:
                spoken_text = transcribe_audio_sarvam(r.content, filename="caller.wav") or ""
        except Exception as e:
            logger.error(f"failed to fetch exotel recording: {e}")

    logger.info(f"exotel caller {caller_phone} said: {spoken_text}")

    if not spoken_text:
        reply_text = "I did not catch that. Please tell me if you would like to book an appointment or check doctor timings."
    else:
        lower = spoken_text.lower()
        if any(w in lower for w in ["slot", "appointment", "book", "timing", "doctor"]):
            tomorrow_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
            slots = get_available_slots(tomorrow_str, doctor_id=1)
            if slots:
                reply_text = f"We have slots available tomorrow with Dr. Kurian at {', '.join(slots[:3])}. What time suits you?"
            else:
                reply_text = "Tomorrow appears fully booked. Would you like me to check the following day?"
        else:
            reply_text = answer_clinic_query(spoken_text, doctor_name="Dr. Kurian")

    # Synthesize reply with Sarvam Bulbul
    audio_bytes = text_to_speech_sarvam(reply_text, language_code="en-IN", speaker="kavya")
    audio_id = f"resp_{int(datetime.now().timestamp())}"
    if audio_bytes:
        audio_cache[audio_id] = audio_bytes
        audio_url = f"https://clinic-autopilot.onrender.com/api/voice/exotel/audio/{audio_id}.wav"
        xml_resp = f'''<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n    <Play>{audio_url}</Play>\n</Response>'''
        return Response(content=xml_resp, media_type="application/xml")

    xml_fallback = f'''<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n    <Say>{greeting}</Say>\n</Response>'''
    return Response(content=xml_fallback, media_type="application/xml")
