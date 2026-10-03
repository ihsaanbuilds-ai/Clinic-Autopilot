import sqlite3
import os
import re
from datetime import datetime, date as dt_date, timedelta
from app.notifier import send_whatsapp_message

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "clinic.db")

WORKING_SLOTS = [
    "09:00", "09:30", "10:00", "10:30", "11:00", "11:30",
    "12:00", "12:30", "14:00", "14:30", "15:00", "15:30",
    "16:00", "16:30", "17:00"
]

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def parse_time_input(raw_time: str) -> str | None:
    raw = raw_time.strip().lower().replace(".", "")
    match = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", raw)
    if not match:
        return None
    
    hours, minutes, meridiem = match.groups()
    hours = int(hours)
    minutes = int(minutes or 0)
    
    if meridiem == "pm" and hours < 12:
        hours += 12
    elif meridiem == "am" and hours == 12:
        hours = 0
        
    formatted = f"{hours:02d}:{minutes:02d}"
    return formatted if formatted in WORKING_SLOTS else None

def is_date_blocked(target_date: str) -> tuple[bool, str]:
    try:
        parsed_dt = datetime.strptime(target_date, "%Y-%m-%d").date()
        if parsed_dt.weekday() == 6:
            return True, "Dr. Kurian's clinic is closed on Sundays for scheduled appointments."
    except ValueError:
        pass

    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT COUNT(*) as count FROM appointments
        WHERE appointment_time LIKE ? AND status = 'doctor_cancelled'
    """, (f"{target_date}%",))
    row = c.fetchone()
    conn.close()

    if row and row["count"] > 0:
        return True, f"Dr. Kurian is unavailable on {target_date} due to a prior schedule hold."
    return False, ""

def get_available_slots(target_date: str) -> list[str]:
    blocked, _ = is_date_blocked(target_date)
    if blocked:
        return []

    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT appointment_time FROM appointments
        WHERE appointment_time LIKE ? AND status = 'confirmed'
    """, (f"{target_date}%",))
    rows = c.fetchall()
    conn.close()

    booked = {r["appointment_time"].split()[1][:5] for r in rows if len(r["appointment_time"].split()) > 1}

    now = datetime.now()
    today_str = str(now.date())
    current_time_str = now.strftime("%H:%M")

    valid_slots = []
    for s in WORKING_SLOTS:
        if s in booked:
            continue
        if target_date == today_str and s <= current_time_str:
            continue
        valid_slots.append(s)

    return valid_slots

def find_nearest_available(target_date: str, requested_slot: str) -> list[str]:
    avail = get_available_slots(target_date)
    if not avail:
        return []
    try:
        req_idx = WORKING_SLOTS.index(requested_slot)
    except ValueError:
        return avail[:2]

    sorted_slots = sorted(avail, key=lambda s: abs(WORKING_SLOTS.index(s) - req_idx))
    return sorted_slots[:2]

def get_patient_active_appointments(phone: str) -> list[dict]:
    conn = get_db()
    c = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    c.execute("""
        SELECT id, patient_name, appointment_time, status FROM appointments 
        WHERE patient_phone LIKE ? 
          AND status = 'confirmed'
          AND appointment_time >= ?
        ORDER BY appointment_time ASC
    """, (f"%{phone[-10:]}%", now_str))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def book_appointment(name: str, phone: str, target_date: str, raw_time: str) -> tuple[bool, str]:
    blocked, reason = is_date_blocked(target_date)
    if blocked:
        return False, f"⚠ Cannot book for *{target_date}*: {reason}"

    normalized_time = parse_time_input(raw_time)
    if not normalized_time:
        return False, f"⚠ '{raw_time}' is outside clinic working hours (9:00 AM - 5:00 PM, 30-min slots)."

    now = datetime.now()
    if target_date == str(now.date()) and normalized_time <= now.strftime("%H:%M"):
        return False, f"⚠ Slot {normalized_time} has already passed for today. Please choose an upcoming time."

    conn = get_db()
    c = conn.cursor()

    c.execute("""
        SELECT id, appointment_time FROM appointments
        WHERE patient_phone LIKE ? AND appointment_time LIKE ? AND status = 'confirmed'
    """, (f"%{phone[-10:]}%", f"{target_date}%"))
    existing = c.fetchone()
    if existing:
        conn.close()
        time_part = existing["appointment_time"].split()[1][:5]
        return False, f"ℹ You already have a confirmed appointment on *{target_date}* at *{time_part}* (ID #{existing['id']}). To change your time, reply with: *'Reschedule to {raw_time}'*."

    avail = get_available_slots(target_date)

    if normalized_time in avail:
        full_time = f"{target_date} {normalized_time}:00"
        try:
            c.execute("""
                INSERT INTO appointments (patient_name, patient_phone, appointment_time, status)
                VALUES (?, ?, ?, 'confirmed')
            """, (name, phone, full_time))
            appt_id = c.lastrowid
            conn.commit()
            conn.close()
            return True, f"✅ Appointment confirmed for *{name}* on *{target_date}* at *{normalized_time}* (ID #{appt_id})."
        except sqlite3.IntegrityError:
            conn.rollback()

    alternatives = find_nearest_available(target_date, normalized_time)
    alt_text = f" Closest available slots: *{', '.join(alternatives)}*." if alternatives else ""
    
    c.execute("""
        INSERT INTO waitlist (patient_name, patient_phone, preferred_date, preferred_time, status)
        VALUES (?, ?, ?, ?, 'waiting')
    """, (name, phone, target_date, normalized_time))
    wait_id = c.lastrowid
    conn.commit()
    conn.close()
    
    return False, (
        f"⚠ Slot *{normalized_time}* on *{target_date}* is fully booked.{alt_text}\n\n"
        f"You are placed on the priority waitlist (WL #{wait_id}). If someone cancels, this slot will be offered to you."
    )

