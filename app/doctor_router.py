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
    today = datetime.now().date()
    target_date = None

    # Date extraction
    iso_match = re.search(r'\b(202\d-\d{2}-\d{2})\b', clean)
    if iso_match:
        target_date = iso_match.group(1)
    elif "tomorrow" in clean or "nale" in clean:
        target_date = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    elif "today" in clean or "inun" in clean:
        target_date = today.strftime("%Y-%m-%d")

    # Time extraction: matches 10:30 am, 10:30am, 10 am, 10:30, 16:00
    target_time = None
    time_match = re.search(r'\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b', clean)
    if time_match:
        raw_hr = int(time_match.group(1))
        raw_min = int(time_match.group(2)) if time_match.group(2) else 0
        meridiem = time_match.group(3)

        # Only treat as time if meridiem is present OR colon is used OR hour is in business range (9-20)
        if meridiem or time_match.group(2) or (9 <= raw_hr <= 20):
            if meridiem:
                if meridiem == "pm" and raw_hr < 12:
                    raw_hr += 12
                elif meridiem == "am" and raw_hr == 12:
                    raw_hr = 0
            if 0 <= raw_hr <= 23 and 0 <= raw_min <= 59:
                target_time = f"{raw_hr:02d}:{raw_min:02d}"

    # Name extraction: handles "for Sehil MC", "for Sehil", etc.
    patient_name = None
    name_match = re.search(r'\bfor\s+([A-Za-z0-9\s\._-]+)', text, re.IGNORECASE)
    if name_match:
        raw_n = name_match.group(1).strip()
        patient_name = re.sub(r'\b(tomorrow|today|at|am|pm)\b', '', raw_n, flags=re.IGNORECASE).strip()

    return target_date, target_time, patient_name


