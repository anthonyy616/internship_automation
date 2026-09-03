"""
Offline tests for the admin panel — auth flow + basic route smoke tests.

Note: DB-backed admin pages return 500 without a Neon connection; these
tests cover the auth gate and login flow, which need no database.

Usage:
    python -m tests.test_admin
"""

import asyncio
import sys

from fastapi.testclient import TestClient

from backend import auth
from backend.app import app


def test_session_roundtrip():
    token = auth.create_session(totp_verified=True)
    data = auth.read_session(token)
    assert data is not None and data["user"] == "admin"

    # Tampered token must be rejected
    assert auth.read_session(token + "x") is None
    assert auth.read_session("garbage") is None


def test_password_verify():
    # With no ADMIN_PASSWORD set, the panel is open (dev mode)
    assert auth.verify_password("anything") is True
    assert auth.password_configured() is False


def test_totp_verify():
    import pyotp
    secret = pyotp.random_base32()
    from backend.config import settings
    settings.admin_totp_secret = secret
    try:
        assert auth.totp_configured() is True
        assert auth.verify_totp(pyotp.TOTP(secret).now()) is True
        assert auth.verify_totp("000000") is False
    finally:
        settings.admin_totp_secret = ""


def test_login_gate_and_flow():
    with TestClient(app) as client:
        # Admin pages redirect to login when unauthenticated
        r = client.get("/admin", follow_redirects=False)
        assert r.status_code == 303
        assert "/admin/login" in r.headers.get("location", "")

        # Login page renders
        r = client.get("/admin/login")
        assert r.status_code == 200
        assert "Admin Login" in r.text

        # Empty ADMIN_PASSWORD -> any password logs in
        r = client.post("/admin/login", data={"password": "whatever"}, follow_redirects=False)
        assert r.status_code == 303
        assert "admin_session" in r.cookies

        # Authenticated session is accepted
        r = client.get("/admin", follow_redirects=False)
        assert r.status_code in (200, 500)  # 200 renders; 500 only if DB is down


def main():
    tests = [
        test_session_roundtrip,
        test_password_verify,
        test_totp_verify,
        test_login_gate_and_flow,
    ]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"  PASS  {test.__name__}")
        except Exception as e:
            failures += 1
            import traceback
            print(f"  FAIL  {test.__name__}:")
            traceback.print_exc()
    print(f"\n{len(tests) - failures}/{len(tests)} tests passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()