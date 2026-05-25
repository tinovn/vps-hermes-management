"""v2 curator endpoints — wrap `hermes curator <action>`.

CLI surface:
    hermes curator status            -> GET    /api/v2/curator/status
    hermes curator run               -> POST   /api/v2/curator/run
    hermes curator backup            -> POST   /api/v2/curator/backup
    hermes curator rollback          -> POST   /api/v2/curator/rollback
    hermes curator pin <skill>       -> POST   /api/v2/curator/{skill}/pin
    hermes curator unpin <skill>     -> POST   /api/v2/curator/{skill}/unpin
    hermes curator archive <skill>   -> POST   /api/v2/curator/{skill}/archive
"""
from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from hermes_mgmt.config import Settings
from hermes_mgmt.deps import get_settings_dep, require_auth
from hermes_mgmt.models import ApiResponse
from hermes_mgmt.routes.v2._base import cli_payload, raise_for_exit_code, run_for

router = APIRouter(
    prefix="/api/v2/curator",
    tags=["v2:curator"],
    dependencies=[Depends(require_auth)],
)


_SKILL_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def _check_skill(skill: str) -> None:
    if not _SKILL_RE.match(skill):
        raise HTTPException(
            status_code=422,
            detail="skill must match ^[A-Za-z0-9_.-]{1,128}$",
        )


@router.get("/status", response_model=ApiResponse)
async def status_(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "curator", ["status"])
    raise_for_exit_code(result, "hermes curator status failed")
    return ApiResponse(ok=True, data=cli_payload(result))


@router.post("/run", response_model=ApiResponse)
async def run(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "curator", ["run"], timeout=300)
    raise_for_exit_code(result, "hermes curator run failed")
    return ApiResponse(ok=True, data=cli_payload(result))


@router.post("/backup", response_model=ApiResponse)
async def backup(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "curator", ["backup"], timeout=120)
    raise_for_exit_code(result, "hermes curator backup failed")
    return ApiResponse(ok=True, data=cli_payload(result))


@router.post("/rollback", response_model=ApiResponse)
async def rollback(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "curator", ["rollback"], timeout=120)
    raise_for_exit_code(result, "hermes curator rollback failed")
    return ApiResponse(ok=True, data=cli_payload(result))


@router.post("/{skill}/pin", response_model=ApiResponse)
async def pin(
    skill: str,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_skill(skill)
    result = await run_for(settings, "curator", ["pin", skill])
    raise_for_exit_code(result, f"hermes curator pin {skill} failed")
    return ApiResponse(ok=True, data={"skill": skill, **cli_payload(result)})


@router.post("/{skill}/unpin", response_model=ApiResponse)
async def unpin(
    skill: str,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_skill(skill)
    result = await run_for(settings, "curator", ["unpin", skill])
    raise_for_exit_code(result, f"hermes curator unpin {skill} failed")
    return ApiResponse(ok=True, data={"skill": skill, **cli_payload(result)})


@router.post("/{skill}/archive", response_model=ApiResponse)
async def archive(
    skill: str,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_skill(skill)
    result = await run_for(settings, "curator", ["archive", skill])
    raise_for_exit_code(result, f"hermes curator archive {skill} failed")
    return ApiResponse(ok=True, data={"skill": skill, **cli_payload(result)})
