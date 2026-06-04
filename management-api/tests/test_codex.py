from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from hermes_mgmt.config import Settings


def _write_auth(settings: Settings, content: dict) -> None:
    f = settings.hermes_home / "auth.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(content), encoding="utf-8")


# ─── auth ────────────────────────────────────────────────────────────────────


def test_codex_status_requires_auth(client: TestClient) -> None:
    assert client.get("/api/codex/auth/status").status_code == 401


def test_codex_status_disconnected(client: TestClient, auth_headers: dict) -> None:
    with patch("hermes_mgmt.routes.codex._flow", {"proc": None, "url": None, "code": None}):
        resp = client.get("/api/codex/auth/status", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "disconnected"


def test_codex_status_connected_sets_model(
    client: TestClient, auth_headers: dict, test_settings: Settings
) -> None:
    _write_auth(test_settings, {"codex": {"access_token": "tok"}})
    (test_settings.hermes_home / "config.yaml").write_text("model:\n  provider: deepseek\n")
    with patch("hermes_mgmt.routes.codex.restart", AsyncMock(return_value=(0, "ok"))):
        resp = client.get("/api/codex/auth/status", headers=auth_headers)
    data = resp.json()["data"]
    assert data["status"] == "connected"
    import yaml

    cfg = yaml.safe_load((test_settings.hermes_home / "config.yaml").read_text())
    assert cfg["model"]["provider"] == "codex"


# ─── start ───────────────────────────────────────────────────────────────────


def test_codex_start_parses_url_and_code(client: TestClient, auth_headers: dict) -> None:
    # Mirror real CLI output incl. ANSI colour codes + 4-5 char hyphenated code.
    out = (
        b"To continue, follow these steps:\n"
        b"  1. Open this URL in your browser:\n"
        b"     \x1b[94mhttps://auth.openai.com/codex/device\x1b[0m\n"
        b"  2. Enter this code:\n"
        b"     \x1b[94m41JU-ST9W8\x1b[0m\n"
        b"Waiting for sign-in...\n"
    )

    class _FakeStdout:
        def __init__(self, data: bytes) -> None:
            self._data = data
            self._sent = False

        async def read(self, n: int) -> bytes:
            if self._sent:
                return b""
            self._sent = True
            return self._data

    class _FakeProc:
        returncode = None
        stdout = _FakeStdout(out)

    with (
        patch("hermes_mgmt.routes.codex._flow", {"proc": None, "url": None, "code": None, "started": 0.0, "output": ""}),
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=_FakeProc())),
    ):
        resp = client.post("/api/codex/auth/start", headers=auth_headers)
    data = resp.json()["data"]
    assert data["status"] == "pending"
    assert data["url"] == "https://auth.openai.com/codex/device"
    assert data["code"] == "41JU-ST9W8"


# ─── import ──────────────────────────────────────────────────────────────────


def test_codex_import_missing(client: TestClient, auth_headers: dict) -> None:
    resp = client.post("/api/codex/auth/import", headers=auth_headers, json={})
    assert resp.status_code == 400


def test_codex_import_no_codex_entry(client: TestClient, auth_headers: dict) -> None:
    resp = client.post(
        "/api/codex/auth/import", headers=auth_headers, json={"auth_json": {"telegram": {}}}
    )
    assert resp.status_code == 400


def test_codex_import_ok_sets_model(
    client: TestClient, auth_headers: dict, test_settings: Settings
) -> None:
    test_settings.hermes_home.mkdir(parents=True, exist_ok=True)
    (test_settings.hermes_home / "config.yaml").write_text("model:\n  provider: deepseek\n")
    payload = {"auth_json": {"codex": {"access_token": "tok", "refresh_token": "r"}}}
    with patch("hermes_mgmt.routes.codex.restart", AsyncMock(return_value=(0, "ok"))):
        resp = client.post("/api/codex/auth/import", headers=auth_headers, json=payload)
    assert resp.status_code == 200
    assert resp.json()["data"]["imported"] is True
    # auth.json written + model switched to codex
    saved = json.loads((test_settings.hermes_home / "auth.json").read_text())
    assert "codex" in saved
    import yaml

    cfg = yaml.safe_load((test_settings.hermes_home / "config.yaml").read_text())
    assert cfg["model"]["provider"] == "codex"


def test_codex_import_accepts_string_json(
    client: TestClient, auth_headers: dict, test_settings: Settings
) -> None:
    payload = {"auth_json": json.dumps({"openai-codex": {"access_token": "t"}})}
    with patch("hermes_mgmt.routes.codex.restart", AsyncMock(return_value=(0, "ok"))):
        resp = client.post("/api/codex/auth/import", headers=auth_headers, json=payload)
    assert resp.status_code == 200


# ─── disable ─────────────────────────────────────────────────────────────────


def test_codex_disable_clears_auth_and_config(
    client: TestClient, auth_headers: dict, test_settings: Settings
) -> None:
    _write_auth(test_settings, {"active_provider": "openai-codex",
                                "providers": {"openai-codex": {"access_token": "t"}},
                                "codex": {"access_token": "t"}})
    test_settings.hermes_home.mkdir(parents=True, exist_ok=True)
    (test_settings.hermes_home / "config.yaml").write_text("model:\n  provider: codex\n")
    with (
        patch("hermes_mgmt.routes.codex.asyncio.create_subprocess_exec", AsyncMock()),
        patch("hermes_mgmt.routes.codex.restart", AsyncMock(return_value=(0, "ok"))),
    ):
        resp = client.post("/api/codex/auth/disable", headers=auth_headers,
                           json={"to_provider": "deepseek"})
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "disconnected"
    auth = json.loads((test_settings.hermes_home / "auth.json").read_text())
    assert auth["active_provider"] is None
    assert "codex" not in auth and "openai-codex" not in auth.get("providers", {})
    import yaml
    cfg = yaml.safe_load((test_settings.hermes_home / "config.yaml").read_text())
    assert cfg["model"]["provider"] == "deepseek"
