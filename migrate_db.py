import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "clinic.db")

def migrate():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Create partial unique index so two confirmed appointments cannot share the same datetime
    c.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_confirmed_slot 
        ON appointments (appointment_time) 
        WHERE status = 'confirmed';
    """)
    
    conn.commit()
    conn.close()
    print("Database index migration applied successfully.")

if __name__ == "__main__":
    migrate()
