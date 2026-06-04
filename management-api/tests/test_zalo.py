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


def test_zalo_status_connected_reports_bot_uid_not_owner(
    client: TestClient, auth_headers: dict, test_settings: Settings
) -> None:
    # health.uid is the BOT account — must be reported as bot_uid, and status
    # must NOT auto-set the owner from it.
    mock_get = AsyncMock(
        return_value=_fake_response(200, {"status": "connected", "uid": "98765", "name": "Bot"})
    )
    with patch("hermes_mgmt.routes.zalo._sidecar_get", mock_get):
        resp = client.get("/api/zalo/status", headers=auth_headers)
    data = resp.json()["data"]
    assert data["status"] == "connected"
    assert data["bot_uid"] == "98765"
    assert data["owner_set"] is False  # owner NOT set from bot uid
    env = read_env(test_settings.hermes_home / ".env")
    assert "ZALO_PERSONAL_OWNER_UID" not in env or not env.get("ZALO_PERSONAL_OWNER_UID")


def test_zalo_status_owner_set_flag(
    client: TestClient, auth_headers: dict, test_settings: Settings
) -> None:
    from hermes_mgmt.env_file import set_env

    set_env(test_settings.hermes_home / ".env", "ZALO_PERSONAL_OWNER_UID", "111")
    mock_get = AsyncMock(
        return_value=_fake_response(200, {"status": "connected", "uid": "98765", "name": "Bot"})
    )
    with patch("hermes_mgmt.routes.zalo._sidecar_get", mock_get):
        resp = client.get("/api/zalo/status", headers=auth_headers)
    assert resp.json()["data"]["owner_set"] is True


# ─── connect ───────────────────────────────────────────────────────────────


def test_zalo_connect_pending(client: TestClient, auth_headers: dict) -> None:
    mock_post = AsyncMock(
        return_value=_fake_response(200, {"status": "pending", "qr_url": "/qr.png"})
    )
    with (
        patch("hermes_mgmt.routes.zalo._ensure_sidecar", AsyncMock(return_value=True)),
        patch("hermes_mgmt.routes.zalo._sidecar_post", mock_post),
    ):
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
    with (
        patch("hermes_mgmt.routes.zalo._ensure_sidecar", AsyncMock(return_value=True)),
        patch("hermes_mgmt.routes.zalo._sidecar_post", mock_post),
    ):
        resp = client.post("/api/zalo/connect", headers=auth_headers)
    data = resp.json()["data"]
    assert data["status"] == "connected"
    assert data["bot_uid"] == "555"  # bot account, NOT owner
    # connect must NOT set owner from the bot uid
    env = read_env(test_settings.hermes_home / ".env")
    assert not env.get("ZALO_PERSONAL_OWNER_UID")


def test_zalo_connect_sidecar_cannot_spawn(client: TestClient, auth_headers: dict) -> None:
    # Sidecar can't be spawned (no node / missing files) → 503.
    with patch("hermes_mgmt.routes.zalo._ensure_sidecar", AsyncMock(return_value=False)):
        resp = client.post("/api/zalo/connect", headers=auth_headers)
    assert resp.status_code == 503


def test_zalo_connect_sidecar_down(client: TestClient, auth_headers: dict) -> None:
    mock_post = AsyncMock(side_effect=httpx.ConnectError("refused"))
    with (
        patch("hermes_mgmt.routes.zalo._ensure_sidecar", AsyncMock(return_value=True)),
        patch("hermes_mgmt.routes.zalo._sidecar_post", mock_post),
    ):
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


# ─── enable plugin in config.yaml ───────────────────────────────────────────


def test_enable_plugin_in_config_adds_key(test_settings: Settings) -> None:
    from hermes_mgmt.routes.zalo import _enable_plugin_in_config

    cfg = test_settings.hermes_home / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("plugins:\n  enabled: []\n", encoding="utf-8")
    with patch("hermes_mgmt.routes.zalo._plugin_key", return_value="zalo-personal-platform"):
        _enable_plugin_in_config(test_settings)
    import yaml

    data = yaml.safe_load(cfg.read_text())
    assert "zalo-personal-platform" in data["plugins"]["enabled"]
    # Must also flip the platform on, or the gateway never starts it.
    assert data["platforms"]["zalo-personal"]["enabled"] is True


