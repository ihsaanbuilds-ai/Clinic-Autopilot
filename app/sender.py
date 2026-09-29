import os
import re
import httpx
from dotenv import load_dotenv

load_dotenv()

PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID")
ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")
GRAPH_API_URL = f"https://graph.facebook.com/v22.0/{PHONE_NUMBER_ID}/messages"

HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/json"
}

def clean_phone_number(raw_phone: str) -> str:
    digits = re.sub(r"\D", "", raw_phone)
    if digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10:
        digits = "91" + digits
    return digits

async def send_whatsapp_template(
    to_phone: str,
    template_name: str,
    lang_code: str,
    body_parameters: list
):
    payload = {
        "messaging_product": "whatsapp",
        "to": clean_phone_number(to_phone),
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": lang_code},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": str(p)} for p in body_parameters]
                }
            ]
        }
    }
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(GRAPH_API_URL, headers=HEADERS, json=payload)
            return resp.json()
        except Exception as e:
            print(f"[DISPATCH ERROR] Failed sending to {to_phone}: {e}")
            return {"error": str(e)}
