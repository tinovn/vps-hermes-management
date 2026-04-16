from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from hermes_mgmt.auth import (
    bearer_matches,
    create_session_token,
    hash_password,
    verify_password,
    verify_session_token,
)
from hermes_mgmt.deps import RateLimiter


# --- password hashing ---

def test_hash_password_produces_bcrypt_string() -> None:
    h = hash_password("hunter2")
    assert h.startswith("$2")


def test_verify_password_correct() -> None:
    h = hash_password("correct-horse")
    assert verify_password("correct-horse", h) is True


def test_verify_password_wrong() -> None:
    h = hash_password("correct-horse")
    assert verify_password("wrong-password", h) is False


def test_verify_password_bad_hash_returns_false() -> None:
    assert verify_password("password", "not-a-valid-hash") is False


# --- session tokens ---

def test_session_token_roundtrip() -> None:
    token, expiry = create_session_token("alice", "secret", ttl_seconds=3600)
    assert isinstance(token, str)
    assert "." in token
    username = verify_session_token(token, "secret")
    assert username == "alice"


def test_session_token_wrong_secret_returns_none() -> None:
    token, _ = create_session_token("alice", "secret")
    assert verify_session_token(token, "wrong-secret") is None


def test_session_token_expired_returns_none() -> None:
    token, _ = create_session_token("alice", "secret", ttl_seconds=-1)
    assert verify_session_token(token, "secret") is None


def test_session_token_malformed_returns_none() -> None:
    assert verify_session_token("notavalidtoken", "secret") is None
    assert verify_session_token("", "secret") is None
    assert verify_session_token("a.b.c", "secret") is None


# --- bearer_matches ---

def test_bearer_matches_positive() -> None:
    assert bearer_matches("Bearer my-secret-key", "my-secret-key") is True


def test_bearer_matches_negative_wrong_key() -> None:
    assert bearer_matches("Bearer wrong-key", "my-secret-key") is False


def test_bearer_matches_missing_header() -> None:
    assert bearer_matches(None, "my-secret-key") is False


def test_bearer_matches_no_bearer_prefix() -> None:
    assert bearer_matches("my-secret-key", "my-secret-key") is False


def test_bearer_matches_case_insensitive_scheme() -> None:
    assert bearer_matches("bearer my-secret-key", "my-secret-key") is True


# --- rate limiter ---

class MockRequest:
    def __init__(self, ip: str = "1.2.3.4") -> None:
        self.headers: dict = {}
        self.client = MagicMock()
        self.client.host = ip


def test_rate_limiter_blocks_after_max_failures() -> None:
    limiter = RateLimiter(max_failures=10, window_seconds=900)
    req = MockRequest("10.0.0.1")
    for _ in range(10):
        limiter.record_failure(req)
    assert limiter.is_blocked(req) is True


def test_rate_limiter_not_blocked_before_max() -> None:
    limiter = RateLimiter(max_failures=10, window_seconds=900)
    req = MockRequest("10.0.0.2")
    for _ in range(9):
        limiter.record_failure(req)
    assert limiter.is_blocked(req) is False


def test_rate_limiter_clear_unblocks() -> None:
    limiter = RateLimiter(max_failures=10, window_seconds=900)
    req = MockRequest("10.0.0.3")
    for _ in range(10):
        limiter.record_failure(req)
    assert limiter.is_blocked(req) is True
    limiter.clear(req)
    assert limiter.is_blocked(req) is False


# --- HTTP login endpoint tests ---

def test_login_valid(client: TestClient, temp_env_file: Path, test_settings) -> None:
    from hermes_mgmt.auth import hash_password
    from hermes_mgmt.env_file import set_env

    set_env(temp_env_file, "HERMES_LOGIN_USER", "admin")
    set_env(temp_env_file, "HERMES_LOGIN_HASH", hash_password("password123"))

    # Patch read_env to read from temp_env_file
    with patch("hermes_mgmt.routes.auth_routes.read_env") as mock_read:
        mock_read.return_value = {
            "HERMES_LOGIN_USER": "admin",
            "HERMES_LOGIN_HASH": hash_password("password123"),
        }
        resp = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "password123"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "token" in body["data"]
    assert "expires_at" in body["data"]


def test_login_invalid_password(client: TestClient) -> None:
    with patch("hermes_mgmt.routes.auth_routes.read_env") as mock_read:
        from hermes_mgmt.auth import hash_password
        mock_read.return_value = {
            "HERMES_LOGIN_USER": "admin",
            "HERMES_LOGIN_HASH": hash_password("correct-password"),
        }
        resp = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrong-password"},
        )

    assert resp.status_code == 401
    body = resp.json()
    assert body["ok"] is False


def test_login_rate_limit(client: TestClient) -> None:
    from hermes_mgmt.deps import _rate_limiter
    from unittest.mock import patch as _patch

    with _patch("hermes_mgmt.routes.auth_routes.read_env") as mock_read:
        from hermes_mgmt.auth import hash_password
        mock_read.return_value = {
            "HERMES_LOGIN_USER": "admin",
            "HERMES_LOGIN_HASH": hash_password("right"),
        }
        # Force rate limiter to report blocked
        with _patch.object(_rate_limiter, "is_blocked", return_value=True):
            resp = client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "wrong"},
            )

    assert resp.status_code == 429
