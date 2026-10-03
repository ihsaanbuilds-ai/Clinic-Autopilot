import re
from datetime import date as dt_date, timedelta

WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6
}

def extract_date(text: str) -> str | None:
    lower = text.lower()
    today = dt_date.today()
    
    if "day after tomorrow" in lower:
        return str(today + timedelta(days=2))
    if "tomorrow" in lower:
        return str(today + timedelta(days=1))
    if "today" in lower or "tonight" in lower:
        return str(today)

    iso_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if iso_match:
        return iso_match.group(1)

    for day_name, day_idx in WEEKDAYS.items():
        if re.search(rf"\b{day_name}\b", lower):
            days_ahead = (day_idx - today.weekday() + 7) % 7
            if days_ahead == 0:
                days_ahead = 7
            return str(today + timedelta(days=days_ahead))
            
    return None

def extract_time(text: str) -> str | None:
    pattern = r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b"
    matches = re.findall(pattern, text.lower())
    
    for m in matches:
        clean = m.strip()
        if len(clean) <= 8 and any(char.isdigit() for char in clean):
            if clean.isdigit():
                val = int(clean)
                if 9 <= val <= 17:
                    return f"{val:02d}:00"
                elif 1 <= val <= 5:
                    return f"{val+12:02d}:00"
            else:
                return clean
    return None

def parse_patient_intent(text: str) -> dict:
    lower = text.lower().strip()
    
    # Priority: Emergency Triaging Override
    emergency_keywords = ["chest pain", "heart attack", "unconscious", "cannot breathe", "severe bleeding", "emergency", "stroke"]
    if any(k in lower for k in emergency_keywords):
        return {"intent": "emergency"}

    # Reschedule
    if any(k in lower for k in ["reschedule", "change time", "move appointment", "postpone"]):
        return {
            "intent": "reschedule",
            "date": extract_date(lower),
            "time": extract_time(lower)
        }

    # Cancellation
    if any(k in lower for k in ["cancel", "drop", "revoke", "cannot make it"]):
        id_match = re.search(r"\bcancel\s+(\d+)\b", lower)
        appt_id = int(id_match.group(1)) if id_match else None
        return {"intent": "cancel", "appt_id": appt_id}
        
    # Status / Lookup
    if any(k in lower for k in ["status", "my appointment", "when is my", "check booking"]):
        return {"intent": "status"}

    # Slot inquiries (Check slots/openings before general FAQ)
    if any(re.search(rf"\b{k}\b", lower) for k in ["slots", "available", "free", "openings"]):
        return {"intent": "inquire_slots", "date": extract_date(lower) or str(dt_date.today())}

    # Clinic FAQs (Use word boundaries so 'openings' doesn't trigger 'open')
    if any(re.search(rf"\b{k}\b", lower) for k in ["fee", "fees", "cost", "charge", "price", "how much"]):
        return {"intent": "faq_fees"}
    if any(re.search(rf"\b{k}\b", lower) for k in ["address", "location", "where", "directions", "map"]):
        return {"intent": "faq_location"}
    if any(re.search(rf"\b{k}\b", lower) for k in ["timing", "timings", "hours", "open", "working days"]):
        return {"intent": "faq_hours"}

    # Booking requests (detected time or booking verbs)
    detected_time = extract_time(lower)
    if any(re.search(rf"\b{k}\b", lower) for k in ["book", "schedule", "appointment", "reserve", "fix"]) or detected_time:
        return {
            "intent": "book",
            "date": extract_date(lower),
            "time": detected_time
        }

    return {"intent": "greeting"}
