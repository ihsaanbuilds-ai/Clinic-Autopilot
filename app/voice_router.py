import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, Response
from fastapi.responses import Response as FastAPIResponse
from app.ai_agent import answer_clinic_query
from app.receptionist import get_available_slots, book_appointment
from app.sarvam_service import text_to_speech_sarvam

logger = logging.getLogger("voice_receptionist")

voice_router = APIRouter(prefix="/api/voice", tags=["Voice Receptionist"])

# Store last synthesized audio in memory for simple playback serving
latest_audio_cache = {}

@voice_router.get("/audio/{audio_id}")
def get_audio_stream(audio_id: str):
    audio_data = latest_audio_cache.get(audio_id)
    if not audio_data:
        return Response(status_code=404)
    return FastAPIResponse(content=audio_data, media_type="audio/wav")

@voice_router.post("/process-call")
async def process_call_turn(request: Request):
    """
    Receives caller text or transcribed voice, decides action via Gemini,
    generates warm natural Indian voice audio via Sarvam, and returns it.
    """
    data = await request.json()
    caller_text = data.get("text", "").strip()
    caller_phone = data.get("phone", "")
    lang = data.get("language_code", "en-IN")

    logger.info(f"Call turn from {caller_phone}: {caller_text}")

    if not caller_text:
        reply_text = "Hello! Welcome to Dr. Kurian's clinic. How can I help you today?"
    else:
        lower = caller_text.lower()
        if "slot" in lower or "appointment" in lower or "book" in lower:
            tomorrow_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
            slots = get_available_slots(tomorrow_str, doctor_id=1)
            if slots:
                reply_text = f"We have slots open tomorrow with Dr. Kurian at {', '.join(slots[:3])}. What time works for you?"
            else:
                reply_text = "Tomorrow appears fully booked. Would you like me to check the day after tomorrow?"
        else:
            reply_text = answer_clinic_query(caller_text, doctor_name="Dr. Kurian")

    # Generate natural voice with Sarvam
    audio_bytes = text_to_speech_sarvam(reply_text, language_code=lang, speaker="kavya")
    audio_id = f"reply_{int(datetime.now().timestamp())}"
    if audio_bytes:
        latest_audio_cache[audio_id] = audio_bytes

    return {
        "reply_text": reply_text,
        "audio_url": f"/api/voice/audio/{audio_id}" if audio_bytes else None
    }
