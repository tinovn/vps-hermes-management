from __future__ import annotations

import logging
import re
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from hermes_mgmt.config import Settings
from hermes_mgmt.deps import get_settings_dep, require_auth
from hermes_mgmt.env_file import delete_env, list_env, set_env
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
    masked = list_env(settings.env_file, mask=True)
    return ApiResponse(ok=True, data=masked)


@router.put("/api/env/{key}", response_model=ApiResponse)
async def set_env_key(
    key: str,
    body: EnvKeyRequest,
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _validate_env_key(key)
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
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _validate_env_key(key)
    found = delete_env(settings.env_file, key)
    return ApiResponse(ok=True, data={"key": key, "removed": found})
