from __future__ import annotations

from pathlib import Path
from typing import Generator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from hermes_mgmt.config import Settings

_API_KEY = "test-api-key-abcdef12"
_SESSION_SECRET = "test-session-secret-xyz"


def make_test_settings(tmp_dir: Path) -> Settings:
    env_file = tmp_dir / ".env"
    env_file.write_text(
        f"HERMES_MGMT_API_KEY={_API_KEY}\n"
        f"HERMES_MGMT_SESSION_SECRET={_SESSION_SECRET}\n"
        "DOMAIN=localhost\n"
        "HERMES_LOGIN_USER=\n"
        "HERMES_LOGIN_HASH=\n",
        encoding="utf-8",
    )
    s = Settings.model_construct(
        mgmt_api_key=_API_KEY,
        session_secret=_SESSION_SECRET,
        install_dir=tmp_dir,
        templates_dir=Path("/tmp/hermes-test-templates"),
        hermes_home=tmp_dir / ".hermes",
        domain="localhost",
        droplet_ip="127.0.0.1",
        mgmt_port=9997,
        dashboard_port=9119,
        allowed_services=("hermes-gateway", "hermes-dashboard", "hermes-mgmt", "caddy"),
    )
    # Attach env_file path as a plain attribute for modules that access it
    object.__setattr__(s, "_env_file_override", env_file)
    return s


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def temp_env_file(tmp_path: Path) -> Path:
    env_path = tmp_path / ".env"
    env_path.write_text(
        f"HERMES_MGMT_API_KEY={_API_KEY}\n"
        f"HERMES_MGMT_SESSION_SECRET={_SESSION_SECRET}\n"
        "DOMAIN=localhost\n",
        encoding="utf-8",
    )
    return env_path


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    return make_test_settings(tmp_path)


@pytest.fixture
def client(test_settings: Settings) -> Generator[TestClient, None, None]:
    from hermes_mgmt.main import create_app
    from hermes_mgmt import deps

    app = create_app()

    # Override both get_settings_dep and get_settings (used in require_auth)
    app.dependency_overrides[deps.get_settings_dep] = lambda: test_settings

    # Patch the lru_cache get_settings used directly in require_auth
    with patch("hermes_mgmt.deps.get_settings", return_value=test_settings):
        with patch("hermes_mgmt.config.get_settings", return_value=test_settings):
            with TestClient(app, raise_server_exceptions=True) as c:
                yield c

    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers(test_settings: Settings) -> dict[str, str]:
    return {"Authorization": f"Bearer {test_settings.mgmt_api_key}"}
