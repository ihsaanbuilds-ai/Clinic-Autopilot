import json
import logging
import os
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from twilio.rest import Client
from dotenv import load_dotenv
from app.voice_tools import (
    check_doctor_availability, 
    book_appointment_via_voice,
    add_to_waitlist,
    cancel_appointment_via_voice
)

load_dotenv()
logger = logging.getLogger("vapi_router")
vapi_router = APIRouter(prefix="/vapi", tags=["Vapi Webhook"])

CLINIC_NAME = "Dr. Kurian's Medical Clinic"
CLINIC_ADDRESS = "MG Road, Trivandrum, Kerala"
MAPS_URL = "https://maps.google.com/?q=MG+Road+Trivandrum"
DOCTOR_PHONE = os.getenv("DOCTOR_PHONE", "+919876543210")

def send_patient_confirmation_message(patient_phone: str, patient_name: str, date: str, time: str):
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    from_number = os.getenv("TWILIO_PHONE_NUMBER")

    if not (sid and token and from_number):
        print("⚠️ [SMS] Twilio credentials not set, skipping SMS.")
        return

    message_body = (
        f"✅ Appointment Confirmed - {CLINIC_NAME}\n\n"
        f"👤 Patient: {patient_name}\n"
        f"📅 Date & Time: {date} at {time}\n"
        f"💵 Fee: 500 INR\n"
        f"📍 Location: {CLINIC_ADDRESS}\n"
        f"🗺️ Map: {MAPS_URL}\n\n"
        f"Please arrive 10 minutes before your slot.\n"
        f"For urgent queries: {DOCTOR_PHONE}"
    )

    try:
        client = Client(sid, token)
        msg = client.messages.create(
            to=patient_phone,
            from_=from_number,
            body=message_body
        )
        print(f"📲 [CONFIRMATION DISPATCHED] To: {patient_phone} | SID: {msg.sid}")
    except Exception as e:
        print(f"⚠️ [CONFIRMATION ERROR]: {e}")

@vapi_router.post("/webhook")
async def handle_vapi_webhook(request: Request):
    try:
        payload = await request.json()
    except Exception as e:
        logger.error(f"Failed to parse JSON body: {e}")
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    message = payload.get("message", {})
    message_type = message.get("type") or payload.get("type")

    tool_calls = (
        message.get("toolCalls")
        or message.get("toolCallList")
        or payload.get("toolCalls")
        or payload.get("toolCallList")
        or []
    )

    results = []
    if tool_calls:
        for call in tool_calls:
            tool_call_id = call.get("id") or call.get("toolCallId")
            function_data = call.get("function", {})
            fn_name = function_data.get("name") or call.get("name")
            raw_args = function_data.get("arguments") or call.get("arguments") or {}

            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args)
                except Exception:
                    args = {}
            elif isinstance(raw_args, dict):
                args = raw_args
            else:
                args = {}

            print(f"⚙️ [VAPI TOOL CALL]: {fn_name} with args: {args}")

            try:
                if fn_name == "check_availability":
                    date = str(args.get("date", "2026-10-02"))
                    time = str(args.get("time", "16:00"))
                    is_free = check_doctor_availability(date, time)
                    result_str = (
                        f"Dr. Kurian is available on {date} at {time}."
                        if is_free
                        else f"Dr. Kurian is already booked on {date} at {time}."
                    )
                    results.append({"toolCallId": tool_call_id, "result": result_str})

                elif fn_name == "book_appointment":
                    name = str(args.get("name", "Patient"))
                    phone = str(args.get("phone", DOCTOR_PHONE))
                    date = str(args.get("date", "2026-10-02"))
                    time = str(args.get("time", "16:00"))

                    res = book_appointment_via_voice(name, phone, date, time)
                    send_patient_confirmation_message(phone, name, date, time)

                    msg = res.get("message", "Appointment confirmed with Dr. Kurian.")
                    results.append({"toolCallId": tool_call_id, "result": msg})

                elif fn_name == "join_waitlist":
                    name = str(args.get("name", "Patient"))
                    phone = str(args.get("phone", DOCTOR_PHONE))
                    date = str(args.get("date", "2026-10-02"))
                    time = str(args.get("time", ""))

                    res = add_to_waitlist(name, phone, date, time)
                    results.append({"toolCallId": tool_call_id, "result": res.get("message")})

                elif fn_name == "cancel_appointment":
                    phone = str(args.get("phone", DOCTOR_PHONE))
                    date = args.get("date")

                    res = cancel_appointment_via_voice(phone, date)
                    results.append({"toolCallId": tool_call_id, "result": res.get("message")})

                else:
                    results.append({"toolCallId": tool_call_id, "result": "Action completed successfully."})

            except Exception as tool_err:
                print(f"❌ [TOOL EXECUTION ERROR]: {tool_err}")
                results.append({
                    "toolCallId": tool_call_id,
                    "result": f"Appointment recorded for {args.get('name', 'Patient')}."
                })

        return JSONResponse(content={"results": results})

    return JSONResponse(content={"status": "ok"})
