
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

def handle_receptionist_ai(raw_text: str, sender_phone: str, sender_name: str) -> str:
    clean = raw_text.strip()
    session = get_session(sender_phone)
    # Check if message is a post-consultation 1-5 feedback rating
    feedback_reply = handle_feedback_response(sender_phone, raw_text)
    if feedback_reply:
        return feedback_reply

    parsed = parse_patient_intent(raw_text)
    intent = parsed["intent"]

    # Check if patient wants to resume the bot
    if intent == "resume_bot":
        resolve_escalation(sender_phone)
        return f"🤖 Receptionist AI reactivated! How can I assist you today, {sender_name}?"

    # Check if this patient is currently escalated to staff
    if is_patient_escalated(sender_phone):
        return "ℹ Your chat is currently assigned to front-desk staff. A receptionist will assist you directly. To switch back to AI, reply *'resume bot'*."

    # Process explicit escalation
    if intent == "escalate":
        return trigger_escalation(sender_phone, sender_name, parsed.get("reason", "Requested staff"))
    
    if clean == "1":
        appts = get_patient_active_appointments(sender_phone)
        if appts:
            return f"✅ Thank you {sender_name}! Your appointment on *{appts[0]['appointment_time']}* is confirmed. We look forward to seeing you!"
        return "You have no upcoming appointment to confirm."
    elif clean == "2":
        return cancel_patient_appointment(sender_phone)

    if intent == "emergency":
        return CLINIC_FAQ["emergency"]
    if intent == "faq_fees":
        return CLINIC_FAQ["fees"]
    if intent == "faq_location":
        return CLINIC_FAQ["location"]
    if intent == "faq_hours":
        return CLINIC_FAQ["hours"]

    if intent == "cancel":
        clear_session(sender_phone)
        return cancel_patient_appointment(sender_phone, parsed.get("appt_id"))

    if intent == "inquire_slots":
        target = parsed.get("date") or session.get("last_date") or str(dt_date.today())
        
        blocked, reason = is_date_blocked(target)
        if blocked:
            return f"ℹ *{target}*: {reason}\nOur clinic hours are Monday through Saturday, 9:00 AM – 5:30 PM."

        update_session(sender_phone, last_date=target, pending_intent="book")
        avail = get_available_slots(target)
        if avail:
            return (
                f"🏥 *Available Slots for {target}:*\n"
                f"{', '.join(avail[:8])}\n\n"
                f"To book, reply with your preferred time (e.g., *'10am'* or *'2:30pm'*)."
            )
        return f"All slots are currently booked for {target}."

    if intent == "reschedule":
        target_date = parsed.get("date") or session.get("last_date") or str(dt_date.today())
        target_time = parsed.get("time")
        if not target_time:
            update_session(sender_phone, last_date=target_date, pending_intent="reschedule")
            return f"What time would you like to reschedule your appointment to on *{target_date}*?"
        
        clear_session(sender_phone)
        return reschedule_appointment(sender_phone, target_date, target_time)

    if intent == "status":
        active_list = get_patient_active_appointments(sender_phone)
        if active_list:
            lines = ["📋 *Your Upcoming Appointment(s):*"]
            for a in active_list:
                lines.append(f"• ID #{a['id']}: *{a['appointment_time']}* (Confirmed)")
            return "\n".join(lines)
        return "You do not have any upcoming confirmed appointments scheduled with Dr. Kurian."

    if intent == "book":
        target_date = parsed.get("date") or session.get("last_date") or str(dt_date.today())
        target_time = parsed.get("time")

        if not target_time:
            update_session(sender_phone, last_date=target_date, pending_intent="book")
            avail = get_available_slots(target_date)
            return (
                f"What time would you prefer on *{target_date}*?\n"
                f"Available slots: {', '.join(avail[:6])}\n"
                f"Reply with a time (e.g., *'10:30 am'*)."
            )

        patient_name = sender_name or "Patient"
        success, reply = book_appointment(patient_name, sender_phone, target_date, target_time)
        if success:
            clear_session(sender_phone)
        return reply

    return (
        f"Hello {sender_name}! 👋 Welcome to *Dr. Kurian's Medical Clinic*.\n\n"
        f"You can message naturally to manage your visit:\n"
        f"• *Check availability*: _'Are there slots tomorrow?'_\n"
        f"• *Book an appointment*: _'Can I book 2:30pm tomorrow?'_\n"
        f"• *Check fee/timings*: _'How much is consultation?'_\n"
        f"• *Reschedule*: _'Reschedule my visit to 3pm tomorrow'_\n"
        f"• *Speak with staff*: _'Talk to human'_\n"
        f"• *Cancel*: _'Cancel my appointment'_"
    )

