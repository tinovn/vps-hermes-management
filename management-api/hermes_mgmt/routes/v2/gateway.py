"""v2 gateway endpoints — wrap `hermes gateway <action>`.

CLI surface:
    hermes gateway start    -> POST /api/v2/gateway/start
    hermes gateway stop     -> POST /api/v2/gateway/stop
    hermes gateway restart  -> POST /api/v2/gateway/restart
    hermes gateway status   -> GET  /api/v2/gateway/status
    hermes gateway list     -> GET  /api/v2/gateway

Note: on this VPS install the gateway is run by systemd (hermes-gateway.service),
NOT by `hermes gateway start` (which targets the CLI's own user-mode unit).
We still expose the CLI variants for parity; control endpoints in /api/restart
remain the canonical way to bounce the server-wide systemd service.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from hermes_mgmt.config import Settings
from hermes_mgmt.deps import get_settings_dep, require_auth
from hermes_mgmt.models import ApiResponse
from hermes_mgmt.routes.v2._base import cli_payload, raise_for_exit_code, run_for

router = APIRouter(
    prefix="/api/v2/gateway",
    tags=["v2:gateway"],
    dependencies=[Depends(require_auth)],
)


@router.get("", response_model=ApiResponse)
async def list_gateway(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "gateway", ["list"])
    raise_for_exit_code(result, "hermes gateway list failed")
    return ApiResponse(ok=True, data=cli_payload(result))


@router.get("/status", response_model=ApiResponse)
async def status_(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "gateway", ["status"])
    raise_for_exit_code(result, "hermes gateway status failed")
    return ApiResponse(ok=True, data=cli_payload(result))


@router.post("/start", response_model=ApiResponse)
async def start(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "gateway", ["start"], timeout=60)
    raise_for_exit_code(result, "hermes gateway start failed")
    return ApiResponse(ok=True, data=cli_payload(result))


@router.post("/stop", response_model=ApiResponse)
async def stop(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "gateway", ["stop"], timeout=60)
    raise_for_exit_code(result, "hermes gateway stop failed")
    return ApiResponse(ok=True, data=cli_payload(result))


@router.post("/restart", response_model=ApiResponse)
async def restart_(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "gateway", ["restart"], timeout=90)
    raise_for_exit_code(result, "hermes gateway restart failed")
    return ApiResponse(ok=True, data=cli_payload(result))
