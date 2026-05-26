"""v2 sessions endpoints — wrap `hermes sessions <action>`.

CLI surface:
    hermes sessions list                        -> GET    /api/v2/sessions
    hermes sessions stats                       -> GET    /api/v2/sessions/stats
    hermes sessions delete <session-id>         -> DELETE /api/v2/sessions/{session_id}
    hermes sessions prune                       -> POST   /api/v2/sessions/prune
    hermes sessions rename <session-id> <title> -> POST   /api/v2/sessions/{session_id}/rename
    hermes sessions export <output> [--session-id ID]
                                                -> POST   /api/v2/sessions/export
"""
from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from hermes_mgmt.config import Settings
from hermes_mgmt.deps import get_settings_dep, require_auth
from hermes_mgmt.models import ApiResponse
from hermes_mgmt.routes.v2._base import cli_payload, raise_for_exit_code, run_for
from hermes_mgmt.routes.v2._parsers import parse_sessions_list, parse_sessions_stats

router = APIRouter(
    prefix="/api/v2/sessions",
    tags=["v2:sessions"],
    dependencies=[Depends(require_auth)],
)


_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def _check_id(session_id: str) -> None:
    if not _ID_RE.match(session_id):
        raise HTTPException(
            status_code=422,
            detail="session_id must match ^[A-Za-z0-9_.-]{1,128}$",
        )


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=256)


class ExportRequest(BaseModel):
    # Output path on the server. Constrained to live under HERMES_HOME so a
    # malicious caller cannot write arbitrary files (e.g. /etc/passwd).
    output: str = Field(min_length=1, max_length=256)
    session_id: str | None = Field(default=None, max_length=128)


@router.get("", response_model=ApiResponse)
async def list_sessions(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "sessions", ["list"])
    raise_for_exit_code(result, "hermes sessions list failed")
    return ApiResponse(ok=True, data=cli_payload(result, parse_sessions_list))


@router.get("/stats", response_model=ApiResponse)
async def stats(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "sessions", ["stats"])
    raise_for_exit_code(result, "hermes sessions stats failed")
    return ApiResponse(ok=True, data=cli_payload(result, parse_sessions_stats))


@router.delete("/{session_id}", response_model=ApiResponse)
async def delete_session(
    session_id: str,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_id(session_id)
    result = await run_for(settings, "sessions", ["delete", session_id])
    raise_for_exit_code(result, f"hermes sessions delete {session_id} failed")
    return ApiResponse(
        ok=True, data={"session_id": session_id, **cli_payload(result)}
    )


@router.post("/prune", response_model=ApiResponse)
async def prune(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "sessions", ["prune"], timeout=60)
    raise_for_exit_code(result, "hermes sessions prune failed")
    return ApiResponse(ok=True, data=cli_payload(result))


@router.post("/{session_id}/rename", response_model=ApiResponse)
async def rename(
    session_id: str,
    body: RenameRequest,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_id(session_id)
    result = await run_for(
        settings, "sessions", ["rename", session_id, body.title]
    )
    raise_for_exit_code(result, f"hermes sessions rename {session_id} failed")
    return ApiResponse(
        ok=True,
        data={"session_id": session_id, "title": body.title, **cli_payload(result)},
    )


@router.post("/export", response_model=ApiResponse)
async def export_sessions(
    body: ExportRequest,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    # Constrain output to within HERMES_HOME — caller passes a relative or
    # absolute path; we resolve against hermes_home and reject escapes.
    from pathlib import Path

    candidate = (settings.hermes_home / body.output).resolve()
    try:
        candidate.relative_to(settings.hermes_home.resolve())
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"output must resolve under {settings.hermes_home}",
        ) from exc

    args = ["export", str(candidate)]
    if body.session_id:
        _check_id(body.session_id)
        args.extend(["--session-id", body.session_id])

    result = await run_for(settings, "sessions", args, timeout=120)
    raise_for_exit_code(result, "hermes sessions export failed")
    return ApiResponse(
        ok=True,
        data={"output": str(candidate), "session_id": body.session_id, **cli_payload(result)},
    )