def reschedule_appointment(phone: str, target_date: str, raw_time: str) -> str:
    blocked, reason = is_date_blocked(target_date)
    if blocked:
        return f"⚠ Cannot reschedule to *{target_date}*: {reason}"

    appts = get_patient_active_appointments(phone)
    if not appts:
        return "You have no upcoming appointment to reschedule. You can book a new one directly."

    target_appt = appts[0]
    normalized_time = parse_time_input(raw_time)
    if not normalized_time:
        return f"⚠ '{raw_time}' is outside clinic working hours (9:00 AM - 5:00 PM)."

    avail = get_available_slots(target_date)
    if normalized_time not in avail:
        alternatives = find_nearest_available(target_date, normalized_time)
        alt_text = f" Open slots: {', '.join(alternatives)}" if alternatives else "No slots open."
        return f"⚠ Cannot reschedule to {normalized_time} on {target_date} (already booked).{alt_text}"

    new_datetime = f"{target_date} {normalized_time}:00"
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        UPDATE appointments 
        SET appointment_time = ? 
        WHERE id = ?
    """, (new_datetime, target_appt["id"]))
    conn.commit()
    conn.close()

    return f"🔄 Appointment #{target_appt['id']} successfully rescheduled to *{target_date}* at *{normalized_time}*."

def get_matching_waitlisted_candidate(target_date: str, slot_time: str = None):
    conn = get_db()
    c = conn.cursor()
    if slot_time:
        c.execute("""
            SELECT id, patient_name, patient_phone, preferred_date, preferred_time
            FROM waitlist
            WHERE preferred_date = ? AND preferred_time = ? AND status = 'waiting'
            ORDER BY id ASC LIMIT 1
        """, (target_date, slot_time))
        candidate = c.fetchone()
        if candidate:
            conn.close()
            return dict(candidate)

    c.execute("""
        SELECT id, patient_name, patient_phone, preferred_date, preferred_time
        FROM waitlist
        WHERE preferred_date = ? AND status = 'waiting'
        ORDER BY id ASC LIMIT 1
    """, (target_date,))
    patient = c.fetchone()
    conn.close()
    return dict(patient) if patient else None

def promote_waitlist_candidate(waitlist_id: int, target_time: str = None) -> tuple[bool, str, dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM waitlist WHERE id = ? AND status = 'waiting'", (waitlist_id,))
    item = c.fetchone()
    if not item:
        conn.close()
        return False, f"Waitlist entry #{waitlist_id} not found or already processed.", {}

    item = dict(item)
    chosen_time = target_time or item["preferred_time"]
    full_datetime = f"{item['preferred_date']} {chosen_time}:00"

    c.execute("""
        INSERT INTO appointments (patient_name, patient_phone, appointment_time, status)
        VALUES (?, ?, ?, 'confirmed')
    """, (item["patient_name"], item["patient_phone"], full_datetime))
    new_appt_id = c.lastrowid

    c.execute("UPDATE waitlist SET status = 'promoted' WHERE id = ?", (waitlist_id,))
    conn.commit()
    conn.close()

    notify_body = (
        f"🎉 *Great news {item['patient_name']}!*\n\n"
        f"A slot opened up at Dr. Kurian's Clinic. You have been confirmed from the waitlist:\n"
        f"📅 Date: *{item['preferred_date']}*\n"
        f"⏰ Time: *{chosen_time}*\n"
        f"📋 Appointment ID: *#{new_appt_id}*\n\n"
        f"Reply *'status'* anytime to view your booking or *'cancel {new_appt_id}'* if no longer needed."
    )
    send_whatsapp_message(item["patient_phone"], notify_body)

    msg = (
        f"🎉 *Patient Promoted from Waitlist!*\n\n"
        f"• *Name*: {item['patient_name']}\n"
        f"• *Phone*: {item['patient_phone']}\n"
        f"• *Slot*: {item['preferred_date']} at {chosen_time}\n"
        f"• *New Appt ID*: #{new_appt_id}"
    )
    return True, msg, item

def cancel_patient_appointment(phone: str, appt_id: int = None) -> str:
    appts = get_patient_active_appointments(phone)
    if not appts:
        return "You have no active upcoming appointments to cancel."

    if appt_id:
        target = next((a for a in appts if a["id"] == appt_id), None)
        if not target:
            return f"No active appointment found with ID #{appt_id}."
    elif len(appts) > 1:
        options = "\n".join([f"• ID #{a['id']}: {a['appointment_time']}" for a in appts])
        return (
            f"You have multiple upcoming appointments:\n{options}\n\n"
            f"Please reply with the exact ID to cancel, e.g.: *'cancel {appts[0]['id']}'*"
        )
    else:
        target = appts[0]

    parts = target["appointment_time"].split()
    target_date = parts[0]
    slot_time = parts[1][:5] if len(parts) > 1 else None

    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE appointments SET status = 'cancelled' WHERE id = ?", (target["id"],))
    conn.commit()
    conn.close()

    promoted_note = ""
    candidate = get_matching_waitlisted_candidate(target_date, slot_time)
    if candidate:
        ok, _, promoted_item = promote_waitlist_candidate(candidate["id"], slot_time)
        if ok:
            promoted_note = (
                f"\n\n⚡ *Waitlist Auto-Backfill:* Freed slot automatically confirmed for *{promoted_item['patient_name']}* and alert sent."
            )

    return f"❌ Your appointment #{target['id']} on *{target['appointment_time']}* has been cancelled.{promoted_note}"
