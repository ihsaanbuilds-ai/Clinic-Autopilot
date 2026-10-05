import sys
import requests
import json
import sqlite3
import os

BASE_URL = "http://localhost:8000"
DB_PATH = os.path.join(os.path.dirname(__file__), "clinic.db")

PASSED = 0
FAILED = 0

def log_test(name, success, detail=""):
    global PASSED, FAILED
    if success:
        PASSED += 1
        print(f"  ✅ PASS: {name}")
    else:
        FAILED += 1
        print(f"  ❌ FAIL: {name} | Reason: {detail}")

print("=========================================================")
print("  RECEPTIONIST AI: COMPREHENSIVE PRODUCTION TEST RUNNER")
print("=========================================================\n")

# 1. License Check
try:
    res = requests.get(f"{BASE_URL}/api/dashboard/subscription")
    data = res.json()
    log_test("Subscription Engine", data.get("active") is True and data.get("days_remaining") >= 0, str(data))
except Exception as e:
    log_test("Subscription Engine", False, str(e))

# 2. Inbound Voice AI Check
try:
    res = requests.post(f"{BASE_URL}/api/voice/incoming")
    log_test("Voice Call Inbound Greeting (TwiML)", "Polly.Aditi" in res.text and "<Gather" in res.text)
    
    speech_res = requests.post(
        f"{BASE_URL}/api/voice/process-speech",
        data={"SpeechResult": "What is the consultation fee?", "From": "+919988776655"}
    )
    log_test("Voice AI Speech Recognition FAQ", "500 rupees" in speech_res.text)
except Exception as e:
    log_test("Voice AI Check", False, str(e))

# 3. WhatsApp NLP Slot Inquiries
try:
    res = requests.post(
        f"{BASE_URL}/api/doctor/whatsapp-webhook",
        data={"Body": "What are the available slots tomorrow?", "From": "whatsapp:+919123456780", "ProfileName": "Tester"}
    )
    log_test("WhatsApp Slot Inquiries", "Available Slots" in res.text or "All slots are currently booked" in res.text or "unavailable" in res.text)
except Exception as e:
    log_test("WhatsApp Slot Inquiries", False, str(e))

# 4. Multi-Turn Session Memory
try:
    # Turn 1: Date inquiry
    requests.post(
        f"{BASE_URL}/api/doctor/whatsapp-webhook",
        data={"Body": "Can I book an appointment tomorrow?", "From": "whatsapp:+919123456781", "ProfileName": "MemoryTest"}
    )
    # Turn 2: Provide only time
    res = requests.post(
        f"{BASE_URL}/api/doctor/whatsapp-webhook",
        data={"Body": "10:00 am", "From": "whatsapp:+919123456781", "ProfileName": "MemoryTest"}
    )
    log_test("Multi-Turn Session Memory (Implicit Date Carry-over)", "Appointment Confirmed" in res.text or "waitlist" in res.text.lower() or "unavailable" in res.text)
except Exception as e:
    log_test("Multi-Turn Session Memory", False, str(e))

# 5. Staff Escalation & AI Silence
try:
    res1 = requests.post(
        f"{BASE_URL}/api/doctor/whatsapp-webhook",
        data={"Body": "I want to speak with a human receptionist", "From": "whatsapp:+919123456782", "ProfileName": "EscalateUser"}
    )
    has_escalated_msg = "paused the AI" in res1.text
    
    res2 = requests.post(
        f"{BASE_URL}/api/doctor/whatsapp-webhook",
        data={"Body": "Are you there?", "From": "whatsapp:+919123456782", "ProfileName": "EscalateUser"}
    )
    is_silenced = "assigned to front-desk staff" in res2.text
    
    # Resume
    res3 = requests.post(
        f"{BASE_URL}/api/doctor/whatsapp-webhook",
        data={"Body": "resume bot", "From": "whatsapp:+919123456782", "ProfileName": "EscalateUser"}
    )
    is_resumed = "Receptionist AI reactivated" in res3.text

    log_test("Staff Escalation Protocol (Trigger, Silence, Resume)", has_escalated_msg and is_silenced and is_resumed)
except Exception as e:
    log_test("Staff Escalation Protocol", False, str(e))

# 6. Post-Consultation Google Review Automation
try:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM appointments WHERE appointment_time = '2026-10-03 11:00:00'")
    c.execute("DELETE FROM post_consultations WHERE patient_phone LIKE '%9123456783%'")
    c.execute("""
        INSERT OR REPLACE INTO appointments (patient_name, patient_phone, appointment_time, status)
        VALUES ('TestReviewPatient', '919123456783', '2026-10-03 11:00:00', 'confirmed')
    """)
    conn.commit()
    conn.close()

    # Trigger followups for 2 days ago
    requests.post(f"{BASE_URL}/api/doctor/trigger-followups?days_ago=2")

    # Patient replies with 5
    res = requests.post(
        f"{BASE_URL}/api/doctor/whatsapp-webhook",
        data={"Body": "5", "From": "whatsapp:+919123456783", "ProfileName": "TestReviewPatient"}
    )
    log_test("Post-Consultation Recovery & Google Review Loop", "Google review" in res.text or "g.page" in res.text)
except Exception as e:
    log_test("Post-Consultation Review Automation", False, str(e))

# 7. Dashboard Metrics API
try:
    res = requests.get(f"{BASE_URL}/api/dashboard/metrics")
    data = res.json()
    has_keys = "summary" in data and "appointments" in data and "waitlist" in data and "escalations" in data
    log_test("Dashboard Metrics Endpoint Integrity", has_keys)
except Exception as e:
    log_test("Dashboard Metrics Endpoint Integrity", False, str(e))

print("\n---------------------------------------------------------")
print(f"RESULTS: {PASSED} Passed | {FAILED} Failed")
print("---------------------------------------------------------")
if FAILED == 0:
    print("🏆 ALL SYSTEMS OPERATIONAL. Zero regressions detected.")
    sys.exit(0)
else:
    print("⚠ Debugging required on failed items above.")
    sys.exit(1)
