from app.ai_agent import answer_clinic_query
"""WhatsApp receptionist brain + doctor command centre.

Inbound messages arrive from the Baileys bridge as JSON and are dispatched here.
Doctor-command routing is per doctor: any phone number that matches an active
``doctors`` row can run schedule/leave commands for their own calendar.
"""
import os
import re
import sqlite3
from datetime import datetime, timedelta, date as dt_date

from fastapi import APIRouter, Request, Header, Depends, HTTPException

from app import config
from app.database import get_sqlite_connection
from app.receptionist import (
    get_available_slots,
    get_active_doctors,
    get_doctor,
    cancel_patient_appointment,
    get_matching_waitlisted_candidate,
    promote_waitlist_candidate,
)
from app.session_manager import get_session, update_session, clear_session
from app.escalation_manager import (
    is_patient_escalated,
    trigger_escalation,
    trigger_emergency_escalation,
    resolve_escalation,
)
from app.notifier import send_whatsapp_message
from app.followup_engine import (
    dispatch_post_consultation_followups,
    handle_feedback_response,
)
from app.auth import verify_admin

doctor_router = APIRouter(prefix="/api/doctor", tags=["Doctor & Patient Portal"])

RAW_DOCTOR_PHONE = os.getenv("DOCTOR_PHONE", config.CLINIC_PHONE)
DOCTOR_PHONE_DIGITS = re.sub(r"\D", "", RAW_DOCTOR_PHONE)[-10:]

STANDARD_CLINIC_SLOTS = [
    ("10:00", "10:00 AM"),
    ("10:30", "10:30 AM"),
    ("11:30", "11:30 AM"),
    ("14:30", "02:30 PM"),
    ("16:30", "04:30 PM"),
]

EMERGENCY_KEYWORDS = [
    "chest pain", "can't breathe", "cannot breathe", "breathless", "shortness of breath",
    "bleeding heavily", "unresponsive", "unconscious", "heart attack", "stroke",
    "severe burn", "poison", "head injury", "convulsion", "seizure", "severe dizziness",
    "pain in chest", "nenju vedhana", "shwasam muttal", "blood varunnu", "kooduthal chora",
]


def get_db():
    return get_sqlite_connection()


# --------------------------------------------------------------------------
# Doctor identity
# --------------------------------------------------------------------------
def _normalise_phone(phone: str) -> str:
    return re.sub(r"\D", "", str(phone))[-10:]


def get_doctor_by_phone(phone: str) -> dict | None:
    """Return the active doctor whose phone matches, if any."""
    tail = _normalise_phone(phone)
    if not tail:
        return None
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, name, specialty, consultation_fee, phone FROM doctors WHERE active = 1")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    for row in rows:
        if row.get("phone") and _normalise_phone(row["phone"]) == tail:
            return row
    # Fall back to the configured doctor phone for clinics not yet in the DB.
    if tail == DOCTOR_PHONE_DIGITS:
        return {"id": 1, "name": config.DOCTOR_NAME, "specialty": None, "consultation_fee": int(config.CONSULTATION_FEE)}
    return None


def is_doctor(phone: str) -> bool:
    return get_doctor_by_phone(phone) is not None


def resolve_default_doctor_id() -> int:
    doctors = get_active_doctors()
    return doctors[0]["id"] if doctors else 1


# --------------------------------------------------------------------------
# Doctor-facing schedule helpers
# --------------------------------------------------------------------------
def get_doctor_daily_summary(query_date: str = None, doctor_id: int = None):
    target_date = query_date or str(dt_date.today())
    conn = get_db()
    c = conn.cursor()

    if doctor_id is None:
        c.execute(
            """
            SELECT id, patient_name AS name, patient_phone AS phone,
                   appointment_time AS time, status
            FROM appointments
            WHERE appointment_time LIKE ? AND status != 'cancelled'
            ORDER BY appointment_time ASC
            """,
            (f"{target_date}%",),
        )
    else:
        c.execute(
            """
            SELECT id, patient_name AS name, patient_phone AS phone,
                   appointment_time AS time, status
            FROM appointments
            WHERE appointment_time LIKE ? AND status != 'cancelled' AND doctor_id = ?
            ORDER BY appointment_time ASC
            """,
            (f"{target_date}%", doctor_id),
        )
    appointments = [dict(r) for r in c.fetchall()]

    c.execute(
        """
        SELECT id, patient_name, patient_phone, preferred_date, preferred_time, status
        FROM waitlist
        WHERE preferred_date LIKE ? AND status = 'waiting'
        ORDER BY id ASC
        """,
        (f"{target_date}%",),
    )
    waitlist = [dict(r) for r in c.fetchall()]
    conn.close()

    return {
        "clinic": config.CLINIC_NAME,
        "date": target_date,
        "total_confirmed": len(appointments),
        "total_waitlisted": len(waitlist),
        "schedule": appointments,
        "waitlist": waitlist,
    }


