import re
import sqlite3
from datetime import datetime, timedelta

def get_clinic_doctors() -> list[dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, name, specialty, consultation_fee FROM doctors WHERE active = 1")
    docs = [dict(r) for r in c.fetchall()]
    conn.close()
    return docs
from app.payment_gateway import create_payment_link
from app.followup_engine import dispatch_post_consultation_followups, handle_feedback_response
import sqlite3
import os
import re
from datetime import date as dt_date, timedelta
from fastapi import APIRouter, Response, Request
from twilio.twiml.messaging_response import MessagingResponse
from app.receptionist import (
    get_available_slots, 
    book_appointment, 
    reschedule_appointment,
    cancel_patient_appointment,
    get_patient_active_appointments,
    get_matching_waitlisted_candidate,
    promote_waitlist_candidate,
    is_date_blocked
)
from app.nlp_parser import parse_patient_intent
from app.reminders import dispatch_day_before_reminders
from app.notifier import send_whatsapp_message
from app.session_manager import get_session, update_session, clear_session
from app.escalation_manager import (
    is_patient_escalated, 
    trigger_escalation, 
    resolve_escalation
)

doctor_router = APIRouter(prefix="/api/doctor", tags=["Doctor & Patient Portal"])
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "clinic.db")
RAW_DOCTOR_PHONE = os.getenv("DOCTOR_PHONE", "+918928740867")
DOCTOR_PHONE_DIGITS = re.sub(r"\D", "", RAW_DOCTOR_PHONE)[-10:]

CLINIC_FAQ = {
    "fees": "💰 *Dr. Kurian's Consultation Fees:*\n• General Consultation: ₹500\n• Follow-up Visit (within 7 days): ₹300\n• Payment: UPI, Cash, Cards accepted at reception.",
    "location": "📍 *Clinic Address:*\nDr. Kurian's Medical Clinic, 2nd Floor, Apex Health Centre, Main Road.\n🗺 Landmark: Opposite Central Metro Station.",
    "hours": "🕒 *Clinic Timings:*\n• Monday – Saturday: 9:00 AM – 1:00 PM & 2:00 PM – 5:30 PM\n• Lunch Break: 1:00 PM – 2:00 PM\n• Sundays: Closed for scheduled appointments.",
    "emergency": "🚨 *MEDICAL EMERGENCY DETECTED*\nIf you or the patient are experiencing severe chest pain, shortness of breath, or critical trauma, please call 108/112 or visit the nearest hospital emergency room immediately."
}

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def is_doctor(phone: str) -> bool:
    digits = re.sub(r"\D", "", phone)
    return digits.endswith(DOCTOR_PHONE_DIGITS)

