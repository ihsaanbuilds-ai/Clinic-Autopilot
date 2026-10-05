import requests
from fastapi import APIRouter, Request, BackgroundTasks
import sqlite3
import os
from datetime import datetime, date as dt_date, timedelta
from app.doctor_router import (
    parse_intent_and_entities, 
    handle_booking_flow, 
    handle_cancellation, 
    is_doctor_on_leave,
    get_available_slots,
    DB_PATH
)

evolution_router = APIRouter(prefix="/api/evolution", tags=["Evolution WhatsApp"])

EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "http://localhost:8080")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "clinic_autopilot_secret_2026")
INSTANCE_NAME = "clinic-bot"

def send_whatsapp_reply(phone: str, text: str):
    """Sends a WhatsApp text message via Evolution API."""
    url = f"{EVOLUTION_API_URL}/message/sendText/{INSTANCE_NAME}"
    headers = {
        "apikey": EVOLUTION_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "number": phone,
        "options": {
            "delay": 1200,
            "presence": "composing"
        },
        "textMessage": {
            "text": text
        }
    }
    try:
        requests.post(url, json=payload, headers=headers, timeout=5)
    except Exception as e:
        print(f"Error sending Evolution message: {e}")

@evolution_router.post("/webhook")
async def evolution_webhook(request: Request, bg_tasks: BackgroundTasks):
    data = await request.json()
    event = data.get("event")

    # Only process inbound messages from real users (ignore self messages)
    if event == "messages.upsert":
        msg_data = data.get("data", {})
        key = msg_data.get("key", {})
        
        # Don't respond to messages sent by the bot itself
        if key.get("fromMe", False):
            return {"status": "ignored_self"}

        sender_jid = key.get("remoteJid", "")
        # Remove @s.whatsapp.net suffix to get plain phone number
        sender_phone = sender_jid.split("@")[0]
        
        # Extract text message body
        message_obj = msg_data.get("message", {})
        body = (
            message_obj.get("conversation") or 
            message_obj.get("extendedTextMessage", {}).get("text") or 
            ""
        ).strip()

        if not body:
            return {"status": "no_text"}

        push_name = msg_data.get("pushName", "Patient")

        # Process message through your booking engine
        reply_text = process_patient_message(sender_phone, push_name, body)
        
        # Send reply asynchronously via Evolution API
        bg_tasks.add_task(send_whatsapp_reply, sender_phone, reply_text)

    return {"status": "ok"}

def process_patient_message(sender_phone: str, patient_name: str, body: str) -> str:
    today = dt_date.today()
    parsed = parse_intent_and_entities(body)
    intent = parsed.get("intent")

    if intent == "faq_timings":
        return f"Hello {patient_name}! Our clinic hours are Monday to Saturday, 9:00 AM – 1:00 PM and 4:30 PM – 8:00 PM. Closed on Sundays."
    
    if intent == "faq_location":
        return f"We are located at 2nd Floor, Apex Health Centre, Main Road. Google Maps: https://maps.google.com/?q=Apex+Health+Centre"

    if intent == "book":
        target_date = parsed.get("date") or str(today + timedelta(days=1))
        target_time = parsed.get("time")

        if not target_time:
            slots = get_available_slots(target_date)
            return (
                f"What time would you prefer on *{target_date}*?\n\n"
                f"Available slots: {', '.join(slots) if slots else 'No slots open'}\n"
                f"Reply with a time (e.g., *'10:30 am'*)."
            )

        success, msg = handle_booking_flow(patient_name, sender_phone, target_date, target_time)
        return msg

    if intent == "cancel":
        success, msg = handle_cancellation(sender_phone)
        return msg

    return (
        f"Hello {patient_name}, welcome to Dr. Kurian's Medical Clinic.\n\n"
        "How can I help you today?\n"
        "• Reply *'Book'* to schedule an appointment\n"
        "• Reply *'Slots tomorrow'* to check openings\n"
        "• Reply *'Location'* or *'Timings'* for clinic information"
    )