def format_schedule_message(summary: dict, label: str = "Daily Briefing") -> str:
    appts = summary["schedule"]
    waitlist = summary["waitlist"]
    lines = [
        f"📋 *{config.CLINIC_NAME} - {label}*",
        f"📅 Date: {summary['date']}",
        f"✅ Confirmed: {len(appts)}",
        f"⏳ Waitlisted: {len(waitlist)}",
        "",
        "*Schedule:*",
    ]
    if appts:
        for a in appts:
            time_part = a["time"].split(" ")[-1] if " " in a["time"] else a["time"]
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
    c.execute(
        "SELECT id, patient_name, appointment_time, doctor_id FROM appointments WHERE id = ?",
        (appt_id,),
    )
    appt = c.fetchone()
    if not appt:
        conn.close()
        return None, "Appointment not found."

    c.execute("UPDATE appointments SET status = 'cancelled' WHERE id = ?", (appt_id,))
    conn.commit()

    date_prefix = appt["appointment_time"].split(" ")[0]
    next_waitlist = get_matching_waitlisted_candidate(date_prefix, doctor_id=appt["doctor_id"])
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
    c.execute("SELECT appointment_time, doctor_id FROM appointments WHERE id = ?", (appt_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return f"Appointment #{appt_id} does not exist."

    parts = row["appointment_time"].split()
    target_date = parts[0]
    slot_time = parts[1][:5] if len(parts) > 1 else "10:00"

    candidate = get_matching_waitlisted_candidate(target_date, slot_time, row["doctor_id"])
    if not candidate:
        return f"No waitlisted patients waiting for {target_date}."

    ok, msg, _ = promote_waitlist_candidate(candidate["id"], slot_time)
    return msg


def block_doctor_date(target_date: str, doctor_id: int = None) -> str:
    """Mark a doctor unavailable for a date, cancel their bookings and notify patients."""
    if doctor_id is None:
        doctor_id = resolve_default_doctor_id()
    doctor = get_doctor(doctor_id)
    doctor_name = doctor["name"] if doctor else config.DOCTOR_NAME

    conn = get_db()
    c = conn.cursor()
    c.execute(
        """
        SELECT id, patient_name, patient_phone, appointment_time
        FROM appointments
        WHERE appointment_time LIKE ? AND status = 'confirmed' AND doctor_id = ?
        """,
        (f"{target_date}%", doctor_id),
    )
    appts = [dict(r) for r in c.fetchall()]

    c.execute(
        """
        INSERT INTO blocked_dates (doctor_id, blocked_date, reason)
        VALUES (?, ?, 'Doctor on leave')
        ON CONFLICT(doctor_id, blocked_date) DO UPDATE SET reason = excluded.reason
        """,
        (doctor_id, target_date),
    )
    c.execute(
        """
        UPDATE appointments SET status = 'doctor_cancelled'
        WHERE appointment_time LIKE ? AND status = 'confirmed' AND doctor_id = ?
        """,
        (f"{target_date}%", doctor_id),
    )
    conn.commit()
    conn.close()

    for a in appts:
        body = (
            f"Dear {a['patient_name']}, {doctor_name} is unavailable on *{target_date}* "
            f"due to a schedule change. Your appointment #{a['id']} has been cancelled. "
            f"Please reply with a preferred alternate date to reschedule."
        )
        send_whatsapp_message(a["patient_phone"], body)

    return (
        f"🛑 *{target_date} blocked for {doctor_name}.* "
        f"Cancelled {len(appts)} booking(s) and alerted affected patients."
    )


def unblock_doctor_date(target_date: str, doctor_id: int = None) -> str:
    if doctor_id is None:
        doctor_id = resolve_default_doctor_id()
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "DELETE FROM blocked_dates WHERE blocked_date = ? AND doctor_id = ?",
        (target_date, doctor_id),
    )
    conn.commit()
    changed = c.rowcount
    conn.close()
    if changed:
        return f"✅ *{target_date}* is now open for booking again."
    return f"{target_date} was not blocked."


def handle_doctor_commands(msg: str, doctor_id: int = None) -> str:
    today_str = str(dt_date.today())
    tomorrow_str = str(dt_date.today() + timedelta(days=1))

    if msg in ["briefing", "today", "schedule"]:
        return format_schedule_message(get_doctor_daily_summary(today_str, doctor_id), "Today's Briefing")
    if msg == "tomorrow":
        return format_schedule_message(get_doctor_daily_summary(tomorrow_str, doctor_id), "Tomorrow's Schedule")
    if msg.startswith("cancel"):
        parts = msg.split()
        if len(parts) > 1 and parts[1].isdigit():
            _, reply = cancel_doctor_appointment(int(parts[1]))
            return reply
        return "⚠ Format: *cancel <id>* (e.g., `cancel 1`)"
    if msg.startswith("fill"):
        parts = msg.split()
        if len(parts) > 1 and parts[1].isdigit():
            return handle_fill_slot(int(parts[1]))
        return "⚠ Format: *fill <appt_id>* (e.g., `fill 1`)"
    if msg.startswith("promote"):
        parts = msg.split()
        if len(parts) > 1 and parts[1].isdigit():
            ok, reply, _ = promote_waitlist_candidate(int(parts[1]))
            return reply
        return "⚠ Format: *promote <waitlist_id>* (e.g., `promote 1`)"
    if msg.startswith("unblock") or msg.startswith("open"):
        parts = msg.split()
        target = tomorrow_str if "tomorrow" in msg else (parts[1] if len(parts) > 1 and parts[1][:2].isdigit() else today_str)
        return unblock_doctor_date(target, doctor_id)
    if msg.startswith("block") or msg.startswith("leave"):
        parts = msg.split()
        target = tomorrow_str if "tomorrow" in msg else (parts[1] if len(parts) > 1 and parts[1][:2].isdigit() else today_str)
        return block_doctor_date(target, doctor_id)

    return (
        f"👨‍⚕ *{config.DOCTOR_NAME} Command Menu:*\n\n"
        "• `today` / `briefing` - View schedule & waitlist\n"
        "• `tomorrow` - View tomorrow's schedule\n"
        "• `cancel <id>` - Cancel an appointment\n"
        "• `block <date>` / `leave tomorrow` - Block day and alert patients\n"
        "• `unblock <date>` - Re-open a blocked day\n"
        "• `fill <appt_id>` - Promote waitlisted candidate\n"
        "• `promote <wl_id>` - Promote by WL ID"
    )


# --------------------------------------------------------------------------
# Patient intent helpers
# --------------------------------------------------------------------------
def check_emergency_triage(text: str) -> bool:
    clean = text.lower()
    return any(kw in clean for kw in EMERGENCY_KEYWORDS)


def get_open_slots_for_date(target_date: str, doctor_id: int = None) -> list[tuple[str, str]]:
    """(24h, 12h label) slots still open for a doctor on a date."""
    if doctor_id is None:
        doctor_id = resolve_default_doctor_id()
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "SELECT appointment_time FROM appointments "
        "WHERE appointment_time LIKE ? AND status = 'confirmed' AND doctor_id = ?",
        (f"{target_date}%", doctor_id),
    )
    booked_times = {row[0].split(" ")[1][:5] for row in c.fetchall() if " " in row[0]}
    conn.close()
    return [(s24, s12) for s24, s12 in STANDARD_CLINIC_SLOTS if s24 not in booked_times]


