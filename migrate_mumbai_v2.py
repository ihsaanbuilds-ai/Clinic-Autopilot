"""Migration: Mumbai multi-doctor template (v2).

Fixes schema drift between the live database and init_db.py and enables real
multi-doctor scheduling.

Changes
-------
1. ``appointments`` — drops the global UNIQUE(appointment_time) constraint that
   made it impossible for two doctors to share a time slot; enforces
   UNIQUE(doctor_id, appointment_time) instead.
2. ``blocked_dates`` — adds ``doctor_id`` so leave blocks one doctor, not the
   whole clinic.
3. ``staff_escalations`` — adds ``resolved_at`` for a full audit trail.
4. ``post_consultations`` — standardises on ``feedback_score``.
5. Rebrands clinic/doctor identity to the generalised Mumbai template.

The migration is idempotent: safe to run repeatedly.
"""
import os
import sqlite3
from datetime import datetime, timedelta

DB_PATH = os.getenv("CLINIC_DB_PATH") or os.path.join(
    os.path.dirname(__file__), "clinic.db"
)


def _table_columns(c: sqlite3.Cursor, table: str) -> list[str]:
    c.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in c.fetchall()]


def _appointments_has_global_unique_time(c: sqlite3.Cursor) -> bool:
    """True when appointment_time is globally UNIQUE (the old, blocking schema)."""
    c.execute("PRAGMA index_list(appointments)")
    for idx in c.fetchall():
        idx_name, unique = idx[1], idx[2]
        if not unique:
            continue
        c.execute(f"PRAGMA index_info({idx_name})")
        cols = [r[2] for r in c.fetchall()]
        if cols == ["appointment_time"]:
            return True
    return False


def _rebuild_appointments(c: sqlite3.Cursor):
    c.execute("""
        CREATE TABLE appointments_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_name TEXT NOT NULL,
            patient_phone TEXT NOT NULL,
            doctor_name TEXT,
            appointment_time TIMESTAMP NOT NULL,
            status TEXT DEFAULT 'confirmed',
            reminder_24h_sent INTEGER DEFAULT 0,
            reminder_2h_sent INTEGER DEFAULT 0,
            opt_out INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            doctor_id INTEGER DEFAULT 1,
            UNIQUE(doctor_id, appointment_time)
        )
    """)
    c.execute("""
        INSERT INTO appointments_new
            (id, patient_name, patient_phone, doctor_name, appointment_time,
             status, reminder_24h_sent, reminder_2h_sent, opt_out, created_at, doctor_id)
        SELECT id, patient_name, patient_phone, doctor_name, appointment_time,
               status,
               COALESCE(reminder_24h_sent, 0), COALESCE(reminder_2h_sent, 0),
               COALESCE(opt_out, 0), created_at, COALESCE(doctor_id, 1)
        FROM appointments
    """)
    c.execute("DROP TABLE appointments")
    c.execute("ALTER TABLE appointments_new RENAME TO appointments")
    c.execute("CREATE INDEX IF NOT EXISTS ix_appointments_phone ON appointments(patient_phone)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_appointments_time ON appointments(appointment_time)")


def _rebuild_blocked_dates(c: sqlite3.Cursor):
    cols = _table_columns(c, "blocked_dates")
    if "doctor_id" in cols:
        return
    c.execute("""
        CREATE TABLE blocked_dates_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doctor_id INTEGER DEFAULT 1,
            blocked_date DATE NOT NULL,
            reason TEXT DEFAULT 'Doctor on leave',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(doctor_id, blocked_date)
        )
    """)
    c.execute("""
        INSERT INTO blocked_dates_new (id, doctor_id, blocked_date, reason)
        SELECT id, 1, blocked_date, reason FROM blocked_dates
    """)
    c.execute("DROP TABLE blocked_dates")
    c.execute("ALTER TABLE blocked_dates_new RENAME TO blocked_dates")


def _add_column_if_missing(c: sqlite3.Cursor, table: str, column: str, ddl: str):
    if column not in _table_columns(c, table):
        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _reconcile_post_consultations(c: sqlite3.Cursor):
    cols = _table_columns(c, "post_consultations")
    if "feedback_score" not in cols:
        c.execute("ALTER TABLE post_consultations ADD COLUMN feedback_score INTEGER")
    # Carry across any legacy `rating` values.
    if "rating" in cols:
        c.execute("""
            UPDATE post_consultations
            SET feedback_score = rating
            WHERE feedback_score IS NULL AND rating IS NOT NULL
        """)


def _reconcile_patient_sessions(c: sqlite3.Cursor):
    cols = _table_columns(c, "patient_sessions")
    if "phone" not in cols and "patient_phone" in cols:
        c.execute("ALTER TABLE patient_sessions RENAME COLUMN patient_phone TO phone")
    if "doctor_id" not in _table_columns(c, "patient_sessions"):
        c.execute("ALTER TABLE patient_sessions ADD COLUMN doctor_id INTEGER DEFAULT 1")


def _seed_tenant(c: sqlite3.Cursor):
    c.execute("""
        CREATE TABLE IF NOT EXISTS clinics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            phone_number_id TEXT
        )
    """)
    c.execute("DELETE FROM clinics")
    c.execute(
        "INSERT INTO clinics (id, name, phone_number_id) VALUES (1, ?, ?)",
        ("CityCare Multispeciality Clinic", None),
    )

    # Rebrand the practitioner roster while keeping doctor 1's WhatsApp phone as
    # the live bridge owner so doctor-command routing keeps working.
    c.execute("""
        UPDATE doctors SET name = 'Dr. Rohan Mehta', specialty = 'General Medicine',
                           consultation_fee = 600
        WHERE id = 1
    """)
    c.execute("""
        UPDATE doctors SET name = 'Dr. Priya Nair', specialty = 'Dental Surgeon',
                           consultation_fee = 800
        WHERE id = 2
    """)
    c.execute("""
        INSERT OR IGNORE INTO doctors (id, name, specialty, phone, consultation_fee, active)
        VALUES (1, 'Dr. Rohan Mehta', 'General Medicine', '918928740867', 600, 1)
    """)

    # Keep the licence row aligned with the new slug.
    c.execute("""
        UPDATE clinic_license SET clinic_slug = 'citycare-clinic'
        WHERE clinic_slug IN ('dr-kurian-clinic', 'citycare-clinic')
    """)


def run():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    c = conn.cursor()
    try:
        c.execute("PRAGMA foreign_keys = OFF")
        if _appointments_has_global_unique_time(c):
            _rebuild_appointments(c)
        _add_column_if_missing(c, "appointments", "doctor_name", "TEXT")
        _rebuild_blocked_dates(c)
        _add_column_if_missing(c, "staff_escalations", "resolved_at", "TIMESTAMP")
        _add_column_if_missing(c, "staff_escalations", "created_at", "TIMESTAMP")
        _reconcile_post_consultations(c)
        _reconcile_patient_sessions(c)
        _seed_tenant(c)
        conn.commit()
        print("✅ Migration v2 (Mumbai multi-doctor template) applied.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    run()
