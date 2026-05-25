"""v2 config endpoints — wrap `hermes config <subcommand>`.

CLI surface (from hermes-agent docs/reference/cli-commands.md):
    hermes config show              -> GET    /api/v2/config/show
    hermes config edit              -> (n/a, requires editor — skipped)
    hermes config set <key> <value> -> POST   /api/v2/config/set
    hermes config path              -> GET    /api/v2/config/path
    hermes config env-path          -> GET    /api/v2/config/env-path
    hermes config check             -> POST   /api/v2/config/check
    hermes config migrate           -> POST   /api/v2/config/migrate
"""
from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from hermes_mgmt.config import Settings
from hermes_mgmt.deps import get_settings_dep, require_auth
from hermes_mgmt.models import ApiResponse
from hermes_mgmt.routes.v2._base import cli_payload, raise_for_exit_code, run_for
from hermes_mgmt.routes.v2._parsers import (
    parse_config_set,
    parse_config_show,
    parse_config_status,
    parse_single_line,
)

router = APIRouter(
    prefix="/api/v2/config",
    tags=["v2:config"],
    dependencies=[Depends(require_auth)],
)


_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


class ConfigSetRequest(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    value: str

    @field_validator("key")
    @classmethod
    def _key(cls, v: str) -> str:
        if not _KEY_RE.match(v):
            raise ValueError("key must match ^[A-Za-z_][A-Za-z0-9_.]*$")
        return v


@router.get("/show", response_model=ApiResponse)
async def show(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "config", ["show"])
    raise_for_exit_code(result, "hermes config show failed")
    return ApiResponse(ok=True, data=cli_payload(result, parse_config_show))


@router.post("/set", response_model=ApiResponse)
async def set_key(
    body: ConfigSetRequest,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "config", ["set", body.key, body.value])
    raise_for_exit_code(result, f"hermes config set {body.key} failed")
    return ApiResponse(
        ok=True,
        data={"key": body.key, **cli_payload(result, parse_config_set)},
    )


@router.get("/path", response_model=ApiResponse)
async def config_path(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "config", ["path"])
    raise_for_exit_code(result, "hermes config path failed")
    return ApiResponse(ok=True, data=cli_payload(result, parse_single_line))


@router.get("/env-path", response_model=ApiResponse)
async def env_path(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "config", ["env-path"])
    raise_for_exit_code(result, "hermes config env-path failed")
    return ApiResponse(ok=True, data=cli_payload(result, parse_single_line))


@router.post("/check", response_model=ApiResponse)
async def check(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "config", ["check"])
    # check returns non-zero when config has drift — surface that, don't 500
    return ApiResponse(
        ok=result.exit_code == 0,
        data=cli_payload(result, parse_config_status),
    )


@router.post("/migrate", response_model=ApiResponse)
async def migrate(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    # migrate is interactive in CLI; run non-interactively and surface output
    result = await run_for(settings, "config", ["migrate"], timeout=60)
    return ApiResponse(
        ok=result.exit_code == 0,
        data=cli_payload(result),
    )
