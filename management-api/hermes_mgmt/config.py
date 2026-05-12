from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="/opt/hermes/.env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    mgmt_api_key: str = Field(
        default="changeme-set-HERMES_MGMT_API_KEY-in-env",
        alias="hermes_mgmt_api_key",
    )
    session_secret: str = Field(
        default="changeme-set-HERMES_MGMT_SESSION_SECRET-in-env",
        alias="hermes_mgmt_session_secret",
    )
    # Token Caddy's auth gate accepts in `?token=…` (first visit) before it
    # drops a 30-day cookie. Exposed via GET /api/info as part of dashboard_url
    # so the provisioning system (Hostbill) can hand a one-click link to the
    # end customer.
    auth_token: str = Field(default="", alias="hermes_auth_token")
    install_dir: Path = Field(default=Path("/opt/hermes"), alias="hermes_install_dir")
    templates_dir: Path = Field(
        default=Path("/etc/hermes/config"), alias="hermes_templates_dir"
    )
    hermes_home: Path = Field(default=Path("/opt/hermes/.hermes"), alias="hermes_home")
    domain: str = Field(default="localhost")
    droplet_ip: str = Field(default="127.0.0.1", alias="hermes_droplet_ip")
    mgmt_port: int = Field(default=9997, alias="hermes_mgmt_port")
    dashboard_port: int = Field(default=9119)
    allowed_services: tuple[str, ...] = (
        "hermes-gateway",
        "hermes-dashboard",
        "hermes-mgmt",
        "caddy",
    )

    @model_validator(mode="before")
    @classmethod
    def set_hermes_home_default(cls, values: Any) -> Any:
        if isinstance(values, dict):
            if not values.get("hermes_home") and not values.get("HERMES_HOME"):
                install_dir = values.get("hermes_install_dir") or values.get(
                    "HERMES_INSTALL_DIR", "/opt/hermes"
                )
                values["hermes_home"] = str(Path(install_dir) / ".hermes")
        return values

    @property
    def env_file(self) -> Path:
        return self.install_dir / ".env"

    @property
    def cors_origins(self) -> list[str]:
        origins = [
            f"https://{self.domain}",
            f"http://127.0.0.1:{self.mgmt_port}",
            f"http://localhost:{self.mgmt_port}",
        ]
        if self.domain != "localhost":
            origins.append(f"http://{self.domain}")
        return origins

    model_config = SettingsConfigDict(
        env_file="/opt/hermes/.env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )


@lru_cache
def get_settings() -> Settings:
    try:
        settings = Settings()
        logger.info(
            "Settings loaded from env; API key prefix=%s...",
            settings.mgmt_api_key[:8] if len(settings.mgmt_api_key) >= 8 else "***",
        )
        return settings
    except Exception as exc:
        logger.warning("Could not load settings from /opt/hermes/.env: %s", exc)
        return Settings.model_construct(
            mgmt_api_key="changeme",
            session_secret="changeme",
            install_dir=Path("/opt/hermes"),
            templates_dir=Path("/etc/hermes/config"),
            hermes_home=Path("/opt/hermes/.hermes"),
            domain="localhost",
            droplet_ip="127.0.0.1",
            mgmt_port=9997,
            dashboard_port=9119,
        )
