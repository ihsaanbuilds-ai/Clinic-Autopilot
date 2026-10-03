import os
import logging
from twilio.rest import Client

logger = logging.getLogger("clinic_notifier")

ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")

def format_whatsapp_number(raw_phone: str) -> str:
    phone = raw_phone.strip().replace(" ", "").replace("-", "")
    if not phone.startswith("whatsapp:"):
        if not phone.startswith("+"):
            phone = f"+{phone}"
        phone = f"whatsapp:{phone}"
    return phone

def send_whatsapp_message(to_phone: str, body: str) -> bool:
    if not ACCOUNT_SID or not AUTH_TOKEN:
        logger.warning("Twilio credentials not configured; logging message locally:")
        logger.info(f"TO: {to_phone} | BODY:\n{body}")
        return False

    try:
        client = Client(ACCOUNT_SID, AUTH_TOKEN)
        recipient = format_whatsapp_number(to_phone)
        client.messages.create(
            from_=TWILIO_NUMBER,
            to=recipient,
            body=body
        )
        return True
    except Exception as e:
        logger.error(f"Failed to send outbound WhatsApp to {to_phone}: {e}")
        return False
