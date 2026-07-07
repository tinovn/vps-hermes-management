from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from hermes_mgmt.config import Settings
from hermes_mgmt.env_file import read_env, set_env


def _fake_response(status_code: int, json_body: dict | None = None) -> httpx.Response:
    return httpx.Response(status_code, json=json_body if json_body is not None else {})


def _write_creds(settings: Settings, legacy: bool = False) -> None:
    sub = "whatsapp/session" if legacy else "platforms/whatsapp/session"
    session = settings.hermes_home / sub
    session.mkdir(parents=True, exist_ok=True)
    (session / "creds.json").write_text("{}", encoding="utf-8")


def _mark_installed(settings: Settings) -> None:
    """Create the Baileys build artifact so _bridge_installed() reports installed."""
    bridge = settings.install_dir / "hermes-agent" / "scripts" / "whatsapp-bridge"
    lib = bridge / "node_modules" / "@whiskeysockets" / "baileys" / "lib"
    lib.mkdir(parents=True, exist_ok=True)
    (lib / "index.js").write_text("// baileys", encoding="utf-8")
    (bridge / "package.json").write_text('{"name":"bridge"}', encoding="utf-8")


# ─── status ──────────────────────────────────────────────────────────────────


def test_whatsapp_status_requires_auth(client: TestClient) -> None:
    assert client.get("/api/whatsapp/status").status_code == 401


