"""Verifies "Sign in with Google" ID tokens for the room recovery/link
feature (see main.py's /api/auth/google). Kept in its own tiny module,
separate from main.py, so tests can monkeypatch verify_id_token instead
of needing a real Google-signed token or network access — same pattern
as storage.upload_photo being monkeypatched in tests for S3.
"""
import os

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token


def verify_id_token(token: str) -> dict:
    """Returns the token's claims (including "sub", the stable per-account
    id, and "email") if it's a valid, unexpired ID token issued for our
    GOOGLE_CLIENT_ID. Raises ValueError otherwise."""
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    return google_id_token.verify_oauth2_token(token, google_requests.Request(), client_id)
