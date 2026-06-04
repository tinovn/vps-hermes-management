from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from hermes_mgmt.config import Settings
from hermes_mgmt.env_file import read_env


def _fake_response(status_code: int, json_body: dict | None = None, content: bytes = b"") -> httpx.Response:
    if json_body is not None:
        return httpx.Response(status_code, json=json_body)
    return httpx.Response(status_code, content=content)


# ─── status ────────────────────────────────────────────────────────────────


def test_zalo_status_requires_auth(client: TestClient) -> None:
    assert client.get("/api/zalo/status").status_code == 401


def test_zalo_status_sidecar_down_reports_disconnected(
    client: TestClient, auth_headers: dict
) -> None:
    mock_get = AsyncMock(side_effect=httpx.ConnectError("refused"))
    with patch("hermes_mgmt.routes.zalo._sidecar_get", mock_get):
        resp = client.get("/api/zalo/status", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["data"]["status"] == "disconnected"
    assert body["data"]["sidecar"] is False


def test_zalo_status_pending(client: TestClient, auth_headers: dict) -> None:
    mock_get = AsyncMock(
        return_value=_fake_response(200, {"status": "pending", "uid": None, "name": None})
    )
    with patch("hermes_mgmt.routes.zalo._sidecar_get", mock_get):
        resp = client.get("/api/zalo/status", headers=auth_headers)
    assert resp.json()["data"]["status"] == "pending"


def test_zalo_status_connected_persists_owner_uid(
    client: TestClient, auth_headers: dict, test_settings: Settings
) -> None:
    mock_get = AsyncMock(
        return_value=_fake_response(
            200, {"status": "connected", "uid": "98765", "name": "Sếp"}
        )
    )
    with patch("hermes_mgmt.routes.zalo._sidecar_get", mock_get):
        resp = client.get("/api/zalo/status", headers=auth_headers)

    body = resp.json()
    assert body["data"]["status"] == "connected"
    assert body["data"]["uid"] == "98765"
    # Owner uid auto-persisted into HERMES_HOME/.env (low-tech: no manual UID).
    env = read_env(test_settings.hermes_home / ".env")
    assert env.get("ZALO_PERSONAL_OWNER_UID") == "98765"


def test_zalo_status_connected_respects_existing_uid(
    client: TestClient, auth_headers: dict, test_settings: Settings
) -> None:
    # Pre-seed a different owner uid; connect must NOT overwrite it.
    from hermes_mgmt.env_file import set_env

    set_env(test_settings.hermes_home / ".env", "ZALO_PERSONAL_OWNER_UID", "111")
    mock_get = AsyncMock(
        return_value=_fake_response(200, {"status": "connected", "uid": "222", "name": "X"})
    )
    with patch("hermes_mgmt.routes.zalo._sidecar_get", mock_get):
        client.get("/api/zalo/status", headers=auth_headers)
    env = read_env(test_settings.hermes_home / ".env")
    assert env.get("ZALO_PERSONAL_OWNER_UID") == "111"


# ─── connect ───────────────────────────────────────────────────────────────


def test_zalo_connect_pending(client: TestClient, auth_headers: dict) -> None:
    mock_post = AsyncMock(
        return_value=_fake_response(200, {"status": "pending", "qr_url": "/qr.png"})
    )
    with patch("hermes_mgmt.routes.zalo._sidecar_post", mock_post):
        resp = client.post("/api/zalo/connect", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["status"] == "pending"
    assert body["data"]["qr_url"] == "/api/zalo/qr"


def test_zalo_connect_already_connected(
    client: TestClient, auth_headers: dict, test_settings: Settings
) -> None:
    mock_post = AsyncMock(
        return_value=_fake_response(200, {"status": "already_connected", "uid": "555"})
    )
    with patch("hermes_mgmt.routes.zalo._sidecar_post", mock_post):
        resp = client.post("/api/zalo/connect", headers=auth_headers)
    assert resp.json()["data"]["status"] == "connected"
    env = read_env(test_settings.hermes_home / ".env")
    assert env.get("ZALO_PERSONAL_OWNER_UID") == "555"


def test_zalo_connect_sidecar_down(client: TestClient, auth_headers: dict) -> None:
    mock_post = AsyncMock(side_effect=httpx.ConnectError("refused"))
    with patch("hermes_mgmt.routes.zalo._sidecar_post", mock_post):
        resp = client.post("/api/zalo/connect", headers=auth_headers)
    assert resp.status_code == 503


# ─── qr image ──────────────────────────────────────────────────────────────


def test_zalo_qr_returns_png(client: TestClient, auth_headers: dict) -> None:
    png_bytes = b"\x89PNG\r\n\x1a\nFAKE"
    mock_get = AsyncMock(return_value=_fake_response(200, content=png_bytes))
    with patch("hermes_mgmt.routes.zalo._sidecar_get", mock_get):
        resp = client.get("/api/zalo/qr", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == png_bytes


def test_zalo_qr_not_ready(client: TestClient, auth_headers: dict) -> None:
    mock_get = AsyncMock(return_value=_fake_response(404, {"error": "QR not ready yet"}))
    with patch("hermes_mgmt.routes.zalo._sidecar_get", mock_get):
        resp = client.get("/api/zalo/qr", headers=auth_headers)
    assert resp.status_code == 404


# ─── disconnect ────────────────────────────────────────────────────────────


def test_zalo_disconnect(client: TestClient, auth_headers: dict) -> None:
    mock_post = AsyncMock(return_value=_fake_response(200, {"ok": True}))
    with patch("hermes_mgmt.routes.zalo._sidecar_post", mock_post):
        resp = client.post("/api/zalo/disconnect", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "disconnected"
