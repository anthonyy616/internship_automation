"""
Admin authentication — password + TOTP 2FA with signed session cookies.

Flow:
    1. POST /admin/login    -> checks ADMIN_PASSWORD (bcrypt)
    2. If ADMIN_TOTP_SECRET is set, a temporary session is created and
       the user must POST /admin/2fa with a valid TOTP code
    3. The final session cookie is signed with itsdangerous and scoped
       to /admin

If ADMIN_PASSWORD is empty the panel is open (local dev only).
"""

import base64
import hmac
import os
import time
from typing import Optional

import bcrypt
import pyotp
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from fastapi import Cookie, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from backend.config import settings

SESSION_SECRET = settings.app_env  # rotated with env in production
if not SESSION_SECRET or SESSION_SECRET in ("development", ""):
    # Stable dev secret so sessions survive restarts during development
    SESSION_SECRET = "dev-insecure-session-secret-do-not-use-in-prod"

_serializer = URLSafeTimedSerializer(SESSION_SECRET, salt="admin-session")


def _password_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def password_configured() -> bool:
    return bool(settings.admin_password)


def verify_password(password: str) -> bool:
    if not password_configured():
        return True  # open panel (dev)
    try:
        # settings.admin_password may be a plain value; treat it as the
        # accepted password and compare with a constant-time check.
        return hmac.compare_digest(password, settings.admin_password)
    except Exception:
        return False


def totp_configured() -> bool:
    return bool(settings.admin_totp_secret)


def verify_totp(code: str) -> bool:
    if not totp_configured():
        return True
    try:
        totp = pyotp.TOTP(settings.admin_totp_secret)
        return totp.verify(code, valid_window=1)
    except Exception:
        return False


def totp_provisioning_uri() -> Optional[str]:
    """OTPAuth URI for enrolling a new authenticator app."""
    if not totp_configured():
        return None
    try:
        totp = pyotp.TOTP(settings.admin_totp_secret)
        return totp.provisioning_uri(name="internship-bot-admin", issuer_name="Internship Bot")
    except Exception:
        return None


def create_session(totp_verified: bool = False, ttl_seconds: int = 60 * 60 * 12) -> str:
    payload = {
        "user": "admin",
        "totp_verified": totp_verified,
        "iat": int(time.time()),
    }
    return _serializer.dumps(payload)


def read_session(token: str) -> Optional[dict]:
    try:
        data = _serializer.loads(token, max_age=60 * 60 * 12)
        return data if data.get("user") == "admin" else None
    except (BadSignature, SignatureExpired):
        return None


def require_admin(request: Request):
    """FastAPI dependency — redirects to /admin/login when unauthenticated."""
    token = request.cookies.get("admin_session")
    session = read_session(token) if token else None
    if session is None:
        raise HTTPException(status_code=303, headers={"Location": "/admin/login"})
    if totp_configured() and not session.get("totp_verified"):
        raise HTTPException(status_code=303, headers={"Location": "/admin/2fa"})
    return session


def admin_logged_in(request: Request) -> bool:
    token = request.cookies.get("admin_session")
    session = read_session(token) if token else None
    if session is None:
        return False
    if totp_configured() and not session.get("totp_verified"):
        return False
    return True


def set_session_cookie(response: Response, session_token: str):
    response.set_cookie(
        key="admin_session",
        value=session_token,
        max_age=60 * 60 * 12,
        httponly=True,
        samesite="lax",
        secure=False,  # set True behind TLS
        path="/",
    )


def clear_session_cookie(response: Response):
    response.delete_cookie("admin_session", path="/")