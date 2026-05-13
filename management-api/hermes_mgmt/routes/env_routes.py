from __future__ import annotations

import logging
import re
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from hermes_mgmt.cli_runner import run_hermes
from hermes_mgmt.config import Settings
from hermes_mgmt.deps import get_settings_dep, require_auth
from hermes_mgmt.env_file import delete_env, mask_value, read_env, set_env
from hermes_mgmt.models import ApiResponse, EnvKeyRequest
from hermes_mgmt.systemd_ctl import restart

logger = logging.getLogger(__name__)

router = APIRouter(tags=["env"], dependencies=[Depends(require_auth)])

_VALID_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def _validate_env_key(key: str) -> None:
    if not _VALID_KEY_RE.match(key):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid env key '{key}'. Must match ^[A-Z_][A-Z0-9_]*$",
        )


@router.get("/api/env", response_model=ApiResponse)
async def get_env(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    # Two .env files coexist: /opt/hermes/.env (systemd EnvironmentFile —
    # service auth tokens, ports, domain) and HERMES_HOME/.env (where
    # Hermes itself stores provider keys, the file the Dashboard UI reads).
    # Merge with HERMES_HOME taking priority for overlapping keys.
    merged = read_env(settings.env_file)
    merged.update(read_env(settings.hermes_home / ".env"))
    return ApiResponse(
        ok=True, data={k: mask_value(k, v) for k, v in merged.items()}
    )


@router.put("/api/env/{key}", response_model=ApiResponse)
async def set_env_key(
    key: str,
    body: EnvKeyRequest,
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _validate_env_key(key)
    # Hermes splits its env across two files and reads each for a different
    # purpose, so we must update both:
    #   - HERMES_HOME/.env: Hermes Dashboard reads it to render
    #     "configured" badges in the UI. Write via `hermes config set`.
    #   - /opt/hermes/.env: systemd EnvironmentFile= for the services.
    #     Adapter code (e.g. anthropic_adapter.os.getenv) only sees vars
    #     loaded here, so writing only HERMES_HOME/.env makes the key
    #     visible in UI but unusable for real API calls.
    result = await run_hermes(
        "config",
        ["set", key, body.value],
        env_overrides={"HERMES_HOME": str(settings.hermes_home)},
    )
    if result.exit_code != 0:
        raise HTTPException(
            status_code=500,
            detail=f"hermes config set {key} failed: {result.stderr}",
        )
    set_env(settings.env_file, key, body.value)

    async def do_restart() -> None:
        allowed = settings.allowed_services
        for svc in ("hermes-gateway", "hermes-dashboard"):
            if svc in allowed:
                try:
                    await restart(svc, allowed)
                except Exception as exc:
                    logger.error("Failed to restart %s: %s", svc, exc)

    background_tasks.add_task(do_restart)
    return ApiResponse(ok=True, data={"key": key, "set": True})


@router.delete("/api/env/{key}", response_model=ApiResponse)
async def delete_env_key(
    key: str,
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _validate_env_key(key)
    found_a = delete_env(settings.env_file, key)
    found_b = delete_env(settings.hermes_home / ".env", key)

    async def do_restart() -> None:
        allowed = settings.allowed_services
        for svc in ("hermes-gateway", "hermes-dashboard"):
            if svc in allowed:
                try:
                    await restart(svc, allowed)
                except Exception as exc:
                    logger.error("Failed to restart %s: %s", svc, exc)

    background_tasks.add_task(do_restart)
    return ApiResponse(ok=True, data={"key": key, "removed": found_a or found_b})
