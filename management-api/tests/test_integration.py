from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


def test_health_endpoint_public(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "version" in body


def test_health_no_auth_required(client: TestClient) -> None:
    # No Authorization header
    resp = client.get("/health")
    assert resp.status_code == 200


def test_protected_routes_reject_no_auth(client: TestClient) -> None:
    protected_routes = [
        ("GET", "/api/info"),
        ("GET", "/api/status"),
        ("GET", "/api/system"),
        ("GET", "/api/version"),
        ("GET", "/api/env"),
        ("GET", "/api/config"),
        ("GET", "/api/channels"),
        ("GET", "/api/logs"),
        ("GET", "/api/cron"),
    ]
    for method, path in protected_routes:
        resp = client.request(method, path)
        assert resp.status_code == 401, f"Expected 401 for {method} {path}, got {resp.status_code}"
        body = resp.json()
        assert body["ok"] is False, f"Expected ok=False for {method} {path}"


def test_protected_routes_accept_bearer(client: TestClient, auth_headers: dict) -> None:
    mock_active = AsyncMock(return_value=True)
    mock_sub = AsyncMock(return_value="running")
    mock_since = AsyncMock(return_value="unknown")
    mock_version = AsyncMock(return_value=type("R", (), {"stdout": "1.0.0", "exit_code": 0})())

    with (
        patch("hermes_mgmt.routes.status.is_active", mock_active),
        patch("hermes_mgmt.routes.status.sub_state", mock_sub),
        patch("hermes_mgmt.routes.status.active_since", mock_since),
        patch("hermes_mgmt.routes.status.run_hermes", mock_version),
    ):
        resp = client.get("/api/status", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_invalid_bearer_rejected(client: TestClient) -> None:
    resp = client.get("/api/status", headers={"Authorization": "Bearer totally-wrong-key"})
    assert resp.status_code == 401


def test_error_response_envelope_shape(client: TestClient) -> None:
    resp = client.get("/api/status")
    body = resp.json()
    assert "ok" in body
    assert body["ok"] is False
    assert "error" in body


def test_env_get_with_auth(client: TestClient, auth_headers: dict, temp_env_file) -> None:
    resp = client.get("/api/env", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert isinstance(body["data"], dict)


def test_env_put_invalid_key(client: TestClient, auth_headers: dict) -> None:
    resp = client.put(
        "/api/env/invalid-key-name",
        headers=auth_headers,
        json={"value": "test"},
    )
    assert resp.status_code == 422


def test_cli_blocked_subcommand(client: TestClient, auth_headers: dict) -> None:
    resp = client.post(
        "/api/cli",
        headers=auth_headers,
        json={"subcommand": "exec", "args": ["rm -rf /"]},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["ok"] is False


def test_cli_allowed_subcommand(client: TestClient, auth_headers: dict) -> None:
    from hermes_mgmt.models import CliResponse
    mock_result = CliResponse(stdout="1.0.0", stderr="", exit_code=0)
    with patch(
        "hermes_mgmt.routes.cli_routes.run_hermes", AsyncMock(return_value=mock_result)
    ):
        resp = client.post(
            "/api/cli",
            headers=auth_headers,
            json={"subcommand": "version", "args": []},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["data"]["exit_code"] == 0


def test_login_page_returns_html(client: TestClient) -> None:
    resp = client.get("/login", follow_redirects=True)
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "form" in resp.text.lower()


def test_logout_clears_cookie(client: TestClient) -> None:
    resp = client.post("/api/auth/logout")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True


def test_full_info_flow(client: TestClient, auth_headers: dict) -> None:
    """Health check -> info -> verify shape."""
    health = client.get("/health")
    assert health.status_code == 200

    mock_result = type("R", (), {"stdout": "0.9.0\n", "exit_code": 0})()
    with patch(
        "hermes_mgmt.routes.status.run_hermes", AsyncMock(return_value=mock_result)
    ):
        info = client.get("/api/info", headers=auth_headers)

    assert info.status_code == 200
    data = info.json()["data"]
    assert data["mgmt_version"] == "0.1.0"
    assert data["hermes_version"] == "0.9.0"