def parse_date_and_time(text: str):
    clean = text.lower().strip()
    today = datetime.now().date()
    target_date = None

    iso_match = re.search(r"\b(202\d-\d{2}-\d{2})\b", clean)
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
            if re.search(rf"\b{day_name}\b", clean):
                days_ahead = (idx - today.weekday() + 7) % 7
                if days_ahead == 0:
                    days_ahead = 7
                target_date = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
                clean = re.sub(rf"\b{day_name}\b", "", clean)
                break

    target_time = None
    time_match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", clean)
    if time_match:
        raw_hr = int(time_match.group(1))
        raw_min = int(time_match.group(2)) if time_match.group(2) else 0
        meridiem = time_match.group(3)

        if meridiem or time_match.group(2) or (9 <= raw_hr <= 20):
            if meridiem:
                if meridiem == "pm" and raw_hr < 12:
                    raw_hr += 12
                elif meridiem == "am" and raw_hr == 12:
                    raw_hr = 0
            elif 1 <= raw_hr <= 6:
                raw_hr += 12
            if 0 <= raw_hr <= 23 and 0 <= raw_min <= 59:
                target_time = f"{raw_hr:02d}:{raw_min:02d}"

    if not target_time:
        if re.search(r"\b(morning|forenoon|ravile)\b", clean):
            target_time = "10:00"
        elif re.search(r"\b(afternoon|uchakku)\b", clean):
            target_time = "14:30"
        elif re.search(r"\b(evening|vaikitt)\b", clean):
            target_time = "16:30"

    patient_name = None
    name_match = re.search(r"\bfor\s+([A-Za-z0-9\s\._-]+)", text, re.IGNORECASE)
    if name_match:
        raw_n = name_match.group(1).strip()
        patient_name = re.sub(
            r"\b(day after tomorrow|tomorrow|today|at|am|pm|morning|afternoon|evening)\b",
            "",
            raw_n,
            flags=re.IGNORECASE,
        ).strip()

    return target_date, target_time, patient_name


