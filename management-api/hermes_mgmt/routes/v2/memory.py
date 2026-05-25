"""v2 memory endpoints — wrap `hermes memory <action>`.

CLI surface:
    hermes memory status  -> GET  /api/v2/memory/status
    hermes memory off     -> POST /api/v2/memory/off
    hermes memory setup   -> (interactive; skipped — use /api/v2/config/set
                              with the appropriate memory.* key instead)
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from hermes_mgmt.config import Settings
from hermes_mgmt.deps import get_settings_dep, require_auth
from hermes_mgmt.models import ApiResponse
from hermes_mgmt.routes.v2._base import cli_payload, raise_for_exit_code, run_for
from hermes_mgmt.routes.v2._parsers import parse_memory_status

router = APIRouter(
    prefix="/api/v2/memory",
    tags=["v2:memory"],
    dependencies=[Depends(require_auth)],
)


@router.get("/status", response_model=ApiResponse)
async def status_(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "memory", ["status"])
    raise_for_exit_code(result, "hermes memory status failed")
    return ApiResponse(ok=True, data=cli_payload(result, parse_memory_status))


@router.post("/off", response_model=ApiResponse)
async def off(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "memory", ["off"])
    raise_for_exit_code(result, "hermes memory off failed")
    return ApiResponse(ok=True, data=cli_payload(result))
