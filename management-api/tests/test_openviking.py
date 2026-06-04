from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from hermes_mgmt.config import Settings
from hermes_mgmt.env_file import read_env, set_env


def test_ov_status_requires_auth(client: TestClient) -> None:
    assert client.get("/api/openviking/status").status_code == 401


def test_ov_status_not_installed(client: TestClient, auth_headers: dict) -> None:
    with patch("hermes_mgmt.routes.openviking._is_installed", return_value=False):
        resp = client.get("/api/openviking/status", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["installed"] is False
    assert data["service_active"] is False
    assert data["endpoint"].endswith(":1933")


def test_ov_status_installed_active_healthy_wired(
    client: TestClient, auth_headers: dict, test_settings: Settings
) -> None:
    set_env(test_settings.hermes_home / ".env", "OPENVIKING_ENDPOINT", "http://127.0.0.1:1933")
    with (
        patch("hermes_mgmt.routes.openviking._is_installed", return_value=True),
        patch("hermes_mgmt.routes.openviking.is_active", AsyncMock(return_value=True)),
        patch("hermes_mgmt.routes.openviking._health", AsyncMock(return_value=True)),
        patch("hermes_mgmt.routes.openviking._config_has_keys", return_value=True),
    ):
        resp = client.get("/api/openviking/status", headers=auth_headers)
    data = resp.json()["data"]
    assert data["installed"] is True
    assert data["service_active"] is True
    assert data["healthy"] is True
    assert data["wired_into_hermes"] is True
    assert data["config_ready"] is True


def test_ov_install_already_installed(client: TestClient, auth_headers: dict) -> None:
    with patch("hermes_mgmt.routes.openviking._is_installed", return_value=True):
        resp = client.post("/api/openviking/install", headers=auth_headers)
    assert resp.status_code == 202
    assert "already installed" in resp.json()["data"]["message"]


def test_ov_install_triggers_background(client: TestClient, auth_headers: dict) -> None:
    with (
        patch("hermes_mgmt.routes.openviking._is_installed", return_value=False),
        patch("hermes_mgmt.routes.openviking._do_install", AsyncMock()) as mock_do,
    ):
        resp = client.post("/api/openviking/install", headers=auth_headers)
    assert resp.status_code == 202
    mock_do.assert_called_once()


def test_ov_config_requires_install(client: TestClient, auth_headers: dict) -> None:
    with patch("hermes_mgmt.routes.openviking._is_installed", return_value=False):
        resp = client.post("/api/openviking/config", headers=auth_headers, json={"api_key": "k"})
    assert resp.status_code == 409


def test_ov_config_writes_file(
    client: TestClient, auth_headers: dict, tmp_path
) -> None:
    conf = tmp_path / "ov.conf"
    with (
        patch("hermes_mgmt.routes.openviking._is_installed", return_value=True),
        patch("hermes_mgmt.routes.openviking._OV_CONF", conf),
    ):
        resp = client.post(
            "/api/openviking/config",
            headers=auth_headers,
            json={"api_key": "sk-test", "embedding_model": "text-embedding-3-small"},
        )
    assert resp.status_code == 200
    import json

    written = json.loads(conf.read_text())
    assert written["vlm"]["api_key"] == "sk-test"
    assert written["embedding"]["dense"]["api_key"] == "sk-test"


def test_ov_config_missing_key(client: TestClient, auth_headers: dict, tmp_path) -> None:
    conf = tmp_path / "ov.conf"  # absent → no existing key to fall back on
    with (
        patch("hermes_mgmt.routes.openviking._is_installed", return_value=True),
        patch("hermes_mgmt.routes.openviking._OV_CONF", conf),
    ):
        resp = client.post("/api/openviking/config", headers=auth_headers, json={})
    assert resp.status_code == 400


def test_ov_enable_requires_config(client: TestClient, auth_headers: dict) -> None:
    with (
        patch("hermes_mgmt.routes.openviking._is_installed", return_value=True),
        patch("hermes_mgmt.routes.openviking._config_has_keys", return_value=False),
    ):
        resp = client.post("/api/openviking/enable", headers=auth_headers)
    assert resp.status_code == 409


def test_ov_enable_starts_and_wires(
    client: TestClient, auth_headers: dict, test_settings: Settings
) -> None:
    with (
        patch("hermes_mgmt.routes.openviking._is_installed", return_value=True),
        patch("hermes_mgmt.routes.openviking._config_has_keys", return_value=True),
        patch("hermes_mgmt.routes.openviking.start", AsyncMock(return_value=(0, "ok"))),
        patch("hermes_mgmt.routes.openviking.restart", AsyncMock(return_value=(0, "ok"))),
    ):
        resp = client.post("/api/openviking/enable", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["enabled"] is True
    env = read_env(test_settings.hermes_home / ".env")
    assert env.get("OPENVIKING_ENDPOINT") == "http://127.0.0.1:1933"


def test_ov_disable_stops_and_unwires(
    client: TestClient, auth_headers: dict, test_settings: Settings
) -> None:
    set_env(test_settings.hermes_home / ".env", "OPENVIKING_ENDPOINT", "http://127.0.0.1:1933")
    with (
        patch("hermes_mgmt.routes.openviking.stop", AsyncMock(return_value=(0, "ok"))),
        patch("hermes_mgmt.routes.openviking.restart", AsyncMock(return_value=(0, "ok"))),
    ):
        resp = client.post("/api/openviking/disable", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["enabled"] is False
    env = read_env(test_settings.hermes_home / ".env")
    assert "OPENVIKING_ENDPOINT" not in env


def test_ov_uninstall_unwires_and_runs(
    client: TestClient, auth_headers: dict, test_settings: Settings
) -> None:
    set_env(test_settings.hermes_home / ".env", "OPENVIKING_ENDPOINT", "http://127.0.0.1:1933")
    with (
        patch("hermes_mgmt.routes.openviking.stop", AsyncMock(return_value=(0, "ok"))),
        patch("hermes_mgmt.routes.openviking.restart", AsyncMock(return_value=(0, "ok"))),
        patch("hermes_mgmt.routes.openviking._do_uninstall", AsyncMock()) as mock_un,
    ):
        resp = client.post("/api/openviking/uninstall", headers=auth_headers, json={"purge": True})
    assert resp.status_code == 202
    mock_un.assert_called_once_with(True)
    env = read_env(test_settings.hermes_home / ".env")
    assert "OPENVIKING_ENDPOINT" not in env


# ─── config (read) ───────────────────────────────────────────────────────────


def test_ov_get_config_not_configured(client: TestClient, auth_headers: dict, tmp_path) -> None:
    conf = tmp_path / "ov.conf"  # absent
    with patch("hermes_mgmt.routes.openviking._OV_CONF", conf):
        resp = client.get("/api/openviking/config", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["configured"] is False


def test_ov_get_config_masks_keys(client: TestClient, auth_headers: dict, tmp_path) -> None:
    import json as _json

    conf = tmp_path / "ov.conf"
    conf.write_text(_json.dumps({
        "embedding": {"dense": {"api_key": "sk-secret12345678", "model": "m1", "provider": "openai"}},
        "vlm": {"api_key": "sk-secret12345678", "model": "m2", "provider": "openai"},
    }), encoding="utf-8")
    with patch("hermes_mgmt.routes.openviking._OV_CONF", conf):
        resp = client.get("/api/openviking/config", headers=auth_headers)
    data = resp.json()["data"]
    assert data["configured"] is True
    assert data["vlm"]["model"] == "m2"
    assert "secret" not in data["embedding"]["api_key"]  # masked
    assert data["embedding"]["api_key"].endswith("5678")


# ─── test-key ────────────────────────────────────────────────────────────────


def test_ov_test_key_missing(client: TestClient, auth_headers: dict) -> None:
    resp = client.post("/api/openviking/test-key", headers=auth_headers, json={})
    assert resp.status_code == 400


def test_ov_test_key_valid(client: TestClient, auth_headers: dict) -> None:
    import httpx as _httpx

    mock = AsyncMock(return_value=_httpx.Response(200, json={"data": []}))
    with patch("httpx.AsyncClient.get", mock):
        resp = client.post(
            "/api/openviking/test-key", headers=auth_headers, json={"api_key": "sk-x"}
        )
    assert resp.json()["data"]["valid"] is True


def test_ov_test_key_invalid(client: TestClient, auth_headers: dict) -> None:
    import httpx as _httpx

    mock = AsyncMock(return_value=_httpx.Response(401, json={"error": "bad"}))
    with patch("httpx.AsyncClient.get", mock):
        resp = client.post(
            "/api/openviking/test-key", headers=auth_headers, json={"api_key": "sk-x"}
        )
    body = resp.json()
    assert body["ok"] is False
    assert body["data"]["valid"] is False


# ─── restart ─────────────────────────────────────────────────────────────────


def test_ov_restart_not_installed(client: TestClient, auth_headers: dict) -> None:
    with patch("hermes_mgmt.routes.openviking._is_installed", return_value=False):
        resp = client.post("/api/openviking/restart", headers=auth_headers)
    assert resp.status_code == 404


def test_ov_restart_ok(client: TestClient, auth_headers: dict) -> None:
    with (
        patch("hermes_mgmt.routes.openviking._is_installed", return_value=True),
        patch("hermes_mgmt.routes.openviking.restart", AsyncMock(return_value=(0, "ok"))),
    ):
        resp = client.post("/api/openviking/restart", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["restarted"] is True


# ─── upgrade ─────────────────────────────────────────────────────────────────


def test_ov_upgrade_not_installed(client: TestClient, auth_headers: dict) -> None:
    with patch("hermes_mgmt.routes.openviking._is_installed", return_value=False):
        resp = client.post("/api/openviking/upgrade", headers=auth_headers)
    assert resp.status_code == 404


def test_ov_upgrade_triggers_background(client: TestClient, auth_headers: dict) -> None:
    with (
        patch("hermes_mgmt.routes.openviking._is_installed", return_value=True),
        patch("hermes_mgmt.routes.openviking._do_upgrade", AsyncMock()) as mock_up,
    ):
        resp = client.post("/api/openviking/upgrade", headers=auth_headers)
    assert resp.status_code == 202
    mock_up.assert_called_once()


# ─── stats ───────────────────────────────────────────────────────────────────


def test_ov_stats_not_installed(client: TestClient, auth_headers: dict) -> None:
    with patch("hermes_mgmt.routes.openviking._is_installed", return_value=False):
        resp = client.get("/api/openviking/stats", headers=auth_headers)
    assert resp.status_code == 404


def test_ov_stats_reports_disk_and_uptime(client: TestClient, auth_headers: dict) -> None:
    with (
        patch("hermes_mgmt.routes.openviking._is_installed", return_value=True),
        patch("hermes_mgmt.routes.openviking.is_active", AsyncMock(return_value=True)),
        patch("hermes_mgmt.routes.openviking.active_since", AsyncMock(return_value="2026-06-04T10:00:00Z")),
        patch("hermes_mgmt.routes.openviking._dir_size_bytes", return_value=5 * 1024 * 1024),
        patch("httpx.AsyncClient.get", AsyncMock(side_effect=__import__("httpx").ConnectError("down"))),
    ):
        resp = client.get("/api/openviking/stats", headers=auth_headers)
    data = resp.json()["data"]
    assert data["service_active"] is True
    assert data["data_size_mb"] == 5.0
    assert data["server_stats"] is None
