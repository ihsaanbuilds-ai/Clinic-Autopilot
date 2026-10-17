import os
import sqlite3
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./clinic.db")

# Canonical SQLite path for the raw-sqlite data-access helpers used across the
# app. Overridable via env so tests can point at a throwaway database.
DB_PATH = os.getenv("CLINIC_DB_PATH") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "clinic.db"
)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_sqlite_connection(timeout: float = 5.0) -> sqlite3.Connection:
    """Open a row-dict SQLite connection to the canonical clinic database."""
    conn = sqlite3.connect(DB_PATH, timeout=timeout)
    conn.row_factory = sqlite3.Row
    return conn
