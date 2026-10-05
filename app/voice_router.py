from fastapi import APIRouter, Request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from app.nlp_parser import parse_patient_intent
from app.receptionist import get_available_slots, book_appointment, cancel_patient_appointment
import os
import re

voice_router = APIRouter(prefix="/api/voice", tags=["Voice AI Receptionist"])
CLINIC_NAME = "Dr. Kurian's Medical Clinic"

@voice_router.post("/incoming")
async def voice_incoming(request: Request):
    """Answers incoming voice calls and prompts the caller."""
    resp = VoiceResponse()
    gather = Gather(
        input="speech",
        action="/api/voice/process-speech",
        method="POST",
        speech_timeout="auto",
        language="en-IN"
    )
    gather.say(
        f"Hello and thank you for calling {CLINIC_NAME}. "
        "I am your AI receptionist. You can ask to book an appointment, check available slots, or inquire about clinic hours. How can I help you today?",
        voice="Polly.Aditi",
        language="en-IN"
    )
    resp.append(gather)
    # Fallback if no speech detected
    resp.redirect("/api/voice/incoming")
    return Response(content=str(resp), media_type="application/xml")

@voice_router.post("/process-speech")
async def process_speech(request: Request):
    form_data = await request.form()
    speech_result = form_data.get("SpeechResult") or ""
    caller_phone = (form_data.get("From") or "").replace("+", "").strip()

    resp = VoiceResponse()

    if not speech_result:
        gather = Gather(input="speech", action="/api/voice/process-speech", method="POST", language="en-IN")
        gather.say("I didn't quite catch that. Could you please repeat your request?", voice="Polly.Aditi", language="en-IN")
        resp.append(gather)
        return Response(content=str(resp), media_type="application/xml")

    parsed = parse_patient_intent(speech_result)
    intent = parsed["intent"]

    if intent == "emergency":
        resp.say(
            "This sounds like a critical medical emergency. Please hang up and immediately dial 108 or 112, or visit the nearest hospital emergency room.",
            voice="Polly.Aditi"
        )
        resp.hangup()
        return Response(content=str(resp), media_type="application/xml")

    if intent == "faq_fees":
        reply = "Dr. Kurian's general consultation fee is 500 rupees. Follow-up visits within seven days are 300 rupees."
    elif intent == "faq_hours":
        reply = "The clinic is open Monday through Saturday from 9 AM to 1 PM, and 2 PM to 5:30 PM. We are closed on Sundays."
    elif intent == "faq_location":
        reply = "The clinic is located on the second floor of Apex Health Centre, Main Road, opposite the Central Metro Station."
    elif intent == "inquire_slots":
        target = parsed.get("date") or "today"
        slots = get_available_slots(target)
        if slots:
            spoken_slots = ", ".join(slots[:4])
            reply = f"Available slots for {target} include {spoken_slots}. Which time would you prefer?"
        else:
            reply = f"I'm sorry, all slots are currently booked for {target}."
    elif intent == "book":
        target_date = parsed.get("date")
        target_time = parsed.get("time")
        if not target_time or not target_date:
            reply = "Could you please specify both the date and preferred time for your appointment?"
        else:
            ok, text = book_appointment("Phone Caller", caller_phone, target_date, target_time)
            # Strip markdown asterisks for spoken TTS
            reply = re.sub(r"[*_#]", "", text)
    elif intent == "cancel":
        text = cancel_patient_appointment(caller_phone)
        reply = re.sub(r"[*_#]", "", text)
    else:
        reply = f"You can book a visit with Dr. Kurian, check our timings, or ask for consultation fees. What would you like to do?"

    # Chain next turn
    gather = Gather(input="speech", action="/api/voice/process-speech", method="POST", language="en-IN")
    gather.say(reply, voice="Polly.Aditi", language="en-IN")
    gather.say("Is there anything else I can help you with?", voice="Polly.Aditi", language="en-IN")
    resp.append(gather)
    resp.say(f"Thank you for calling {CLINIC_NAME}. Have a healthy day!", voice="Polly.Aditi", language="en-IN")
    resp.hangup()

    return Response(content=str(resp), media_type="application/xml")