def test_enable_plugin_in_config_idempotent_and_no_section(
    test_settings: Settings,
) -> None:
    from hermes_mgmt.routes.zalo import _enable_plugin_in_config

    cfg = test_settings.hermes_home / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("model: gpt-4o\n", encoding="utf-8")  # no plugins section
    with patch("hermes_mgmt.routes.zalo._plugin_key", return_value="zalo-personal-platform"):
        _enable_plugin_in_config(test_settings)
        _enable_plugin_in_config(test_settings)  # second call must not duplicate
    import yaml

    data = yaml.safe_load(cfg.read_text())
    assert data["plugins"]["enabled"].count("zalo-personal-platform") == 1
    assert data["model"] == "gpt-4o"  # preserved other keys


# ─── disconnect ────────────────────────────────────────────────────────────


def test_zalo_disconnect(client: TestClient, auth_headers: dict) -> None:
    mock_post = AsyncMock(return_value=_fake_response(200, {"ok": True}))
    with patch("hermes_mgmt.routes.zalo._sidecar_post", mock_post):
        resp = client.post("/api/zalo/disconnect", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "disconnected"


# ─── set-owner (boss phone → uid) ───────────────────────────────────────────


def test_set_owner_by_phone(
    client: TestClient, auth_headers: dict, test_settings: Settings
) -> None:
    # sidecar /users/by-phones resolves the boss phone → uid
    mock_post = AsyncMock(
        return_value=_fake_response(200, {"ok": True, "users": [{"phone": "0900", "uid": "boss123", "name": "Sếp"}]})
    )
    with (
        patch("hermes_mgmt.routes.zalo.httpx.AsyncClient.post", mock_post),
        patch("hermes_mgmt.routes.zalo._activate_plugin_and_handover", AsyncMock()) as mock_act,
    ):
        resp = client.post("/api/zalo/set-owner", headers=auth_headers, json={"phone": "0900"})
    assert resp.status_code == 200
    assert resp.json()["data"]["owner_uid"] == "boss123"
    env = read_env(test_settings.hermes_home / ".env")
    assert env.get("ZALO_PERSONAL_OWNER_UID") == "boss123"
    mock_act.assert_called_once()


def test_set_owner_by_uid_direct(
    client: TestClient, auth_headers: dict, test_settings: Settings
) -> None:
    with patch("hermes_mgmt.routes.zalo._activate_plugin_and_handover", AsyncMock()):
        resp = client.post("/api/zalo/set-owner", headers=auth_headers, json={"uid": "boss999"})
    assert resp.status_code == 200
    env = read_env(test_settings.hermes_home / ".env")
    assert env.get("ZALO_PERSONAL_OWNER_UID") == "boss999"


def test_set_owner_phone_not_found(client: TestClient, auth_headers: dict) -> None:
    mock_post = AsyncMock(return_value=_fake_response(200, {"ok": True, "users": []}))
    with patch("hermes_mgmt.routes.zalo.httpx.AsyncClient.post", mock_post):
        resp = client.post("/api/zalo/set-owner", headers=auth_headers, json={"phone": "0900"})
    assert resp.status_code == 404


def test_set_owner_missing_input(client: TestClient, auth_headers: dict) -> None:
    resp = client.post("/api/zalo/set-owner", headers=auth_headers, json={})
    assert resp.status_code == 400


def test_get_owner(client: TestClient, auth_headers: dict, test_settings: Settings) -> None:
    from hermes_mgmt.env_file import set_env

    set_env(test_settings.hermes_home / ".env", "ZALO_PERSONAL_OWNER_UID", "boss")
    resp = client.get("/api/zalo/owner", headers=auth_headers)
    assert resp.json()["data"]["owner_uid"] == "boss"
    assert resp.json()["data"]["owner_set"] is True