def get_doctor_daily_summary(query_date: str = None):
    target_date = query_date or str(dt_date.today())
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT id, patient_name AS name, patient_phone AS phone, appointment_time AS time, status
        FROM appointments
        WHERE appointment_time LIKE ? AND status != 'cancelled'
        ORDER BY appointment_time ASC
    """, (f"{target_date}%",))
    appointments = [dict(r) for r in c.fetchall()]

    c.execute("""
        SELECT id, patient_name, patient_phone, preferred_date, preferred_time, status
        FROM waitlist
        WHERE preferred_date LIKE ? AND status = 'waiting'
        ORDER BY id ASC
    """, (f"{target_date}%",))
    waitlist = [dict(r) for r in c.fetchall()]
    conn.close()

    return {
        "clinic": "Dr. Kurian's Medical Clinic",
        "date": target_date,
        "total_confirmed": len(appointments),
        "total_waitlisted": len(waitlist),
        "schedule": appointments,
        "waitlist": waitlist
    }

def format_schedule_message(summary: dict, label: str = "Daily Briefing") -> str:
    appts = summary["schedule"]
    waitlist = summary["waitlist"]
    lines = [
        f"📋 *Dr. Kurian's Clinic - {label}*",
        f"📅 Date: {summary['date']}",
        f"✅ Confirmed: {len(appts)}",
        f"⏳ Waitlisted: {len(waitlist)}",
        "",
        "*Schedule:*"
    ]
    if appts:
        for a in appts:
            time_part = a['time'].split(" ")[-1] if " " in a['time'] else a['time']
            lines.append(f"• ID #{a['id']}: {time_part} - {a['name']} ({a['phone']})")
    else:
        lines.append("No bookings scheduled.")
        
    if waitlist:
        lines.append("\n*Waitlist Queue:*")
        for w in waitlist:
            lines.append(f"• WL #{w['id']}: {w['patient_name']} (Pref: {w['preferred_time']})")
            
    return "\n".join(lines)

def cancel_doctor_appointment(appt_id: int):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, patient_name, appointment_time FROM appointments WHERE id = ?", (appt_id,))
    appt = c.fetchone()
    if not appt:
        conn.close()
        return None, "Appointment not found."

    c.execute("UPDATE appointments SET status = 'cancelled' WHERE id = ?", (appt_id,))
    conn.commit()

    date_prefix = appt["appointment_time"].split(" ")[0]
    next_waitlist = get_matching_waitlisted_candidate(date_prefix)
    conn.close()

    msg = f"❌ *Appointment #{appt_id} Cancelled* for {appt['patient_name']}."
    if next_waitlist:
        msg += (
            f"\n\n⚡ *Waitlist Candidate Available:*\n"
            f"• WL #{next_waitlist['id']}: {next_waitlist['patient_name']} ({next_waitlist['patient_phone']})\n"
            f"Preferred: {next_waitlist['preferred_time']}\n\n"
            f"To backfill, reply: `fill {appt_id}`"
        )
    else:
        msg += "\n\nNo waitlisted patients for this date."
    return True, msg

def handle_fill_slot(appt_id: int) -> str:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT appointment_time FROM appointments WHERE id = ?", (appt_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return f"Appointment #{appt_id} does not exist."
        
    parts = row["appointment_time"].split()
    target_date = parts[0]
    slot_time = parts[1][:5] if len(parts) > 1 else "10:00"

    candidate = get_matching_waitlisted_candidate(target_date, slot_time)
    if not candidate:
        return f"No waitlisted patients waiting for {target_date}."

    ok, msg, _ = promote_waitlist_candidate(candidate["id"], slot_time)
    return msg

def block_doctor_date(target_date: str) -> str:
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT id, patient_name, patient_phone, appointment_time 
        FROM appointments
        WHERE appointment_time LIKE ? AND status = 'confirmed'
    """, (f"{target_date}%",))
    appts = [dict(r) for r in c.fetchall()]

    c.execute("""
        UPDATE appointments SET status = 'doctor_cancelled'
        WHERE appointment_time LIKE ? AND status = 'confirmed'
    """, (f"{target_date}%",))
    conn.commit()
    conn.close()

    for a in appts:
        body = (
            f"Dear {a['patient_name']}, Dr. Kurian is unavailable on *{target_date}* due to schedule changes. "
            f"Your appointment #{a['id']} has been cancelled. Please reply with a preferred alternate date to reschedule."
        )
        send_whatsapp_message(a["patient_phone"], body)

    return f"🛑 *Date {target_date} Blocked.* Cancelled {len(appts)} bookings and alerted affected patients."

def handle_doctor_commands(msg: str) -> str:
    today_str = str(dt_date.today())
    tomorrow_str = str(dt_date.today() + timedelta(days=1))

    if msg in ["briefing", "today", "schedule"]:
        return format_schedule_message(get_doctor_daily_summary(today_str), "Today's Briefing")
    elif msg == "tomorrow":
        return format_schedule_message(get_doctor_daily_summary(tomorrow_str), "Tomorrow's Schedule")
    elif msg.startswith("cancel"):
        parts = msg.split()
        if len(parts) > 1 and parts[1].isdigit():
            _, reply = cancel_doctor_appointment(int(parts[1]))
            return reply
        return "⚠ Format: *cancel <id>* (e.g., `cancel 1`)"
    elif msg.startswith("fill"):
        parts = msg.split()
        if len(parts) > 1 and parts[1].isdigit():
            return handle_fill_slot(int(parts[1]))
        return "⚠ Format: *fill <appt_id>* (e.g., `fill 1`)"
    elif msg.startswith("promote"):
        parts = msg.split()
        if len(parts) > 1 and parts[1].isdigit():
            ok, reply, _ = promote_waitlist_candidate(int(parts[1]))
            return reply
        return "⚠ Format: *promote <waitlist_id>* (e.g., `promote 1`)"
    elif msg.startswith("block") or msg.startswith("leave"):
        parts = msg.split()
        target = tomorrow_str if "tomorrow" in msg else (parts[1] if len(parts) > 1 else today_str)
        return block_doctor_date(target)

    return (
        "👨‍⚕ *Dr. Kurian Command Menu:*\n\n"
        "• `today` / `briefing` - View schedule & waitlist\n"
        "• `tomorrow` - View tomorrow's schedule\n"
        "• `cancel <id>` - Cancel an appointment\n"
        "• `block <date>` / `leave tomorrow` - Block day and alert patients\n"
        "• `fill <appt_id>` - Promote waitlisted candidate\n"
        "• `promote <wl_id>` - Promote by WL ID"
    )


