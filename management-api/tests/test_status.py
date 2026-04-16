from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


def test_get_status_requires_auth(client: TestClient) -> None:
    resp = client.get("/api/status")
    assert resp.status_code == 401


def test_get_status_with_bearer(client: TestClient, auth_headers: dict) -> None:
    mock_active = AsyncMock(return_value=True)
    mock_sub = AsyncMock(return_value="running")
    mock_since = AsyncMock(return_value="2024-01-01T00:00:00Z")

    with (
        patch("hermes_mgmt.routes.status.is_active", mock_active),
        patch("hermes_mgmt.routes.status.sub_state", mock_sub),
        patch("hermes_mgmt.routes.status.active_since", mock_since),
    ):
        resp = client.get("/api/status", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "services" in body["data"]
    services = body["data"]["services"]
    assert isinstance(services, list)
    assert len(services) > 0
    for svc in services:
        assert "name" in svc
        assert "active" in svc
        assert "sub_state" in svc
        assert "since" in svc


def test_get_status_inactive_service(client: TestClient, auth_headers: dict) -> None:
    mock_active = AsyncMock(return_value=False)
    mock_sub = AsyncMock(return_value="dead")
    mock_since = AsyncMock(return_value="unknown")

    with (
        patch("hermes_mgmt.routes.status.is_active", mock_active),
        patch("hermes_mgmt.routes.status.sub_state", mock_sub),
        patch("hermes_mgmt.routes.status.active_since", mock_since),
    ):
        resp = client.get("/api/status", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    for svc in body["data"]["services"]:
        assert svc["active"] is False
        assert svc["sub_state"] == "dead"


def test_get_system_requires_auth(client: TestClient) -> None:
    resp = client.get("/api/system")
    assert resp.status_code == 401


def test_get_system_with_auth(client: TestClient, auth_headers: dict) -> None:
    resp = client.get("/api/system", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    data = body["data"]
    assert "cpu_percent" in data
    assert "memory" in data
    assert "disk" in data
    assert "uptime_seconds" in data
    assert "load_avg" in data
    assert isinstance(data["load_avg"], list)
    assert len(data["load_avg"]) == 3


def test_get_version_requires_auth(client: TestClient) -> None:
    resp = client.get("/api/version")
    assert resp.status_code == 401


def test_get_version_with_auth(client: TestClient, auth_headers: dict) -> None:
    mock_result = AsyncMock(return_value=type("R", (), {"stdout": "0.9.0\n", "exit_code": 0})())
    with patch("hermes_mgmt.routes.status.run_hermes", mock_result):
        resp = client.get("/api/version", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["data"]["version"] == "0.9.0"


def test_get_domain_requires_auth(client: TestClient) -> None:
    resp = client.get("/api/domain")
    assert resp.status_code == 401


def test_get_domain_with_auth(client: TestClient, auth_headers: dict) -> None:
    resp = client.get("/api/domain", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "domain" in body["data"]


def test_get_info_requires_auth(client: TestClient) -> None:
    resp = client.get("/api/info")
    assert resp.status_code == 401


def test_get_info_with_auth(client: TestClient, auth_headers: dict) -> None:
    mock_result = type("R", (), {"stdout": "1.0.0\n", "exit_code": 0})()
    with patch(
        "hermes_mgmt.routes.status.run_hermes", AsyncMock(return_value=mock_result)
    ):
        resp = client.get("/api/info", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    data = body["data"]
    assert "hermes_version" in data
    assert "mgmt_version" in data
    assert "domain" in data
    assert "ip" in data
    assert "dashboard_url" in data
