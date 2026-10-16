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


STANDARD_CLINIC_SLOTS = [
    ("10:00", "10:00 AM"),
    ("10:30", "10:30 AM"),
    ("11:30", "11:30 AM"),
    ("14:30", "02:30 PM"),
    ("16:30", "04:30 PM"),
]

def get_open_slots_for_date(target_date: str) -> list[tuple[str, str]]:
    """Returns list of (24h_str, 12h_label) slots that are not yet confirmed on target_date."""
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    c = conn.cursor()
    c.execute(
        "SELECT appointment_time FROM appointments WHERE appointment_time LIKE ? AND status = 'confirmed'",
        (f"{target_date}%",)
    )
    booked_times = {row[0].split(" ")[1][:5] for row in c.fetchall() if " " in row[0]}
    conn.close()
    return [(s24, s12) for s24, s12 in STANDARD_CLINIC_SLOTS if s24 not in booked_times]


def parse_date_and_time(text: str):
    clean = text.lower().strip()
    today = datetime.now().date()
    target_date = None

    # 1. Date extraction (including fuzzy 'day after tomorrow' & weekdays)
    iso_match = re.search(r'\b(202\d-\d{2}-\d{2})\b', clean)
    if iso_match:
        target_date = iso_match.group(1)
    elif "day after tomorrow" in clean or "matannal" in clean:
        target_date = (today + timedelta(days=2)).strftime("%Y-%m-%d")
        clean = clean.replace("day after tomorrow", "").replace("matannal", "")
    elif "tomorrow" in clean or "nale" in clean:
        target_date = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        clean = clean.replace("tomorrow", "").replace("nale", "")
    elif "today" in clean or "inun" in clean:
        target_date = today.strftime("%Y-%m-%d")
        clean = clean.replace("today", "").replace("inun", "")
    else:
        weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        for idx, day_name in enumerate(weekdays):
            if re.search(rf'\b{day_name}\b', clean):
                days_ahead = (idx - today.weekday() + 7) % 7
                if days_ahead == 0:
                    days_ahead = 7
                target_date = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
                clean = re.sub(rf'\b{day_name}\b', '', clean)
                break

    # 2. Time extraction (explicit + fuzzy morning/afternoon/evening)
    target_time = None
    time_match = re.search(r'\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b', clean)
    if time_match:
        raw_hr = int(time_match.group(1))
        raw_min = int(time_match.group(2)) if time_match.group(2) else 0
        meridiem = time_match.group(3)

        # Avoid treating single-digit menu selections (1-5) as hours unless am/pm or colon is given
        if meridiem or time_match.group(2) or (9 <= raw_hr <= 20):
            if meridiem:
                if meridiem == "pm" and raw_hr < 12:
                    raw_hr += 12
                elif meridiem == "am" and raw_hr == 12:
                    raw_hr = 0
            elif 1 <= raw_hr <= 6:
                # e.g. "around 4" -> 16:00 (4 PM)
                raw_hr += 12
            if 0 <= raw_hr <= 23 and 0 <= raw_min <= 59:
                target_time = f"{raw_hr:02d}:{raw_min:02d}"

    if not target_time:
        if re.search(r'\b(morning|forenoon|ravile)\b', clean):
            target_time = "10:00"
        elif re.search(r'\b(afternoon|uchakku)\b', clean):
            target_time = "14:30"
        elif re.search(r'\b(evening|vaikitt)\b', clean):
            target_time = "16:30"

    # 3. Patient Name extraction
    patient_name = None
    name_match = re.search(r'\bfor\s+([A-Za-z0-9\s\._-]+)', text, re.IGNORECASE)
    if name_match:
        raw_n = name_match.group(1).strip()
        patient_name = re.sub(
            r'\b(day after tomorrow|tomorrow|today|at|am|pm|morning|afternoon|evening)\b',
            '', raw_n, flags=re.IGNORECASE
        ).strip()

    return target_date, target_time, patient_name


