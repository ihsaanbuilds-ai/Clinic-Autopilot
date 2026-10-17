"""Admin authentication for clinic-operator endpoints.

The dashboard, billing and manual-dispatch endpoints must not be world-readable.
Operators authenticate with the ``X-Admin-Secret`` header (or ``?admin_token=``
query parameter for convenience). Authentication fails closed: if no secret is
configured the endpoints refuse to serve rather than silently allowing access.
"""
import os
import secrets

from dotenv import load_dotenv
from fastapi import Header, HTTPException, status

load_dotenv()

ADMIN_SECRET = os.getenv("ADMIN_SECRET", "").strip()


def verify_admin(x_admin_secret: str = Header(None), admin_token: str = None) -> bool:
    if not ADMIN_SECRET:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin authentication is not configured. Set the ADMIN_SECRET environment variable.",
        )
    token = x_admin_secret or admin_token
    if not token or not secrets.compare_digest(str(token), ADMIN_SECRET):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: valid X-Admin-Secret header required.",
        )
    return True