def test_whatsapp_status_not_installed(client: TestClient, auth_headers: dict) -> None:
    # Fresh box: bridge deps not installed → status not_installed.
    with patch("hermes_mgmt.routes.whatsapp._port_open", return_value=False):
        resp = client.get("/api/whatsapp/status", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "not_installed"
    assert data["bridge_installed"] is False
    assert data["install_state"] == "not_installed"
    assert data["paired"] is False
    assert data["enabled"] is False
    assert data["valid_modes"] == ["bot", "self-chat"]


def test_whatsapp_status_disconnected_when_installed(
    client: TestClient, auth_headers: dict, test_settings: Settings
) -> None:
    # Installed but nothing paired → disconnected (ready to Connect).
    _mark_installed(test_settings)
    with patch("hermes_mgmt.routes.whatsapp._port_open", return_value=False):
        resp = client.get("/api/whatsapp/status", headers=auth_headers)
    data = resp.json()["data"]
    assert data["bridge_installed"] is True
    assert data["status"] == "disconnected"


def test_whatsapp_status_paired_not_enabled(
    client: TestClient, auth_headers: dict, test_settings: Settings
) -> None:
    _write_creds(test_settings)
    with patch("hermes_mgmt.routes.whatsapp._port_open", return_value=False):
        resp = client.get("/api/whatsapp/status", headers=auth_headers)
    data = resp.json()["data"]
    assert data["paired"] is True
    assert data["status"] == "paired"


def test_whatsapp_status_paired_legacy_session(
    client: TestClient, auth_headers: dict, test_settings: Settings
) -> None:
    # Installs paired via `hermes whatsapp` keep creds under the legacy
    # whatsapp/session path — status must detect that, not report "not paired".
    _write_creds(test_settings, legacy=True)
    with patch("hermes_mgmt.routes.whatsapp._port_open", return_value=False):
        resp = client.get("/api/whatsapp/status", headers=auth_headers)
    data = resp.json()["data"]
    assert data["paired"] is True
    assert data["status"] == "paired"


def test_whatsapp_connect_already_paired_legacy(
    client: TestClient, auth_headers: dict, test_settings: Settings
) -> None:
    # Must NOT spawn a second-device pairing sidecar when legacy creds exist.
    _write_creds(test_settings, legacy=True)
    resp = client.post("/api/whatsapp/connect", headers=auth_headers, json={})
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "paired"


def test_whatsapp_status_connected(
    client: TestClient, auth_headers: dict, test_settings: Settings
) -> None:
    _write_creds(test_settings)
    set_env(test_settings.env_file, "WHATSAPP_ENABLED", "true")
    with (
        patch("hermes_mgmt.routes.whatsapp._port_open", return_value=False),
        patch("hermes_mgmt.routes.whatsapp._bridge_connected", AsyncMock(return_value=True)),
    ):
        resp = client.get("/api/whatsapp/status", headers=auth_headers)
    data = resp.json()["data"]
    assert data["status"] == "connected"
    assert data["enabled"] is True
    assert data["bridge_connected"] is True


def test_whatsapp_status_pending_from_sidecar(
    client: TestClient, auth_headers: dict
) -> None:
    mock_get = AsyncMock(return_value=_fake_response(200, {"status": "pending", "qr": "abc", "paired": False}))
    with (
        patch("hermes_mgmt.routes.whatsapp._port_open", return_value=True),
        patch("hermes_mgmt.routes.whatsapp._sidecar_get", mock_get),
        patch("hermes_mgmt.routes.whatsapp._bridge_connected", AsyncMock(return_value=False)),
    ):
        resp = client.get("/api/whatsapp/status", headers=auth_headers)
    data = resp.json()["data"]
    assert data["status"] == "pending"
    assert data["qr_ready"] is True


# ─── connect ─────────────────────────────────────────────────────────────────


def test_whatsapp_connect_gated_when_not_installed(
    client: TestClient, auth_headers: dict
) -> None:
    # Bridge not installed → connect refused with 409 (install first).
    resp = client.post("/api/whatsapp/connect", headers=auth_headers, json={})
    assert resp.status_code == 409


def test_whatsapp_connect_pending(
    client: TestClient, auth_headers: dict, test_settings: Settings
) -> None:
    _mark_installed(test_settings)
    with patch("hermes_mgmt.routes.whatsapp._ensure_sidecar", AsyncMock(return_value=True)):
        resp = client.post("/api/whatsapp/connect", headers=auth_headers, json={})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "pending"
    assert data["qr_url"] == "/api/whatsapp/qr"


def test_whatsapp_connect_already_paired(
    client: TestClient, auth_headers: dict, test_settings: Settings
) -> None:
    _write_creds(test_settings)  # paired short-circuits before the install gate
    resp = client.post("/api/whatsapp/connect", headers=auth_headers, json={})
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "paired"


def test_whatsapp_connect_invalid_mode(client: TestClient, auth_headers: dict) -> None:
    resp = client.post("/api/whatsapp/connect", headers=auth_headers, json={"mode": "nope"})
    assert resp.status_code == 400


def test_whatsapp_connect_stores_mode(
    client: TestClient, auth_headers: dict, test_settings: Settings
) -> None:
    _mark_installed(test_settings)
    with patch("hermes_mgmt.routes.whatsapp._ensure_sidecar", AsyncMock(return_value=True)):
        resp = client.post("/api/whatsapp/connect", headers=auth_headers, json={"mode": "bot"})
    assert resp.status_code == 200
    assert read_env(test_settings.env_file).get("WHATSAPP_MODE") == "bot"


def test_whatsapp_connect_sidecar_fail(
    client: TestClient, auth_headers: dict, test_settings: Settings
) -> None:
    _mark_installed(test_settings)
    with patch("hermes_mgmt.routes.whatsapp._ensure_sidecar", AsyncMock(return_value=False)):
        resp = client.post("/api/whatsapp/connect", headers=auth_headers, json={})
    assert resp.status_code == 503


# ─── install (dashboard "Install WhatsApp bridge" button) ────────────────────


def test_whatsapp_install_starts(client: TestClient, auth_headers: dict) -> None:
    with patch(
        "hermes_mgmt.routes.whatsapp._start_install", AsyncMock(return_value="installing")
    ):
        resp = client.post("/api/whatsapp/install", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["install_state"] == "installing"


def test_whatsapp_install_already_installed(
    client: TestClient, auth_headers: dict, test_settings: Settings
) -> None:
    _mark_installed(test_settings)
    resp = client.post("/api/whatsapp/install", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["install_state"] == "installed"


def test_whatsapp_install_no_bridge(client: TestClient, auth_headers: dict) -> None:
    # No bridge package.json at all → 503.
    resp = client.post("/api/whatsapp/install", headers=auth_headers)
    assert resp.status_code == 503


def test_whatsapp_install_status_reports_state_and_log(
    client: TestClient, auth_headers: dict, test_settings: Settings
) -> None:
    _mark_installed(test_settings)
    bridge = test_settings.install_dir / "hermes-agent" / "scripts" / "whatsapp-bridge"
    (bridge / ".hermes-install.log").write_text("line1\nline2\n", encoding="utf-8")
    resp = client.get("/api/whatsapp/install-status", headers=auth_headers)
    data = resp.json()["data"]
    assert data["install_state"] == "installed"
    assert data["installed"] is True
    assert data["log"][-1] == "line2"


def test_install_state_transitions(test_settings: Settings) -> None:
    from hermes_mgmt.routes import whatsapp as wa

    bridge = test_settings.install_dir / "hermes-agent" / "scripts" / "whatsapp-bridge"
    bridge.mkdir(parents=True, exist_ok=True)
    # nothing yet
    assert wa._install_state(test_settings) == "not_installed"
    # failed marker
    (bridge / ".hermes-install.failed").write_text("boom", encoding="utf-8")
    assert wa._install_state(test_settings) == "failed"
    # installed wins over the stale failed marker
    _mark_installed(test_settings)
    assert wa._install_state(test_settings) == "installed"


def test_install_state_partial_dir_not_installed(test_settings: Settings) -> None:
    # Regression: npm creates an EMPTY baileys dir early during install. Without
    # a lib/index.js build artifact it must NOT count as installed.
    from hermes_mgmt.routes import whatsapp as wa

    bridge = test_settings.install_dir / "hermes-agent" / "scripts" / "whatsapp-bridge"
    (bridge / "node_modules" / "@whiskeysockets" / "baileys").mkdir(parents=True)
    assert wa._bridge_installed(test_settings) is False
    assert wa._install_state(test_settings) == "not_installed"


def test_install_state_running_beats_partial_dir(test_settings: Settings) -> None:
    # A live install job → "installing" even though the partial dir exists.
    import os

    from hermes_mgmt.routes import whatsapp as wa

    bridge = test_settings.install_dir / "hermes-agent" / "scripts" / "whatsapp-bridge"
    (bridge / "node_modules" / "@whiskeysockets" / "baileys").mkdir(parents=True)
    (bridge / ".hermes-install.pid").write_text(str(os.getpid()))  # a live pid
    assert wa._install_state(test_settings) == "installing"


# ─── self-heal pairing asset (upgrade path) ──────────────────────────────────


def test_self_heal_asset_fetches_when_missing(tmp_path, monkeypatch) -> None:
    import asyncio

    from hermes_mgmt.routes import whatsapp as wa

    dest = tmp_path / "assets" / "whatsapp_pair.mjs"
    monkeypatch.setattr(wa, "_PAIR_ASSET", dest)

    class _Resp:
        status_code = 200
        text = "// sidecar code\n"

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url): return _Resp()

    monkeypatch.setattr(wa.httpx, "AsyncClient", _Client)
    assert asyncio.run(wa._self_heal_asset()) is True
    assert dest.read_text() == "// sidecar code\n"


def test_self_heal_asset_returns_false_on_error(tmp_path, monkeypatch) -> None:
    import asyncio

    from hermes_mgmt.routes import whatsapp as wa

    monkeypatch.setattr(wa, "_PAIR_ASSET", tmp_path / "assets" / "whatsapp_pair.mjs")

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url): raise httpx.ConnectError("no net")

    monkeypatch.setattr(wa.httpx, "AsyncClient", _Client)
    assert asyncio.run(wa._self_heal_asset()) is False


# ─── qr image ────────────────────────────────────────────────────────────────


def test_whatsapp_qr_returns_png(client: TestClient, auth_headers: dict) -> None:
    mock_get = AsyncMock(return_value=_fake_response(200, {"status": "pending", "qr": "2@abc123", "paired": False}))
    with patch("hermes_mgmt.routes.whatsapp._sidecar_get", mock_get):
        resp = client.get("/api/whatsapp/qr", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"  # real PNG signature from segno


def test_whatsapp_qr_not_ready(client: TestClient, auth_headers: dict) -> None:
    mock_get = AsyncMock(return_value=_fake_response(200, {"status": "connecting", "qr": None, "paired": False}))
    with patch("hermes_mgmt.routes.whatsapp._sidecar_get", mock_get):
        resp = client.get("/api/whatsapp/qr", headers=auth_headers)
    assert resp.status_code == 404


def test_whatsapp_qr_sidecar_down(client: TestClient, auth_headers: dict) -> None:
    mock_get = AsyncMock(side_effect=httpx.ConnectError("refused"))
    with patch("hermes_mgmt.routes.whatsapp._sidecar_get", mock_get):
        resp = client.get("/api/whatsapp/qr", headers=auth_headers)
    assert resp.status_code == 503


# ─── enable ──────────────────────────────────────────────────────────────────


def test_whatsapp_enable_requires_creds(client: TestClient, auth_headers: dict) -> None:
    resp = client.post("/api/whatsapp/enable", headers=auth_headers, json={"mode": "self-chat"})
    assert resp.status_code == 409


def test_whatsapp_enable_bot_requires_allowed(
    client: TestClient, auth_headers: dict, test_settings: Settings
) -> None:
    _write_creds(test_settings)
    resp = client.post("/api/whatsapp/enable", headers=auth_headers, json={"mode": "bot"})
    assert resp.status_code == 400


def test_whatsapp_enable_self_chat(
    client: TestClient, auth_headers: dict, test_settings: Settings
) -> None:
    _write_creds(test_settings)
    with (
        patch("hermes_mgmt.routes.whatsapp._port_open", return_value=False),
        patch("hermes_mgmt.routes.whatsapp.restart", AsyncMock()) as mock_restart,
    ):
        resp = client.post("/api/whatsapp/enable", headers=auth_headers, json={"mode": "self-chat"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "enabled"
    assert data["restarted"] is True
    env = read_env(test_settings.env_file)
    assert env.get("WHATSAPP_ENABLED") == "true"
    assert env.get("WHATSAPP_MODE") == "self-chat"
    # also written to the Hermes store
    assert read_env(test_settings.hermes_home / ".env").get("WHATSAPP_ENABLED") == "true"
    mock_restart.assert_awaited_once()


def test_whatsapp_enable_bot_with_allowed(
    client: TestClient, auth_headers: dict, test_settings: Settings
) -> None:
    _write_creds(test_settings)
    with (
        patch("hermes_mgmt.routes.whatsapp._port_open", return_value=False),
        patch("hermes_mgmt.routes.whatsapp.restart", AsyncMock()),
    ):
        resp = client.post(
            "/api/whatsapp/enable",
            headers=auth_headers,
            json={"mode": "bot", "allowed_users": "15551234567"},
        )
    assert resp.status_code == 200
    env = read_env(test_settings.env_file)
    assert env.get("WHATSAPP_ALLOWED_USERS") == "15551234567"
    assert env.get("WHATSAPP_MODE") == "bot"


# ─── disconnect ──────────────────────────────────────────────────────────────


def test_whatsapp_disconnect(
    client: TestClient, auth_headers: dict, test_settings: Settings
) -> None:
    _write_creds(test_settings)
    with (
        patch("hermes_mgmt.routes.whatsapp._port_open", return_value=False),
        patch("hermes_mgmt.routes.whatsapp.restart", AsyncMock()) as mock_restart,
    ):
        resp = client.post("/api/whatsapp/disconnect", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "disconnected"
    assert read_env(test_settings.env_file).get("WHATSAPP_ENABLED") == "false"
    # session dir removed
    assert not (test_settings.hermes_home / "platforms" / "whatsapp" / "session").exists()
    mock_restart.assert_awaited_once()