def book_slot_in_db(clean_phone: str, final_name: str, target_date: str, target_time: str) -> str:
    """Helper to verify Sunday rules, check conflicts, book or waitlist, and return WhatsApp message."""
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
        clear_session(clean_phone)
        return (
            f"⚠️ The *{target_time}* slot on *{target_date}* is already reserved.\n"
            f"You have been placed on the *priority waitlist*. If a cancellation occurs, you will be notified immediately."
        )

    try:
        c.execute("""
            INSERT INTO appointments (patient_name, patient_phone, appointment_time, status, doctor_id)
            VALUES (?, ?, ?, 'confirmed', 1)
        """, (final_name, clean_phone, appointment_datetime))
        conn.commit()
        clear_session(clean_phone)
        return (
            f"✅ *Appointment Confirmed!*\n\n"
            f"• *Patient*: {final_name}\n"
            f"• *Date*: {target_date}\n"
            f"• *Time*: {target_time}\n"
            f"• *Clinic*: Dr. Kurian's Medical Clinic\n"
            f"• *Consultation Fee*: ₹500\n\n"
            f"📍 *Location*: https://maps.google.com/?q=9.9816,76.2999\n\n"
            f"💳 *Payment Options:*\n"
            f"1. Pay via UPI: upi://pay?pa=drkurian@upi&pn=Dr%20Kurians%20Clinic&am=500&cu=INR&tn=Consultation%20Fee\n"
            f"2. Pay at clinic counter upon arrival (Cash/UPI)\n\n"
            f"To view bookings, reply *'My appointments'*. To cancel, reply *'Cancel'*."
        )
    except Exception:
        conn.rollback()
        return "An unexpected error occurred while booking. Please try again."
    finally:
        conn.close()


