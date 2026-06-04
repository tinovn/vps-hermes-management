from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ApiResponse(BaseModel):
    ok: bool
    data: Any | None = None
    error: str | None = None


class ServiceStatus(BaseModel):
    name: str
    active: bool
    sub_state: str
    since: str


class StatusResponse(BaseModel):
    services: list[ServiceStatus]


class SystemMetrics(BaseModel):
    cpu_percent: float
    memory: dict[str, Any]
    disk: dict[str, Any]
    uptime_seconds: float
    load_avg: list[float]


class InfoResponse(BaseModel):
    domain: str
    ip: str
    hermes_version: str
    mgmt_version: str
    dashboard_url: str
    # AUTH_TOKEN Caddy expects in `?token=…` on first visit.
    # Null when HERMES_AUTH_TOKEN is not set in .env.
    auth_token: str | None = None


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class LoginResponse(BaseModel):
    token: str
    expires_at: int


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=8, max_length=256)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class EnvKeyRequest(BaseModel):
    value: str


class ProviderConfigRequest(BaseModel):
    provider: str
    # Optional: codex (ChatGPT account) takes no explicit model — it uses the
    # account default and rejects a specified model. Other providers need one.
    model: str = ""

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9_-]+$", v):
            raise ValueError("provider must match ^[a-z0-9_-]+$")
        return v


class ApiKeyRequest(BaseModel):
    provider: str
    api_key: str = Field(min_length=1)

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9_-]+$", v):
            raise ValueError("provider must match ^[a-z0-9_-]+$")
        return v


class DomainRequest(BaseModel):
    domain: str = Field(min_length=1, max_length=253)

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, v: str) -> str:
        pattern = r"^([a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)*[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?$"
        if not re.match(pattern, v) and v != "localhost":
            raise ValueError("invalid domain format")
        return v


class ChannelTokenRequest(BaseModel):
    token: str = Field(min_length=1)
    extra: dict[str, str] | None = None
    # Telegram-only: comma-joined into TELEGRAM_ALLOWED_USERS by the channels
    # route. Ignored for other channels.
    allowed_users: list[str] | None = None


class CronAddRequest(BaseModel):
    spec: str = Field(min_length=1)
    command: str = Field(min_length=1)
    name: str | None = None


class CliRequest(BaseModel):
    subcommand: str
    args: list[str] = []


class CliResponse(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