EMERGENCY_KEYWORDS = [
    "chest pain", "can't breathe", "cannot breathe", "breathless", "shortness of breath",
    "bleeding heavily", "unresponsive", "unconscious", "heart attack", "stroke", 
    "severe burn", "poison", "head injury", "convulsion", "seizure", "severe dizziness",
    "pain in chest", "nenju vedhana", "shwasam muttal", "blood varunnu", "kooduthal chora"
]

def check_emergency_triage(text: str) -> bool:
    clean = text.lower()
    return any(kw in clean for kw in EMERGENCY_KEYWORDS)

def log_staff_escalation(phone: str, name: str, reason: str):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        c = conn.cursor()
        c.execute("""
            INSERT INTO staff_escalations (patient_phone, patient_name, reason, status)
            VALUES (?, ?, ?, 'open')
        """, (phone, name, reason))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Failed to log escalation: {e}")


def parse_date_and_time(text: str):
    clean = text.lower().strip()
    
    # 1. Date extraction
    target_date = None
    today = datetime.now().date()
    
    # Match explicit ISO date YYYY-MM-DD
    iso_match = re.search(r'(202\d-\d{2}-\d{2})', clean)
    if iso_match:
        target_date = iso_match.group(1)
        clean = clean.replace(target_date, "")
    elif "tomorrow" in clean or "nale" in clean:
        target_date = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        clean = clean.replace("tomorrow", "").replace("nale", "")
    elif "today" in clean or "inun" in clean:
        target_date = today.strftime("%Y-%m-%d")
        clean = clean.replace("today", "").replace("inun", "")

    # 2. Time extraction (require colon or am/pm to avoid matching generic integers like "2 kids")
    target_time = None
    time_match = re.search(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)|(\d{1,2}):(\d{2})', clean)
    if time_match:
        matched_str = time_match.group(0)
        clean = clean.replace(matched_str, "")
        if "am" in matched_str or "pm" in matched_str:
            is_pm = "pm" in matched_str
            digits = re.findall(r'\d+', matched_str)
            hr = int(digits[0])
            minute = int(digits[1]) if len(digits) > 1 else 0
            if is_pm and hr < 12:
                hr += 12
            elif not is_pm and hr == 12:
                hr = 0
            target_time = f"{hr:02d}:{minute:02d}"
        else:
            parts = matched_str.split(":")
            target_time = f"{int(parts[0]):02d}:{int(parts[1]):02d}"

    # 3. Patient Name Extraction fallback
    name_match = re.search(r'for\s+([A-Za-z\s]+)', text, re.IGNORECASE)
    patient_name = name_match.group(1).strip() if name_match else None

    return target_date, target_time, patient_name