def handle_receptionist_ai(incoming_msg: str, sender_phone: str, profile_name: str = "Patient") -> str:
    msg_clean = incoming_msg.strip()
    msg_lower = msg_clean.lower()
    clean_phone = sender_phone.replace("whatsapp:", "").replace("+", "").strip()

    # 0. DOCTOR ADMIN COMMANDS (If message comes from Dr. Kurian's phone)
    if is_doctor(clean_phone) and msg_lower.split()[0] in ["briefing", "today", "schedule", "tomorrow", "cancel", "fill", "promote", "block", "leave"]:
        return handle_doctor_commands(msg_lower)

    # 0.5. REMINDER CONFIRMATION HOOK (If patient replies 1 to a 24h reminder)
    if msg_lower == "1":
        conn = get_db()
        c = conn.cursor()
        c.execute("""
            SELECT id, appointment_time FROM appointments 
            WHERE patient_phone = ? AND status = 'confirmed' 
            ORDER BY appointment_time ASC LIMIT 1
        """, (clean_phone,))
        upcoming = c.fetchone()
        conn.close()
        if upcoming:
            return (
                f"✅ *Attendance Confirmed!* Thank you {profile_name}. "
                f"Your appointment (#{upcoming['id']} on {upcoming['appointment_time']}) is confirmed with Dr. Kurian. "
                f"We look forward to seeing you at MG Road, Kochi!"
            )

    # 1. IMMEDIATE EMERGENCY TRIAGE (Always preempts even if bot is muted)
    if check_emergency_triage(msg_clean):
        log_staff_escalation(clean_phone, profile_name, f"Emergency Triage Triggered: {msg_clean}")
        return (
            "🚨 *EMERGENCY MEDICAL NOTICE*:\n"
            "If you or the patient are experiencing acute symptoms like severe chest pain, breathing difficulty, or heavy bleeding, "
            "please immediately call *108 / 112* or visit the nearest Hospital Emergency Room.\n\n"
            "📍 *Emergency Map*: https://maps.google.com/?q=Hospital+Emergency+Near+Me\n"
            "Our clinic staff has been alerted to your message."
        )

    # 2. STAFF TAKEOVER / BOT MUTE CHECK
    if msg_lower in ["resume bot", "start bot", "unmute", "bot"]:
        resolve_escalation(clean_phone)
        return f"🤖 Automated assistant reactivated for {profile_name}! How can I help you book or manage your appointment today?"

    if is_patient_escalated(clean_phone):
        # Bot stays completely silent so clinic staff can chat manually with the patient
        return ""

    if re.search(r'\b(speak to human|talk to human|human|staff|receptionist|real person|call me|agent)\b', msg_lower):
        return trigger_escalation(clean_phone, profile_name, f"Patient requested human takeover: {msg_clean}")

    # 3. MEDICAL ADVICE & PRESCRIPTION GUARDRAIL
    if re.search(r'\b(prescribe|prescription|medicine|antibiotic|antibiotics|dosage|diagnose|fever|cough|tablet|painkiller)\b', msg_lower) and not re.search(r'\b(book|slot|cancel)\b', msg_lower):
        return (
            "⚠️ *Medical Notice*:\n"
            "As an AI clinic assistant, I cannot diagnose illnesses or prescribe medications autonomously.\n\n"
            "Please book an in-person consultation with Dr. Kurian so you can be properly evaluated. "
            "Reply *'Available slots tomorrow'* to schedule an appointment."
        )

    # 4. CLINIC INFORMATION: LOCATION (WITH GOOGLE MAPS PIN), TIMINGS, FEES
    if re.search(r'\b(where|location|address|place|find you|map|direction|directions)\b', msg_lower) or \
       (re.search(r'\b(timings?|hours|open|closed|closing)\b', msg_lower) and not re.search(r'\b(slot|book|reserve)\b', msg_lower)):
        return (
            "🏥 *Dr. Kurian's Medical Clinic*\n\n"
            "📍 *Address*: 2nd Floor, Apex Health Centre, MG Road, Central Junction, Kochi\n"
            "🗺️ *Google Maps Pin*: https://maps.google.com/?q=9.9816,76.2999\n"
            "🕒 *Working Hours*: Mon – Sat, 9:00 AM – 6:00 PM (Closed Sundays)\n"
            "💰 *Consultation Fee*: ₹500\n\n"
            "Reply *'Available slots tomorrow'* to view open times and book!"
        )

    if re.search(r'\b(fee|fees|cost|charge|charges|price|payment)\b', msg_lower) and not re.search(r'\b(book|reserve)\b', msg_lower):
        return (
            "💰 *Consultation Fee*: ₹500 (Follow-up within 7 days: ₹300)\n\n"
            "We accept UPI (GPay, PhonePe, Paytm), Cards, and Cash at the clinic counter."
        )

    # 5. VIEW ACTIVE BOOKINGS
    if re.search(r'\b(my appointment|my appointments|what appointment|check booking|active booking|my slot|booked)\b', msg_lower) and not re.search(r'\b(book|cancel)\b', msg_lower):
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
            reply += f"• *{r['patient_name']}*: {r['appointment_time']} (ID #{r['id']})\n"
        reply += "\nReply *'Cancel my appointment'* if you need to cancel or reschedule."
        return reply

    # 6. CANCELLATION REQUEST
    if re.search(r'\b(cancel|cancellation)\b', msg_lower):
        success, reply = handle_cancellation(clean_phone)
        clear_session(clean_phone)
        return reply

    # 7. CONVERSATION CONTEXT & NUMBER-KEYED MENU HANDLING
    session = get_session(clean_phone)
    pending_intent = session.get("pending_intent") if session else None

    if pending_intent == "awaiting_reminder_ack":
        if msg_clean == "1":
            clear_session(clean_phone)
            return "✅ Thank you! Your appointment has been confirmed. See you at the clinic."
        elif msg_clean == "2":
            clear_session(clean_phone)
            success, reply = handle_cancellation(clean_phone)
            return f"❌ {reply}"

    # Number-Keyed Slot Selection (e.g., user replies "1", "2", "3" or "2 for Rohith")
    num_choice_match = re.match(r'^([1-9])(?:\s+for\s+([A-Za-z0-9\s\._-]+))?$', msg_clean, re.IGNORECASE)
    if pending_intent == "awaiting_time_selection" and num_choice_match:
        choice_idx = int(num_choice_match.group(1)) - 1
        custom_name = num_choice_match.group(2).strip() if num_choice_match.group(2) else None
        target_date = session.get("last_date") or (datetime.now().date() + timedelta(days=1)).strftime("%Y-%m-%d")
        open_slots = get_open_slots_for_date(target_date)

        if 0 <= choice_idx < len(open_slots):
            chosen_24h, _ = open_slots[choice_idx]
            final_name = custom_name or (profile_name if profile_name != "Patient" else "Patient")
            return book_slot_in_db(clean_phone, final_name, target_date, chosen_24h)
        else:
            return f"⚠️ Invalid option number. Please reply with a number between *1* and *{len(open_slots)}*."

    # 8. INTENT: LIST AVAILABLE SLOTS (Number-Keyed Menu)
    if re.search(r'\b(slot|slots|available|free slots)\b', msg_lower) and not re.search(r'\b(book|reserve)\b', msg_lower):
        target_date, _, _ = parse_date_and_time(msg_clean)
        if not target_date:
            target_date = (datetime.now().date() + timedelta(days=1)).strftime("%Y-%m-%d")

        try:
            day_obj = datetime.strptime(target_date, "%Y-%m-%d")
            if day_obj.weekday() == 6:
                next_mon = (day_obj + timedelta(days=1)).strftime("%Y-%m-%d")
                return f"📅 The clinic is closed on Sundays ({target_date}).\nOur next working day is Monday ({next_mon}). Reply *'Available slots Monday'* to check slots."
        except Exception:
            pass

        open_slots = get_open_slots_for_date(target_date)
        if not open_slots:
            return f"😔 All regular slots for *{target_date}* are currently full. Reply with a preferred time (e.g., *'Book 10:30 AM {target_date}'*) to join the priority waitlist!"

        number_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"]
        menu_lines = [f"📅 *Available slots for {target_date}*:\n"]
        for i, (_, label12) in enumerate(open_slots):
            badge = number_emojis[i] if i < len(number_emojis) else f"{i+1}."
            menu_lines.append(f"{badge} {label12}")

        menu_lines.append(
            f"\n👉 *Reply with the slot number* (e.g., *1* or *2*) to instant-book for *{profile_name}*, "
            f"or reply *'2 for [Patient Name]'*!"
        )
        update_session(clean_phone, last_date=target_date, pending_intent="awaiting_time_selection")
        return "\n".join(menu_lines)

    # 9. INTENT: BOOK APPOINTMENT (Direct or Fuzzy Time)
    target_date, target_time, patient_name = parse_date_and_time(msg_clean)
    if re.search(r'\b(book|reserve|schedule|appointment)\b', msg_lower) or pending_intent == "awaiting_time_selection" or (target_date and target_time):
        if not target_date and session and session.get("last_date"):
            target_date = session.get("last_date")
        if not target_date:
            target_date = (datetime.now().date() + timedelta(days=1)).strftime("%Y-%m-%d")

        final_name = patient_name or (profile_name if profile_name != "Patient" else "Patient")

        if target_time:
            return book_slot_in_db(clean_phone, final_name, target_date, target_time)
        else:
            # Graceful typo recovery when user is trying to book or pick a time
            open_slots = get_open_slots_for_date(target_date)
            sample_times = " or ".join([s[1] for s in open_slots[:2]]) if open_slots else "10:00 AM or 10:30 AM"
            update_session(clean_phone, last_date=target_date, pending_intent="awaiting_time_selection")
            return (
                f"🤔 I couldn't quite catch the time for *{target_date}*.\n\n"
                f"Did you mean *{sample_times}*?\n"
                f"Reply with a slot number (e.g., *1* or *2*), a time like *'10:30 AM'*, or *'morning'* / *'afternoon'*."
            )

    # 10. OUT OF SCOPE / IRRELEVANT
    if re.search(r'\b(car|engine|crypto|bitcoin|flight|hotel|plumber|mechanic|movie)\b', msg_lower):
        return (
            "I am the AI receptionist for Dr. Kurian's Medical Clinic. "
            "I can assist you with appointment bookings, slot availability, clinic timings, and clinic location. "
            "How may I assist you with your clinic visit today?"
        )

    # 11. DEFAULT FALLBACK
    return (
        f"Hello {profile_name}! 👋 Welcome to *Dr. Kurian's Medical Clinic*.\n\n"
        f"How can I help you today?\n"
        f"• 📅 *Check open slots*: 'Available slots tomorrow'\n"
        f"• ⚡ *Quick book*: 'Book morning tomorrow' or 'Book 10:30 am'\n"
        f"• 📋 *My bookings*: 'What appointments do I have booked'\n"
        f"• 📍 *Location & Map*: 'Where is the clinic located'\n"
        f"• 👤 *Staff Help*: 'Speak to receptionist'"
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