def detect_doctor_id(text: str) -> int | None:
    """Match 'with dr mehta' / 'dr nair' against the active roster."""
    lower = text.lower()
    for doc in get_active_doctors():
        surname = doc["name"].split()[-1].lower()
        if f"dr {surname}" in lower or f"doctor {surname}" in lower or surname in lower.split():
            return doc["id"]
    return None


def book_slot_in_db(clean_phone: str, final_name: str, target_date: str, target_time: str, doctor_id: int = None) -> str:
    if doctor_id is None:
        doctor_id = resolve_default_doctor_id()
    doctor = get_doctor(doctor_id)
    doctor_name = doctor["name"] if doctor else config.DOCTOR_NAME
    fee = doctor["consultation_fee"] if doctor else config.CONSULTATION_FEE

    try:
        if datetime.strptime(target_date, "%Y-%m-%d").weekday() == 6:
            return f"❌ The clinic is closed on Sundays ({target_date}). Please choose Monday through Saturday."
    except ValueError:
        return f"❌ I couldn't read the date '{target_date}'. Please try again."

    appointment_datetime = f"{target_date} {target_time}:00"
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute(
            """
            INSERT INTO appointments
                (patient_name, patient_phone, doctor_name, appointment_time, status, doctor_id)
            VALUES (?, ?, ?, ?, 'confirmed', ?)
            """,
            (final_name, clean_phone, doctor_name, appointment_datetime, doctor_id),
        )
        conn.commit()
        clear_session(clean_phone)
        return (
            f"✅ *Appointment Confirmed!*\n\n"
            f"• *Patient*: {final_name}\n"
            f"• *Doctor*: {doctor_name}\n"
            f"• *Date*: {target_date}\n"
            f"• *Time*: {target_time}\n"
            f"• *Clinic*: {config.CLINIC_NAME}\n"
            f"• *Consultation Fee*: ₹{fee}\n\n"
            f"📍 *Location*: {config.CLINIC_MAPS_URL}\n\n"
            f"💳 *Payment Options:*\n"
            f"1. Pay via UPI: upi://pay?pa={config.CLINIC_UPI_ID}&pn={config.CLINIC_UPI_NAME.replace(' ', '%20')}&am={fee}&cu=INR&tn=Consultation%20Fee\n"
            f"2. Pay at clinic counter upon arrival (Cash/UPI)\n\n"
            f"To view bookings, reply *'My appointments'*. To cancel, reply *'Cancel'*."
        )
    except sqlite3.IntegrityError:
        # Slot taken for this doctor — offer the waitlist instead.
        c.execute(
            """
            INSERT INTO waitlist
                (patient_name, patient_phone, preferred_date, preferred_time, status, doctor_id)
            VALUES (?, ?, ?, ?, 'waiting', ?)
            """,
            (final_name, clean_phone, target_date, target_time, doctor_id),
        )
        conn.commit()
        clear_session(clean_phone)
        return (
            f"⚠️ The *{target_time}* slot on *{target_date}* with {doctor_name} is already reserved.\n"
            f"You have been placed on the *priority waitlist*. If a cancellation occurs, you will be notified immediately."
        )
    finally:
        conn.close()


def handle_cancellation(phone: str, appt_id: int = None) -> tuple[bool, str]:
    """Cancel a patient's appointment. Returns (success, patient_message)."""
    reply = cancel_patient_appointment(phone, appt_id)
    success = reply.startswith("❌")
    return success, reply