def handle_receptionist_ai(incoming_msg: str, sender_phone: str, profile_name: str = "Patient") -> str:
    msg_clean = incoming_msg.strip()
    msg_lower = msg_clean.lower()
    clean_phone = sender_phone.replace("whatsapp:", "").replace("+", "").strip()

    # 1. IMMEDIATE EMERGENCY TRIAGE
    if check_emergency_triage(msg_clean):
        log_staff_escalation(clean_phone, profile_name, f"Emergency Triage Triggered: {msg_clean}")
        return (
            "🚨 *EMERGENCY MEDICAL NOTICE*:\n"
            "If you or the patient are experiencing acute symptoms like severe chest pain, breathing difficulty, or heavy bleeding, "
            "please immediately call *108 / 112* or visit the nearest Hospital Emergency Room.\n\n"
            "Our clinic staff has been alerted to your message."
        )

    # 2. MEDICAL ADVICE & PRESCRIPTION GUARDRAIL
    if re.search(r'\b(prescribe|prescription|medicine|antibiotic|antibiotics|dosage|diagnose|fever|cough)\b', msg_lower) and not re.search(r'\b(book|slot|cancel)\b', msg_lower):
        return (
            "⚠️ *Medical Notice*:\n"
            "As an AI clinic assistant, I cannot diagnose illnesses or prescribe medications autonomously.\n\n"
            "Please book an in-person consultation with Dr. Kurian so you can be properly evaluated. "
            "Reply *'Available slots tomorrow'* to schedule an appointment."
        )

    # 3. CLINIC INFORMATION: LOCATION, TIMINGS, FEES
    if re.search(r'\b(where|location|address|place|find you)\b', msg_lower) or \
       (re.search(r'\b(timings?|hours|open|closed|closing)\b', msg_lower) and not re.search(r'\b(slot|book|reserve)\b', msg_lower)):
        return (
            "🏥 *Dr. Kurian's Medical Clinic*\n\n"
            "📍 *Location*: MG Road, Central Junction, Kochi, Kerala\n"
            "🕒 *Working Hours*: Monday to Saturday, 9:00 AM – 6:00 PM (Closed on Sundays)\n"
            "💰 *Consultation Fee*: ₹500\n\n"
            "Would you like to book an appointment? Reply *'Available slots tomorrow'* to view open times."
        )

    if re.search(r'\b(fee|fees|cost|charge|charges|price|payment)\b', msg_lower) and not re.search(r'\b(book|reserve)\b', msg_lower):
        return (
            "💰 *Consultation Fee*: ₹500\n\n"
            "We accept UPI (Google Pay, PhonePe, Paytm), Cards, and Cash at the clinic reception counter."
        )

    # 4. VIEW ACTIVE BOOKINGS
    if re.search(r'\b(my appointment|my appointments|what appointment|check booking|active booking|my slot)\b', msg_lower):
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("""
            SELECT id, patient_name, appointment_time, status 
            FROM appointments 
            WHERE patient_phone = ? AND status = 'confirmed'
            ORDER BY appointment_time ASC
        """, (clean_phone,))
        rows = c.fetchall()
        conn.close()

        if not rows:
            return "You do not have any active confirmed appointments under this phone number. Reply *'Available slots tomorrow'* to book one!"
        
        reply = "📋 *Your Confirmed Appointments*:\n"
        for r in rows:
            reply += f"• *{r['patient_name']}*: {r['appointment_time']}\n"
        reply += "\nReply *'Cancel my appointment'* if you need to cancel or reschedule."
        return reply

    # 5. CANCELLATION REQUEST
    if re.search(r'\b(cancel|cancellation)\b', msg_lower):
        success, reply = handle_cancellation(clean_phone)
        return reply

    # 6. CONVERSATION CONTEXT & ACTIVE SESSION LOOKUP
    session = get_session(clean_phone)
    pending_intent = session.get("pending_intent") if session else None

    if pending_intent == "awaiting_reminder_ack":
        if msg_clean == "1":
            update_session(clean_phone, pending_intent=None)
            return "✅ Thank you! Your appointment has been confirmed. See you at the clinic."
        elif msg_clean == "2":
            update_session(clean_phone, pending_intent=None)
            success, reply = handle_cancellation(clean_phone)
            return f"❌ {reply}"

    # 7. INTENT: LIST AVAILABLE SLOTS
    if re.search(r'\b(slot|slots|available|free slots)\b', msg_lower) and not re.search(r'\b(book|reserve)\b', msg_lower):
        target_date, _, _ = parse_date_and_time(msg_clean)
        if not target_date:
            target_date = (datetime.now().date() + timedelta(days=1)).strftime("%Y-%m-%d")

        # Verify clinic is not closed on Sunday
        try:
            day_obj = datetime.strptime(target_date, "%Y-%m-%d")
            if day_obj.weekday() == 6: # Sunday
                next_mon = (day_obj + timedelta(days=1)).strftime("%Y-%m-%d")
                return f"📅 The clinic is closed on Sundays ({target_date}).\nOur next available working day is Monday ({next_mon}). Reply *'Available slots Monday'* to check slots."
        except Exception:
            pass
        
        update_session(clean_phone, last_date=target_date, pending_intent="awaiting_time_selection")
        return f"📅 Available slots for {target_date}:\n• 10:00 AM\n• 10:30 AM\n• 11:30 AM\n• 04:30 PM\n\nReply with your preferred time to book (e.g., 'Book 10:30 AM tomorrow for {profile_name}')."

    # 8. INTENT: BOOK APPOINTMENT
    if re.search(r'\b(book|reserve|schedule|appointment)\b', msg_lower) or pending_intent == "awaiting_time_selection":
        target_date, target_time, patient_name = parse_date_and_time(msg_clean)
        
        if not target_date and session and session.get("last_date"):
            target_date = session.get("last_date")
        if not target_date:
            target_date = (datetime.now().date() + timedelta(days=1)).strftime("%Y-%m-%d")

        final_name = patient_name or (profile_name if profile_name != "Patient" else "Patient")

        if target_time:
            # Check Sunday closure
            try:
                day_obj = datetime.strptime(target_date, "%Y-%m-%d")
                if day_obj.weekday() == 6:
                    return f"❌ The clinic is closed on Sundays ({target_date}). Please choose Monday through Saturday."
            except Exception:
                pass

            appointment_datetime = f"{target_date} {target_time}:00"
            conn = sqlite3.connect(DB_PATH, timeout=5.0)
            c = conn.cursor()
            
            c.execute("SELECT id FROM appointments WHERE appointment_time = ? AND status = 'confirmed'", (appointment_datetime,))
            exists = c.fetchone()
            
            if exists:
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
                    f"• Clinic: Dr. Kurian's Medical Clinic\n"
                    f"• Consultation Fee: ₹500\n\n"
                    f"💳 *Payment Options:*\n"
                    f"1. Tap to pay via UPI (GPay/PhonePe/Paytm):\nupi://pay?pa=drkurian@upi&pn=Dr%20Kurians%20Clinic&am=500&cu=INR&tn=Consultation%20Fee\n"
                    f"2. Pay at clinic counter upon arrival (Cash or UPI)\n\n"
                    f"To check your booking, text 'What appointments do I have booked'. To cancel, reply 'Cancel'."
                )
            except Exception as e:
                conn.rollback()
                return "An unexpected error occurred while booking. Please try again."
            finally:
                conn.close()

    # 9. OUT OF SCOPE / IRRELEVANT
    if re.search(r'\b(car|engine|crypto|bitcoin|flight|hotel|plumber|mechanic|movie)\b', msg_lower):
        return (
            "I am the AI receptionist for Dr. Kurian's Medical Clinic. "
            "I can assist you with appointment bookings, slot availability, clinic timings, and clinic information. "
            "How may I assist you with your health visit today?"
        )

    # 10. DEFAULT FALLBACK
    return (
        f"Hello {profile_name}! Welcome to Dr. Kurian's Medical Clinic.\n\n"
        f"• To check open slots: 'Available slots tomorrow'\n"
        f"• To book: 'Book 10:30 am tomorrow for {profile_name}'\n"
        f"• To view bookings: 'What appointments do I have booked'\n"
        f"• To cancel: 'Cancel my appointment'"
    )

