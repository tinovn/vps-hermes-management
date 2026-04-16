from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time

import bcrypt as _bcrypt_lib

logger = logging.getLogger(__name__)

_BCRYPT_ROUNDS = 12


def hash_password(password: str) -> str:
    salt = _bcrypt_lib.gensalt(rounds=_BCRYPT_ROUNDS)
    return _bcrypt_lib.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _bcrypt_lib.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def _b64url_encode(data: str) -> str:
    return base64.urlsafe_b64encode(data.encode()).decode().rstrip("=")


def _b64url_decode(data: str) -> str:
    # Re-add padding
    padding = 4 - len(data) % 4
    if padding != 4:
        data += "=" * padding
    return base64.urlsafe_b64decode(data).decode()


def create_session_token(
    username: str, secret: str, ttl_seconds: int = 86400 * 7
) -> tuple[str, int]:
    expiry = int(time.time()) + ttl_seconds
    payload = _b64url_encode(f"{username}|{expiry}")
    sig = hmac.new(key=secret.encode(), msg=payload.encode(), digestmod=hashlib.sha256).hexdigest()
    sig_b64 = _b64url_encode(sig)
    token = f"{payload}.{sig_b64}"
    return token, expiry


def verify_session_token(token: str, secret: str) -> str | None:
    try:
        parts = token.split(".")
        if len(parts) != 2:
            return None
        payload, sig_b64 = parts
        expected_sig = hmac.new(
            key=secret.encode(), msg=payload.encode(), digestmod=hashlib.sha256
        ).hexdigest()
        expected_sig_b64 = _b64url_encode(expected_sig)
        if not hmac.compare_digest(sig_b64, expected_sig_b64):
            return None
        decoded = _b64url_decode(payload)
        username, expiry_str = decoded.rsplit("|", 1)
        if int(time.time()) > int(expiry_str):
            return None
        return username
    except Exception:
        return None


def bearer_matches(authorization_header: str | None, expected_key: str) -> bool:
    if not authorization_header:
        return False
    parts = authorization_header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return False
    provided = parts[1].strip()
    return hmac.compare_digest(provided, expected_key)
