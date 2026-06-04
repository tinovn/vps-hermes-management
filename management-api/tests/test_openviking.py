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
