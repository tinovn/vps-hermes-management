"""v2 diagnostic endpoints — wrap read-only `hermes <subcommand>` calls.

CLI surface:
    hermes status [--all] [--deep]      -> GET /api/v2/diagnostics/status
    hermes doctor [--fix]               -> POST /api/v2/diagnostics/doctor
    hermes dump [--show-keys]           -> GET /api/v2/diagnostics/dump
    hermes debug share [--lines N]      -> POST /api/v2/diagnostics/debug-share
    hermes insights [--days N]          -> GET /api/v2/diagnostics/insights
    hermes logs [name] [--lines N]      -> GET /api/v2/diagnostics/logs
"""
from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from hermes_mgmt.config import Settings
from hermes_mgmt.deps import get_settings_dep, require_auth
from hermes_mgmt.models import ApiResponse
from hermes_mgmt.routes.v2._base import cli_payload, raise_for_exit_code, run_for
from hermes_mgmt.routes.v2._parsers import (
    parse_doctor,
    parse_dump,
    parse_insights,
    parse_status_deep,
)

router = APIRouter(
    prefix="/api/v2/diagnostics",
    tags=["v2:diagnostics"],
    dependencies=[Depends(require_auth)],
)


_LOG_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


@router.get("/status", response_model=ApiResponse)
async def status_(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    deep: bool = Query(False),
    all_: bool = Query(False, alias="all"),
) -> ApiResponse:
    args: list[str] = []
    if all_:
        args.append("--all")
    if deep:
        args.append("--deep")
    result = await run_for(settings, "status", args, timeout=60)
    raise_for_exit_code(result, "hermes status failed")
    return ApiResponse(ok=True, data=cli_payload(result, parse_status_deep))


@router.post("/doctor", response_model=ApiResponse)
async def doctor(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    fix: bool = Query(False),
) -> ApiResponse:
    args: list[str] = []
    if fix:
        args.append("--fix")
    result = await run_for(settings, "doctor", args, timeout=120)
    # doctor returns non-zero when issues found; surface but don't 500
    return ApiResponse(
        ok=result.exit_code == 0, data=cli_payload(result, parse_doctor)
    )


@router.get("/dump", response_model=ApiResponse)
async def dump(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    show_keys: bool = Query(False),
) -> ApiResponse:
    args = ["--show-keys"] if show_keys else []
    result = await run_for(settings, "dump", args)
    raise_for_exit_code(result, "hermes dump failed")
    return ApiResponse(ok=True, data=cli_payload(result, parse_dump))


@router.post("/debug-share", response_model=ApiResponse)
async def debug_share(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    lines: int = Query(500, ge=1, le=10000),
) -> ApiResponse:
    result = await run_for(
        settings, "debug", ["share", "--lines", str(lines)], timeout=60
    )
    raise_for_exit_code(result, "hermes debug share failed")
    return ApiResponse(ok=True, data=cli_payload(result))


@router.get("/insights", response_model=ApiResponse)
async def insights(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    days: int = Query(7, ge=1, le=365),
    source: str | None = Query(None, max_length=64),
) -> ApiResponse:
    args = ["--days", str(days)]
    if source:
        if not re.match(r"^[a-z0-9_-]+$", source):
            raise HTTPException(
                status_code=422,
                detail="source must match ^[a-z0-9_-]+$",
            )
        args.extend(["--source", source])
    result = await run_for(settings, "insights", args, timeout=60)
    raise_for_exit_code(result, "hermes insights failed")
    return ApiResponse(ok=True, data=cli_payload(result, parse_insights))


@router.get("/logs", response_model=ApiResponse)
async def logs(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    name: str | None = Query(None, max_length=64),
    lines: int = Query(100, ge=1, le=10000),
) -> ApiResponse:
    args: list[str] = []
    if name:
        if not _LOG_NAME_RE.match(name):
            raise HTTPException(
                status_code=422,
                detail="log name must match ^[A-Za-z0-9_.-]{1,64}$",
            )
        args.append(name)
    args.extend(["--lines", str(lines)])
    result = await run_for(settings, "logs", args)
    raise_for_exit_code(result, "hermes logs failed")
    return ApiResponse(ok=True, data=cli_payload(result))