# --------------------------------------------------------------------------
# Main conversation router
# --------------------------------------------------------------------------
def _doctor_menu() -> str:
    lines = ["👨‍⚕ *Which doctor would you like to see?*\n"]
    for i, doc in enumerate(get_active_doctors(), start=1):
        lines.append(f"{i}️⃣ {doc['name']} — {doc['specialty']} (₹{doc['consultation_fee']})")
    lines.append("\n👉 Reply with the doctor number.")
    return "\n".join(lines)


def _slot_menu(target_date: str, doctor_id: int, profile_name: str) -> str:
    open_slots = get_open_slots_for_date(target_date, doctor_id)
    if not open_slots:
        return (
            f"😔 All regular slots for *{target_date}* are currently full. "
            f"Reply with a preferred time (e.g., *'Book 10:30 AM {target_date}'*) to join the priority waitlist!"
        )
    number_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"]
    doctor = get_doctor(doctor_id)
    lines = [f"📅 *Available slots for {target_date}*"
             + (f" with {doctor['name']}" if doctor else "") + ":\n"]
    for i, (_, label12) in enumerate(open_slots):
        badge = number_emojis[i] if i < len(number_emojis) else f"{i + 1}."
        lines.append(f"{badge} {label12}")
    lines.append(
        f"\n👉 *Reply with the slot number* (e.g., *1*) to instant-book for *{profile_name}*, "
        f"or reply *'2 for [Patient Name]'*!"
    )
    return "\n".join(lines)