def handle_receptionist_ai(incoming_msg: str, sender_phone: str, profile_name: str = "Patient") -> str:
    msg_clean = incoming_msg.strip()
    clean_phone = sender_phone.replace("whatsapp:", "").replace("+", "").strip()

    # 1. IMMEDIATE EMERGENCY TRIAGE (Preempts all intent/session parsing)
    if check_emergency_triage(msg_clean):
        log_staff_escalation(clean_phone, profile_name, f"Emergency Triage Triggered: {msg_clean}")
        return (
            "🚨 *EMERGENCY MEDICAL NOTICE*:\n"
            "If you or the patient are experiencing acute symptoms like severe chest pain, breathing difficulty, or heavy bleeding, "
            "please immediately call *108 / 112* or visit the nearest Hospital Emergency Room.\n\n"
            "Our clinic staff has been alerted to your message."
        )

    # 2. CANCELLATION REQUEST
    if re.search(r'\b(cancel|cancellation)\b', msg_clean.lower()):
        success, reply = handle_cancellation(clean_phone)
        return reply

    # 3. CONVERSATION CONTEXT & ACTIVE SESSION LOOKUP
    session = get_session(clean_phone)
    pending_intent = session.get("pending_intent") if session else None

    # Handle reminder replies (1 = Confirm, 2 = Cancel)
    if pending_intent == "awaiting_reminder_ack":
        if msg_clean == "1":
            update_session(clean_phone, pending_intent=None)
            return "✅ Thank you! Your appointment has been confirmed. See you at the clinic."
        elif msg_clean == "2":
            update_session(clean_phone, pending_intent=None)
            success, reply = handle_cancellation(clean_phone)
            return f"❌ {reply}"

    # Handle feedback ONLY if patient is explicitly in a feedback flow
    if pending_intent == "awaiting_feedback" and msg_clean in ["1", "2", "3", "4", "5"]:
        rating = int(msg_clean)
        update_session(clean_phone, pending_intent=None)
        if rating >= 4:
            return "⭐ Thank you for your feedback! We are glad you had a smooth visit."
        else:
            log_staff_escalation(clean_phone, profile_name, f"Low rating received ({rating}/5): Needs follow-up")
            return "Thank you for sharing your feedback. Our clinic team has noted this and will review it."

    # 4. INTENT: LIST AVAILABLE SLOTS
    if re.search(r'\b(slot|slots|available|timings?|time)\b', msg_clean.lower()) and not re.search(r'\b(book|reserve)\b', msg_clean.lower()):
        target_date, _, _ = parse_date_and_time(msg_clean)
        if not target_date:
            target_date = (datetime.now().date() + timedelta(days=1)).strftime("%Y-%m-%d")
        
        update_session(clean_phone, last_date=target_date, pending_intent="awaiting_time_selection")
        return f"📅 Available slots for {target_date}:\n• 10:00 AM\n• 10:30 AM\n• 11:30 AM\n• 04:30 PM\n\nReply with your preferred time to book (e.g., '10:30 AM for {profile_name}')."

    # 5. INTENT: BOOK APPOINTMENT
    target_date, target_time, patient_name = parse_date_and_time(msg_clean)
    
    # Use session memory if date was discussed earlier
    if not target_date and session and session.get("last_date"):
        target_date = session.get("last_date")

    final_name = patient_name or profile_name or "Patient"

    if target_time:
        if not target_date:
            update_session(clean_phone, pending_intent="awaiting_date")
            return f"Got it, {target_time}. Would you like to book this slot for *Today* or *Tomorrow*?"
        
        # Verify slot availability and persist
        appointment_datetime = f"{target_date} {target_time}:00"
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        c = conn.cursor()
        
        # Check existing confirmed booking
        c.execute("SELECT id FROM appointments WHERE appointment_time = ? AND status = 'confirmed'", (appointment_datetime,))
        exists = c.fetchone()
        
        if exists:
            # Add to waitlist
            c.execute("""
                INSERT INTO waitlist (patient_name, patient_phone, preferred_date, preferred_time, status)
                VALUES (?, ?, ?, ?, 'waiting')
            """, (final_name, clean_phone, target_date, target_time))
            conn.commit()
            conn.close()
            update_session(clean_phone, pending_intent=None)
            return (
                f"⚠️ The {target_time} slot on {target_date} is already reserved.\n"
                f"You have been placed on the *priority waitlist*. If a cancellation occurs, you will be notified immediately."
            )

        # Slot available -> Confirm booking
        try:
            c.execute("""
                INSERT INTO appointments (patient_name, patient_phone, appointment_time, status, doctor_id)
                VALUES (?, ?, ?, 'confirmed', 1)
            """, (final_name, clean_phone, appointment_datetime))
            conn.commit()
            update_session(clean_phone, pending_intent=None)
            return (
                f"✅ *Appointment Confirmed!*\n\n"
                f"• Patient: {final_name}\n"
                f"• Date: {target_date}\n"
                f"• Time: {target_time}\n"
                f"• Clinic: Dr. Kurian's Medical Clinic\n\n"
                f"To cancel or reschedule, reply 'Cancel'."
            )
        except Exception as e:
            conn.rollback()
            return "An unexpected error occurred while booking. Please try again."
        finally:
            conn.close()

    # 6. DEFAULT FALLBACK
    return (
        f"Hello {profile_name}! Welcome to Dr. Kurian's Medical Clinic.\n\n"
        f"• To check open slots: 'Available slots tomorrow'\n"
        f"• To book: 'Book 10:30 am tomorrow for {profile_name}'\n"
        f"• To cancel: 'Cancel my appointment'"
    )