def trigger_doctor_briefing():
    """
    Summarizes today's confirmed appointments and waitlist,
    returning a briefing string for the doctor.
    """
    today_str = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    try:
        c.execute("""
            SELECT patient_name, appointment_time, status 
            FROM appointments 
            WHERE appointment_time LIKE ? AND status = 'confirmed'
            ORDER BY appointment_time ASC
        """, (f"{today_str}%",))
        appts = c.fetchall()

        c.execute("""
            SELECT COUNT(*) as count FROM waitlist 
            WHERE preferred_date = ? AND status = 'waiting'
        """, (today_str,))
        waiter_count = c.fetchone()["count"]

        briefing = f"📋 Good morning Doctor! Daily Briefing ({today_str}):\n"
        briefing += f"• Total Confirmed Appointments: {len(appts)}\n"
        briefing += f"• Patients on Waitlist: {waiter_count}\n\n"

        if appts:
            briefing += "Schedule:\n"
            for row in appts:
                time_part = str(row["appointment_time"]).split(" ")[1][:5]
                briefing += f" - {time_part}: {row['patient_name']}\n"
        else:
            briefing += "No appointments scheduled yet for today.\n"

        print(briefing)
        return briefing
    except Exception as e:
        print(f"Error generating briefing: {e}")
        return f"Error generating daily briefing: {e}"
    finally:
        conn.close()




@doctor_router.post("/whatsapp-webhook")
async def whatsapp_webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    sender_phone = str(data.get("phone") or data.get("from") or "").strip()
    sender_name = str(data.get("name") or data.get("sender_name") or "Patient").strip()
    message_body = str(data.get("message") or data.get("text") or data.get("body") or "").strip()

    if not sender_phone or not message_body:
        return {"status": "ignored", "detail": "Missing phone or message text"}

    try:
        reply_text = handle_receptionist_ai(
            incoming_msg=message_body,
            sender_phone=sender_phone,
            profile_name=sender_name
        )
        return {"status": "success", "reply": reply_text}
    except Exception as e:
        print(f"Error executing receptionist AI: {e}")
        return {
            "status": "error",
            "reply": "Thank you for contacting Dr. Kurian's clinic. An assistant will get back to you shortly."
        }