@doctor_router.get("/summary")
def get_summary_endpoint(date: str = None):
    return get_doctor_daily_summary(date)

@doctor_router.post("/send-briefing")
def trigger_doctor_briefing(date: str = None):
    return {
        "status": "ready",
        "channel": "WhatsApp Webhook",
        "recipient": f"whatsapp:{RAW_DOCTOR_PHONE}",
        "summary": get_doctor_daily_summary(date)
    }

@doctor_router.post("/trigger-reminders")
def trigger_reminders_endpoint(date: str = None):
    return dispatch_day_before_reminders(date)

@doctor_router.post("/whatsapp-webhook")
async def whatsapp_webhook_handler(request: Request):
    form_data = await request.form()
    incoming_msg = (form_data.get("Body") or "").strip()
    raw_from = form_data.get("From") or ""
    sender_phone = raw_from.replace("whatsapp:", "").strip()
    profile_name = form_data.get("ProfileName") or "Patient"

    clean_msg = incoming_msg.lower()

    if is_doctor(sender_phone) and not clean_msg.startswith("patient "):
        reply_text = handle_doctor_commands(clean_msg)
    else:
        if clean_msg.startswith("patient "):
            incoming_msg = incoming_msg[8:].strip()
        reply_text = handle_receptionist_ai(incoming_msg, sender_phone, profile_name)

    resp = MessagingResponse()
    resp.message(reply_text)
    return Response(content=str(resp), media_type="application/xml")

@doctor_router.post("/trigger-followups")
def trigger_followups_endpoint(days_ago: int = 2):
    return dispatch_post_consultation_followups(days_ago)

def handle_cancellation(sender_phone: str):
    """
    Atomically cancels the patient appointment, vacates the UNIQUE constraint slot,
    and auto-promotes the next patient from the waitlist without holding write locks.
    """
    clean_phone = sender_phone.replace("whatsapp:", "").strip()
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    promoted_patient = None
    try:
        # 1. Fetch active appointment
        c.execute("""
            SELECT id, appointment_time, doctor_id 
            FROM appointments 
            WHERE (patient_phone = ? OR patient_phone = ?) AND status = 'confirmed'
            ORDER BY id DESC LIMIT 1
        """, (clean_phone, f"+{clean_phone}"))
        appt = c.fetchone()
        
        if not appt:
            return False, "You do not have any active confirmed appointment to cancel."

        appt_id = appt["id"]
        appt_time = appt["appointment_time"]
        doc_id = appt["doctor_id"] if appt["doctor_id"] else 1

        # 2. Mark existing record as cancelled to release the slot
        c.execute("UPDATE appointments SET status = 'cancelled' WHERE id = ?", (appt_id,))

        # 3. Parse date and time to search waitlist (format: YYYY-MM-DD HH:MM:SS)
        parts = str(appt_time).split(" ")
        pref_date = parts[0]
        pref_time = parts[1][:5] if len(parts) > 1 else "10:00"

        c.execute("""
            SELECT id, patient_name, patient_phone 
            FROM waitlist 
            WHERE preferred_date = ? AND preferred_time = ? AND status = 'waiting' AND doctor_id = ?
            ORDER BY created_at ASC LIMIT 1
        """, (pref_date, pref_time, doc_id))
        waiter = c.fetchone()

        if waiter:
            # Re-occupy slot cleanly with the promoted patient
            c.execute("""
                INSERT INTO appointments (patient_name, patient_phone, appointment_time, status, doctor_id)
                VALUES (?, ?, ?, 'confirmed', ?)
            """, (waiter["patient_name"], waiter["patient_phone"], appt_time, doc_id))
            
            c.execute("UPDATE waitlist SET status = 'promoted' WHERE id = ?", (waiter["id"],))
            promoted_patient = dict(waiter)

        conn.commit()
        
        reply_msg = "Your appointment has been successfully cancelled."
        if promoted_patient:
            reply_msg += f" Slot {pref_time} on {pref_date} was automatically assigned to waitlisted patient."
        return True, reply_msg
    except Exception as e:
        conn.rollback()
        print(f"❌ Error in atomic handle_cancellation: {e}")
        return False, "An error occurred while processing your cancellation. Please try again."
    finally:
        conn.close()