def handle_receptionist_ai(incoming_msg: str, sender_phone: str, profile_name: str = "Patient") -> str:
    msg_clean = incoming_msg.strip()
    msg_lower = msg_clean.lower()
    clean_phone = sender_phone.replace("whatsapp:", "").replace("+", "").strip()

    # 0. Doctor admin commands (any active doctor's phone).
    doctor = get_doctor_by_phone(clean_phone)
    if doctor and msg_lower.split()[0] in [
        "briefing", "today", "schedule", "tomorrow", "cancel", "fill",
        "promote", "block", "leave", "unblock", "open",
    ]:
        return handle_doctor_commands(msg_lower, doctor["id"])

    # 1. Emergency triage always preempts, even when the bot is muted.
    if check_emergency_triage(msg_clean):
        return trigger_emergency_escalation(clean_phone, profile_name, msg_clean)

    # 2. Staff takeover / bot mute.
    if msg_lower in ["resume bot", "start bot", "unmute", "bot"]:
        resolve_escalation(clean_phone)
        return f"🤖 Automated assistant reactivated for {profile_name}! How can I help you book or manage your appointment today?"

    if is_patient_escalated(clean_phone):
        return ""

    if re.search(r"\b(speak to human|talk to human|human|staff|receptionist|real person|call me|agent)\b", msg_lower):
        return trigger_escalation(clean_phone, profile_name, f"Patient requested human takeover: {msg_clean}")

    # 3. Medical advice guardrail (non-clinical by design).
    if re.search(r"\b(prescribe|prescription|medicine|antibiotic|antibiotics|dosage|diagnose|fever|cough|tablet|painkiller)\b", msg_lower) \
            and not re.search(r"\b(book|slot|cancel)\b", msg_lower):
        return (
            "⚠️ *Medical Notice*:\n"
            "As an AI clinic assistant, I cannot diagnose illnesses or prescribe medications.\n\n"
            f"Please book an in-person consultation with {config.DOCTOR_NAME} so you can be properly evaluated. "
            "Reply *'Available slots tomorrow'* to schedule an appointment."
        )

    # 4. Clinic information.
    if re.search(r"\b(where|location|address|place|find you|map|direction|directions)\b", msg_lower) or \
       (re.search(r"\b(timings?|hours|open|closed|closing)\b", msg_lower) and not re.search(r"\b(slot|book|reserve)\b", msg_lower)):
        return (
            f"🏥 *{config.CLINIC_NAME}*\n\n"
            f"📍 *Address*: {config.CLINIC_ADDRESS}\n"
            f"🗺️ *Google Maps Pin*: {config.CLINIC_MAPS_URL}\n"
            f"🕒 *Working Hours*: {config.CLINIC_HOURS_TEXT}\n"
            f"💰 *Consultation Fee*: ₹{config.CONSULTATION_FEE}\n\n"
            "Reply *'Available slots tomorrow'* to view open times and book!"
        )

    if re.search(r"\b(fee|fees|cost|charge|charges|price|payment)\b", msg_lower) and not re.search(r"\b(book|reserve)\b", msg_lower):
        return (
            f"💰 *Consultation Fee*: ₹{config.CONSULTATION_FEE} (Follow-up within 7 days: ₹{config.FOLLOWUP_FEE})\n\n"
            "We accept UPI (GPay, PhonePe, Paytm), Cards, and Cash at the clinic counter."
        )

    # 5. View active bookings.
    if re.search(r"\b(my appointment|my appointments|what appointment|check booking|active booking|my slot|booked)\b", msg_lower) \
            and not re.search(r"\b(book|cancel)\b", msg_lower):
        conn = get_db()
        c = conn.cursor()
        c.execute(
            """
            SELECT id, patient_name, appointment_time, status
            FROM appointments
            WHERE patient_phone = ? AND status = 'confirmed'
            ORDER BY appointment_time ASC
            """,
            (clean_phone,),
        )
        rows = c.fetchall()
        conn.close()

        if not rows:
            return "You do not have any active confirmed appointments under this phone number. Reply *'Available slots tomorrow'* to book one!"

        reply = "📋 *Your Confirmed Appointments*:\n"
        for r in rows:
            reply += f"• *{r['patient_name']}*: {r['appointment_time']} (ID #{r['id']})\n"
        reply += "\nReply *'Cancel my appointment'* if you need to cancel or reschedule."
        return reply

    # 6. Cancellation request.
    if re.search(r"\b(cancel|cancellation)\b", msg_lower):
        id_match = re.search(r"cancel\s+(\d+)", msg_lower)
        appt_id = int(id_match.group(1)) if id_match else None
        _, reply = handle_cancellation(clean_phone, appt_id)
        clear_session(clean_phone)
        return reply

    # 7. Conversation context.
    session = get_session(clean_phone) or {}
    pending_intent = session.get("pending_intent")
    session_doctor = session.get("doctor_id")

    num_choice_match = re.match(r"^([1-9])(?:\s+for\s+([A-Za-z0-9\s\._-]+))?$", msg_clean, re.IGNORECASE)

    if pending_intent == "awaiting_doctor_selection" and num_choice_match:
        doctors = get_active_doctors()
        idx = int(num_choice_match.group(1)) - 1
        if 0 <= idx < len(doctors):
            chosen = doctors[idx]["id"]
            target_date = session.get("last_date") or (datetime.now().date() + timedelta(days=1)).strftime("%Y-%m-%d")
            update_session(clean_phone, last_date=target_date, pending_intent="awaiting_time_selection", doctor_id=chosen)
            return _slot_menu(target_date, chosen, profile_name)
        return f"⚠️ Invalid option. Please reply with a number between 1 and {len(doctors)}."

    if pending_intent == "awaiting_time_selection" and num_choice_match:
        choice_idx = int(num_choice_match.group(1)) - 1
        custom_name = num_choice_match.group(2).strip() if num_choice_match.group(2) else None
        target_date = session.get("last_date") or (datetime.now().date() + timedelta(days=1)).strftime("%Y-%m-%d")
        doctor_id = session_doctor or resolve_default_doctor_id()
        open_slots = get_open_slots_for_date(target_date, doctor_id)

        if 0 <= choice_idx < len(open_slots):
            chosen_24h, _ = open_slots[choice_idx]
            final_name = custom_name or (profile_name if profile_name != "Patient" else "Patient")
            return book_slot_in_db(clean_phone, final_name, target_date, chosen_24h, doctor_id)
        return f"⚠️ Invalid option number. Please reply with a number between *1* and *{len(open_slots)}*."

    if pending_intent == "awaiting_reminder_ack":
        if msg_clean == "1":
            clear_session(clean_phone)
            return "✅ Thank you! Your appointment has been confirmed. See you at the clinic."
        if msg_clean in ("2", "cancel"):
            _, reply = handle_cancellation(clean_phone)
            clear_session(clean_phone)
            return f"❌ {reply}"

    # 7b. Post-consultation feedback / Google-review funnel.
    feedback = handle_feedback_response(clean_phone, msg_clean)
    if feedback:
        return feedback

    # 7c. Legacy bare "1" confirmation (session expired but a visit is upcoming).
    if msg_clean == "1":
        conn = get_db()
        c = conn.cursor()
        c.execute(
            """
            SELECT id, appointment_time FROM appointments
            WHERE patient_phone = ? AND status = 'confirmed'
            ORDER BY appointment_time ASC LIMIT 1
            """,
            (clean_phone,),
        )
        upcoming = c.fetchone()
        conn.close()
        if upcoming:
            return (
                f"✅ *Attendance Confirmed!* Thank you {profile_name}. "
                f"Your appointment (#{upcoming['id']} on {upcoming['appointment_time']}) is confirmed. "
                f"We look forward to seeing you."
            )

    # 8. List available slots (number-keyed menu).
    if re.search(r"\b(slot|slots|available|free slots)\b", msg_lower) and not re.search(r"\b(book|reserve)\b", msg_lower):
        target_date, _, _ = parse_date_and_time(msg_clean)
        if not target_date:
            target_date = (datetime.now().date() + timedelta(days=1)).strftime("%Y-%m-%d")

        try:
            if datetime.strptime(target_date, "%Y-%m-%d").weekday() == 6:
                next_mon = (datetime.strptime(target_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
                return f"📅 The clinic is closed on Sundays ({target_date}).\nOur next working day is Monday ({next_mon}). Reply *'Available slots Monday'* to check slots."
        except ValueError:
            pass

        doctors = get_active_doctors()
        named = detect_doctor_id(msg_clean)
        if named:
            update_session(clean_phone, last_date=target_date, pending_intent="awaiting_time_selection", doctor_id=named)
            return _slot_menu(target_date, named, profile_name)
        if len(doctors) > 1:
            update_session(clean_phone, last_date=target_date, pending_intent="awaiting_doctor_selection", doctor_id=None)
            return _doctor_menu()

        doctor_id = doctors[0]["id"] if doctors else 1
        update_session(clean_phone, last_date=target_date, pending_intent="awaiting_time_selection", doctor_id=doctor_id)
        return _slot_menu(target_date, doctor_id, profile_name)

    # 9. Book appointment (direct or fuzzy).
    target_date, target_time, patient_name = parse_date_and_time(msg_clean)
    if re.search(r"\b(book|reserve|schedule|appointment)\b", msg_lower) or pending_intent == "awaiting_time_selection" or (target_date and target_time):
        if not target_date and session.get("last_date"):
            target_date = session.get("last_date")
        if not target_date:
            target_date = (datetime.now().date() + timedelta(days=1)).strftime("%Y-%m-%d")

        final_name = patient_name or (profile_name if profile_name != "Patient" else "Patient")

        doctors = get_active_doctors()
        chosen_doctor = detect_doctor_id(msg_clean) or session_doctor
        if chosen_doctor is None and len(doctors) > 1:
            update_session(clean_phone, last_date=target_date, pending_intent="awaiting_doctor_selection", doctor_id=None)
            return _doctor_menu()
        doctor_id = chosen_doctor or (doctors[0]["id"] if doctors else 1)

        if target_time:
            return book_slot_in_db(clean_phone, final_name, target_date, target_time, doctor_id)

        open_slots = get_open_slots_for_date(target_date, doctor_id)
        sample_times = " or ".join([s[1] for s in open_slots[:2]]) if open_slots else "10:00 AM or 10:30 AM"
        update_session(clean_phone, last_date=target_date, pending_intent="awaiting_time_selection", doctor_id=doctor_id)
        return (
            f"🤔 I couldn't quite catch the time for *{target_date}*.\n\n"
            f"Did you mean *{sample_times}*?\n"
            f"Reply with a slot number (e.g., *1* or *2*), a time like *'10:30 AM'*, or *'morning'* / *'afternoon'*."
        )

    # 10. Out of scope.
    if re.search(r"\b(car|engine|crypto|bitcoin|flight|hotel|plumber|mechanic|movie)\b", msg_lower):
        return (
            f"I am the AI receptionist for {config.CLINIC_NAME}. "
            "I can assist you with appointment bookings, slot availability, clinic timings, and clinic location. "
            "How may I assist you with your clinic visit today?"
        )

    # 11. Smart AI Q&A fallback.
    # Simple short greetings show the quick action menu
    if msg_lower in ["hi", "hello", "hey", "start", "namaste", "good morning", "good evening"]:
        return (
            f"Hello {profile_name}! 👋 Welcome to *{config.CLINIC_NAME}*.\n\n"
            "How can I help you today?\n"
            "• 📅 *Check open slots*: 'Available slots tomorrow'\n"
            "• ⚡ *Quick book*: 'Book morning tomorrow' or 'Book 10:30 am'\n"
            "• 📋 *My bookings*: 'What appointments do I have booked'\n"
            "• 📍 *Location & Map*: 'Where is the clinic located'\n"
            "• 👤 *Staff Help*: 'Speak to receptionist'"
        )

    # All other inquiries, parking, insurance, qualifications, etc. answered by AI
    ai_answer = answer_clinic_query(msg_clean, profile_name)
    if ai_answer:
        return ai_answer

    return (
        f"Hello {profile_name}! 👋 How can I help you at *{config.CLINIC_NAME}*?\n"
        "You can ask about clinic timings, fees, parking, or reply *'Available slots tomorrow'* to book an appointment."
    )


def trigger_doctor_briefing():
    """Build (and log) the doctor's morning briefing string."""
    today_str = datetime.now().strftime("%Y-%m-%d")
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute(
            """
            SELECT patient_name, appointment_time, status
            FROM appointments
            WHERE appointment_time LIKE ? AND status = 'confirmed'
            ORDER BY appointment_time ASC
            """,
            (f"{today_str}%",),
        )
        appts = c.fetchall()

        c.execute(
            "SELECT COUNT(*) AS count FROM waitlist WHERE preferred_date = ? AND status = 'waiting'",
            (today_str,),
        )
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
    except Exception as e:  # pragma: no cover - logged, non-fatal
        print(f"Error generating briefing: {e}")
        return f"Error generating daily briefing: {e}"
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Webhook + admin endpoints
# --------------------------------------------------------------------------
WEBHOOK_SHARED_SECRET = os.getenv("WEBHOOK_SHARED_SECRET", "").strip()


@doctor_router.post("/whatsapp-webhook")
async def whatsapp_webhook(request: Request, x_webhook_secret: str = Header(None)):
    if WEBHOOK_SHARED_SECRET and x_webhook_secret != WEBHOOK_SHARED_SECRET:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

    data = {}
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON payload")
    else:
        # Twilio / Meta style form POSTs.
        try:
            form = await request.form()
            data = dict(form)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid form payload")

    sender_phone = str(data.get("phone") or data.get("from") or data.get("From") or "").strip()
    sender_name = str(data.get("name") or data.get("sender_name") or data.get("ProfileName") or "Patient").strip()
    message_body = str(data.get("message") or data.get("text") or data.get("body") or data.get("Body") or "").strip()

    if not sender_phone or not message_body:
        return {"status": "ignored", "detail": "Missing phone or message text"}

    try:
        reply_text = handle_receptionist_ai(
            incoming_msg=message_body,
            sender_phone=sender_phone,
            profile_name=sender_name,
        )
        return {"status": "success", "reply": reply_text}
    except Exception as e:
        print(f"Error executing receptionist AI: {e}")
        return {
            "status": "error",
            "reply": f"Thank you for contacting {config.CLINIC_NAME}. An assistant will get back to you shortly.",
        }


@doctor_router.post("/trigger-followups", dependencies=[Depends(verify_admin)])
async def trigger_followups(days_ago: int = 2):
    """Manually dispatch post-consultation follow-ups (normally run by the scheduler)."""
    result = dispatch_post_consultation_followups(days_ago=days_ago)
    return {"status": "success", **result}


@doctor_router.post("/whatsapp-voice-webhook")
async def handle_whatsapp_voice(request: Request):
    import base64
    from app.ai_agent import transcribe_and_process_voice
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json payload")

    sender_phone = str(data.get("phone") or "").strip()
    sender_name = str(data.get("name") or "Patient").strip()
    audio_base64 = data.get("audio_base64")
    mime_type = data.get("mime_type") or "audio/ogg; codecs=opus"

    if not sender_phone or not audio_base64:
        return {"status": "ignored", "detail": "missing phone or audio data"}

    try:
        audio_bytes = base64.b64decode(audio_base64)
        clean_mime = mime_type.split(";")[0].strip()
        transcribed_text = transcribe_and_process_voice(audio_bytes, mime_type=clean_mime)
        if not transcribed_text:
            return {
                "status": "success",
                "reply": "I received your voice note, but I could not clearly hear the audio. Could you please send your request as text or try re-recording?"
            }

        reply_text = handle_receptionist_ai(
            incoming_msg=transcribed_text,
            sender_phone=sender_phone,
            profile_name=sender_name
        )
        return {
            "status": "success",
            "transcription": transcribed_text,
            "reply": f"🎙️ *Heard*: \"{transcribed_text}\"\n\n{reply_text}"
        }
    except Exception as e:
        logger.error(f"error processing whatsapp voice webhook: {e}")
        return {
            "status": "error",
            "reply": "Thank you for contacting the clinic. We could not process your voice note at this moment. Please reply with text."
        }
