import os
import time
import logging
import requests

logger = logging.getLogger("clinic_notifier")

BRIDGE_SEND_URL = os.getenv("WHATSAPP_BRIDGE_URL", "http://localhost:3000/send")
BRIDGE_TIMEOUT = float(os.getenv("WHATSAPP_BRIDGE_TIMEOUT", "10"))
BRIDGE_RETRIES = int(os.getenv("WHATSAPP_BRIDGE_RETRIES", "3"))
RETRY_BACKOFF = 1.5


def send_whatsapp_message(to_phone: str, body: str) -> bool:
    """Send a WhatsApp message through the local Baileys bridge.

    Retries transient failures (bridge reconnecting / socket restarting) with
    exponential backoff. Returns True only when the bridge confirms dispatch.
    """
    clean_phone = to_phone.replace("whatsapp:", "").replace("+", "").strip()
    if not clean_phone or not body:
        logger.error("send_whatsapp_message called with empty phone or body")
        return False

    last_error = "unknown error"
    for attempt in range(1, BRIDGE_RETRIES + 1):
        retryable = True
        try:
            response = requests.post(
                BRIDGE_SEND_URL,
                json={"phone": clean_phone, "message": body},
                timeout=BRIDGE_TIMEOUT,
            )
            try:
                data = response.json()
            except ValueError:
                data = {}

            if response.ok and data.get("success"):
                return True

            last_error = data.get("error") or f"HTTP {response.status_code}"
            retryable = bool(data.get("retryable", response.status_code >= 500))
        except requests.RequestException as exc:
            last_error = str(exc)
            retryable = True

        if attempt < BRIDGE_RETRIES and retryable:
            sleep_for = RETRY_BACKOFF ** attempt
            logger.warning(
                "Bridge send failed (attempt %d/%d): %s — retrying in %.1fs",
                attempt,
                BRIDGE_RETRIES,
                last_error,
                sleep_for,
            )
            time.sleep(sleep_for)
        else:
            break

    logger.error(
        "Bridge failed to send after %d attempt(s): %s", BRIDGE_RETRIES, last_error
    )
    logger.info("UNDELIVERED -> +%s | BODY:\n%s", clean_phone, body)
    return False
