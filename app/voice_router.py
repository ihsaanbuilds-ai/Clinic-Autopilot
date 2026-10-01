import os
import json
import logging
from fastapi import APIRouter, Request, Form
from fastapi.responses import Response
from twilio.twiml.voice_response import VoiceResponse, Gather
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

SYSTEM_PROMPT = """You are Maya, a warm, polite, and professional medical receptionist for Dr. Kurian's clinic.
Your capabilities:
1. Answer questions about clinic hours (Mon-Sat, 9:00 AM - 6:00 PM), walk-ins (always welcome), and fee (500 INR).
2. Book appointments: Ask for the patient's name, preferred date (YYYY-MM-DD or readable date), and preferred time.
3. Once you have the patient's name, date, and time, call the book_appointment_tool to finalize the booking.
4. Keep spoken responses short, natural, and conversational (1-2 sentences). Do not use markdown, asterisks, or lists."""

# Declare tools for Gemini
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

@voice_router.post("/incoming")
async def incoming_call(request: Request):
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
        "How can I help you today? You can ask to book an appointment or check our walk-in hours.",
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

    # Process speech with Gemini + Tools
    try:
        user_prompt = f"Caller phone number is {From}. Caller said: {SpeechResult}"
        
        chat = client.chats.create(
            model=ACTIVE_MODEL,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                tools=[check_availability_tool, book_appointment_tool]
            )
        )
        gemini_response = chat.send_message(user_prompt)
        
        # Check if the model triggered a function call
        reply_text = ""
        if gemini_response.function_calls:
            for call in gemini_response.function_calls:
                fn_name = call.name
                fn_args = call.args or {}
                if fn_name == "book_appointment_tool" and "phone" not in fn_args:
                    fn_args["phone"] = From or "+918928740867"
                
                print(f"⚙️ [TOOL CALL]: {fn_name} with args {fn_args}")
                tool_func = TOOL_MAP.get(fn_name)
                if tool_func:
                    tool_result = tool_func(**fn_args)
                    follow_up = chat.send_message(f"Tool {fn_name} returned: {tool_result}")
                    reply_text = follow_up.text.strip()
        else:
            reply_text = gemini_response.text.strip() if gemini_response.text else "I am here to help you book an appointment with Dr. Kurian."

        print(f"🤖 [MAYA REPLY]: {reply_text}")
    except Exception as e:
        print(f"❌ [GEMINI CALL ERROR]: {e}")
        reply_text = "I have noted that down. Could you please confirm your preferred time and name?"

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
