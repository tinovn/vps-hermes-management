from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from fastapi.testclient import TestClient

from hermes_mgmt.config import Settings


@pytest.fixture
def config_dir(tmp_path: Path):
    """A fake repo config dir with one rule group + one preset role."""
    rules = tmp_path / "config" / "rules"
    roles = tmp_path / "config" / "roles"
    rules.mkdir(parents=True)
    roles.mkdir(parents=True)
    (rules / "a-identity.md").write_text(
        "# Nhóm A — Danh tính\n> guidance\n- Luôn xưng là trợ lý của sếp.\n",
        encoding="utf-8",
    )
    (rules / "f-conversation-quality.md").write_text(
        "# Nhóm F — Hội thoại\n- Trả lời đúng trọng tâm.\n", encoding="utf-8"
    )
    (roles / "cskh.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "cskh",
                "label": "CSKH",
                "description": "Chăm sóc khách hàng",
                "emoji": "🎧",
                "tone": "Lễ phép",
                "persona": "Bạn là trợ lý CSKH.",
                "rules": ["a-identity", "f-conversation-quality"],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    with patch("hermes_mgmt.routes.roles._config_dir", return_value=tmp_path / "config"):
        yield tmp_path / "config"


def test_list_rules(client: TestClient, auth_headers: dict, config_dir) -> None:
    resp = client.get("/api/rules", headers=auth_headers)
    assert resp.status_code == 200
    ids = [g["id"] for g in resp.json()["data"]["groups"]]
    assert "a-identity" in ids and "f-conversation-quality" in ids


def test_list_roles_includes_preset(client: TestClient, auth_headers: dict, config_dir) -> None:
    resp = client.get("/api/roles", headers=auth_headers)
    roles = resp.json()["data"]["roles"]
    assert any(r["id"] == "cskh" and r["source"] == "preset" for r in roles)


def test_get_role(client: TestClient, auth_headers: dict, config_dir) -> None:
    resp = client.get("/api/roles/cskh", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["persona"] == "Bạn là trợ lý CSKH."


def test_get_role_404(client: TestClient, auth_headers: dict, config_dir) -> None:
    assert client.get("/api/roles/nope", headers=auth_headers).status_code == 404


def test_create_custom_role(client: TestClient, auth_headers: dict, config_dir) -> None:
    resp = client.post(
        "/api/roles",
        headers=auth_headers,
        json={"id": "spa", "label": "Spa", "persona": "Bạn là lễ tân spa.", "rules": ["a-identity", "bogus"]},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["saved"] is True
    assert "bogus" in data["ignored_rules"]  # unknown rule filtered
    # now listed as custom
    roles = client.get("/api/roles", headers=auth_headers).json()["data"]["roles"]
    assert any(r["id"] == "spa" and r["source"] == "custom" for r in roles)


def test_create_role_requires_persona(client: TestClient, auth_headers: dict, config_dir) -> None:
    resp = client.post("/api/roles", headers=auth_headers, json={"id": "x"})
    assert resp.status_code == 400


def test_create_role_cannot_shadow_preset(client: TestClient, auth_headers: dict, config_dir) -> None:
    resp = client.post(
        "/api/roles", headers=auth_headers, json={"id": "cskh", "persona": "x"}
    )
    assert resp.status_code == 409


def test_delete_preset_forbidden(client: TestClient, auth_headers: dict, config_dir) -> None:
    assert client.delete("/api/roles/cskh", headers=auth_headers).status_code == 403


def test_delete_custom_role(client: TestClient, auth_headers: dict, config_dir) -> None:
    client.post("/api/roles", headers=auth_headers, json={"id": "tmp", "persona": "p"})
    assert client.delete("/api/roles/tmp", headers=auth_headers).status_code == 200
    assert client.delete("/api/roles/tmp", headers=auth_headers).status_code == 404


def test_apply_role_builds_persona(
    client: TestClient, auth_headers: dict, config_dir, test_settings: Settings, tmp_path
) -> None:
    # Point the plugin session dir at a temp folder so we can read bot_persona.json.
    from hermes_mgmt.env_file import set_env

    sess = tmp_path / "zalo-session"
    set_env(test_settings.hermes_home / ".env", "ZALO_PERSONAL_SESSION_DIR", str(sess))
    with patch("hermes_mgmt.routes.roles.restart", AsyncMock(return_value=(0, "ok"))):
        resp = client.post("/api/roles/cskh/apply", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["applied"] is True
    # persona written into the plugin's bot_persona.json (3 fields)
    persona_obj = json.loads((sess / "bot_persona.json").read_text(encoding="utf-8"))
    assert "name" in persona_obj and "self_intro" in persona_obj
    persona = persona_obj["personality"]
    assert "Bạn là trợ lý CSKH." in persona
    assert "Luôn xưng là trợ lý của sếp." in persona  # rule a-identity body
    assert "Trả lời đúng trọng tâm." in persona       # rule f body
    # active_role.json records it
    active = json.loads((test_settings.hermes_home / "active_role.json").read_text())
    assert active["id"] == "cskh"


def test_active_role(
    client: TestClient, auth_headers: dict, config_dir, test_settings: Settings, tmp_path
) -> None:
    from hermes_mgmt.env_file import set_env

    set_env(test_settings.hermes_home / ".env", "ZALO_PERSONAL_SESSION_DIR", str(tmp_path / "z"))
    with patch("hermes_mgmt.routes.roles.restart", AsyncMock(return_value=(0, "ok"))):
        client.post("/api/roles/cskh/apply", headers=auth_headers)
    resp = client.get("/api/roles/active", headers=auth_headers)
    assert resp.json()["data"]["id"] == "cskh"
