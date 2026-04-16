from __future__ import annotations

import logging
from typing import Annotated, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sse_starlette.sse import EventSourceResponse

from hermes_mgmt.config import Settings
from hermes_mgmt.deps import get_settings_dep, require_auth
from hermes_mgmt.hermes_fs import follow_log_file, list_log_files, tail_log_file
from hermes_mgmt.models import ApiResponse
from hermes_mgmt.systemd_ctl import journal_follow, journal_tail

logger = logging.getLogger(__name__)

router = APIRouter(tags=["logs"], dependencies=[Depends(require_auth)])

_FILE_PREFIX = "hermes-file/"


@router.get("/api/logs", response_model=ApiResponse)
async def get_logs(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    service: str = Query(default="hermes-gateway"),
    lines: int = Query(default=100, ge=1, le=5000),
) -> ApiResponse:
    if service.startswith(_FILE_PREFIX):
        log_name = service[len(_FILE_PREFIX):]
        # Strip .log suffix if provided
        if log_name.endswith(".log"):
            log_name = log_name[:-4]
        try:
            content = tail_log_file(settings.hermes_home, log_name, lines)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            )
        return ApiResponse(ok=True, data={"service": service, "lines": content})

    # Systemd journal path
    allowed = settings.allowed_services
    if service not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Service '{service}' not in allowed list.",
        )
    content = await journal_tail(service, lines, allowed)
    return ApiResponse(ok=True, data={"service": service, "lines": content})


@router.get("/api/logs/stream")
async def stream_logs(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    service: str = Query(default="hermes-gateway"),
) -> EventSourceResponse:
    allowed = settings.allowed_services
    if service not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Service '{service}' not in allowed list.",
        )

    async def event_generator() -> AsyncIterator[dict]:
        async for line in journal_follow(service, allowed):
            if await request.is_disconnected():
                break
            yield {"data": line}

    return EventSourceResponse(event_generator())


@router.get("/api/logs/files", response_model=ApiResponse)
async def list_log_file_entries(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    files = list_log_files(settings.hermes_home)
    return ApiResponse(ok=True, data={"files": files})
