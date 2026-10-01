import os
import httpx
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "").strip()
META_PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID", "").strip()

def clean_phone_number(phone: str) -> str:
    cleaned = "".join(filter(str.isdigit, str(phone)))
    if len(cleaned) == 10:
        return f"91{cleaned}"
    return cleaned

async def send_whatsapp_reminder(to_phone: str, patient_name: str, doctor_name: str, appt_time: datetime) -> bool:
    clean_phone = clean_phone_number(to_phone)
    if not META_ACCESS_TOKEN or not META_PHONE_NUMBER_ID:
        print(f"[META SENDER SKIPPED] No API credentials set. Logged for {clean_phone}")
        return False

    url = f"https://graph.facebook.com/v22.0/{META_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {META_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    formatted_time = appt_time.strftime("%d %b at %I:%M %p")

    # Uses the appointment_reminder template with language 'en'
    payload = {
        "messaging_product": "whatsapp",
        "to": clean_phone,
        "type": "template",
        "template": {
            "name": "appointment_reminder",
            "language": {"code": "en"},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": patient_name},
                        {"type": "text", "text": doctor_name},
                        {"type": "text", "text": formatted_time}
                    ]
                }
            ]
        }
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                print(f"✅ [WHATSAPP DISPATCHED] Custom reminder sent to {clean_phone}")
                return True
            else:
                print(f"⚠️ [CUSTOM TEMPLATE FAILED - {resp.status_code}]: {resp.text}")
                print("Falling back to hello_world while template is in review...")
                fallback_payload = {
                    "messaging_product": "whatsapp",
                    "to": clean_phone,
                    "type": "template",
                    "template": {
                        "name": "hello_world",
                        "language": {"code": "en_US"}
                    }
                }
                fb_resp = await client.post(url, headers=headers, json=fallback_payload)
                return fb_resp.status_code == 200
        except Exception as e:
            print(f"❌ [DISPATCH EXCEPTION] {e}")
            return False

async def send_whatsapp_text(to_phone: str, message: str) -> bool:
    clean_phone = clean_phone_number(to_phone)
    if not META_ACCESS_TOKEN or not META_PHONE_NUMBER_ID:
        print(f"[META SENDER SKIPPED] Text: {message} -> {clean_phone}")
        return False

    url = f"https://graph.facebook.com/v22.0/{META_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {META_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": clean_phone,
        "type": "text",
        "text": {"body": message}
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(url, headers=headers, json=payload)
            return resp.status_code == 200
        except Exception as e:
            print(f"❌ [TEXT DISPATCH FAILED] {e}")
            return False

# Compatibility alias
async def send_whatsapp_template(to_phone: str, template_name: str = "hello_world") -> bool:
    return await send_whatsapp_reminder(to_phone, "Valued Patient", "Clinic Doctor", datetime.now())
