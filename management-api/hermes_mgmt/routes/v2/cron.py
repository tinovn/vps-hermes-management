"""v2 cron endpoints — wrap `hermes cron <action>`.

CLI surface:
    hermes cron list           -> GET    /api/v2/cron
    hermes cron create ...     -> POST   /api/v2/cron
    hermes cron edit <id>      -> PATCH  /api/v2/cron/{job_id}
    hermes cron pause <id>     -> POST   /api/v2/cron/{job_id}/pause
    hermes cron resume <id>    -> POST   /api/v2/cron/{job_id}/resume
    hermes cron remove <id>    -> DELETE /api/v2/cron/{job_id}
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
from hermes_mgmt.routes.v2._parsers import parse_cron_list

router = APIRouter(
    prefix="/api/v2/cron",
    tags=["v2:cron"],
    dependencies=[Depends(require_auth)],
)


_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def _check_id(job_id: str) -> None:
    if not _ID_RE.match(job_id):
        raise HTTPException(
            status_code=422,
            detail="job_id must match ^[A-Za-z0-9_.-]{1,128}$",
        )


class CronCreateRequest(BaseModel):
    spec: str = Field(min_length=1, max_length=128)
    prompt: str = Field(min_length=1, max_length=4096)
    name: str | None = Field(default=None, max_length=128)


class CronEditRequest(BaseModel):
    spec: str | None = Field(default=None, max_length=128)
    prompt: str | None = Field(default=None, max_length=4096)
    name: str | None = Field(default=None, max_length=128)


@router.get("", response_model=ApiResponse)
async def list_jobs(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "cron", ["list"])
    raise_for_exit_code(result, "hermes cron list failed")
    return ApiResponse(ok=True, data=cli_payload(result, parse_cron_list))


@router.post("", response_model=ApiResponse)
async def create(
    body: CronCreateRequest,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    args = ["create", "--spec", body.spec, "--prompt", body.prompt]
    if body.name:
        args.extend(["--name", body.name])
    result = await run_for(settings, "cron", args)
    raise_for_exit_code(result, "hermes cron create failed")
    return ApiResponse(ok=True, data=cli_payload(result))


@router.patch("/{job_id}", response_model=ApiResponse)
async def edit(
    job_id: str,
    body: CronEditRequest,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_id(job_id)
    args = ["edit", job_id]
    if body.spec:
        args.extend(["--spec", body.spec])
    if body.prompt:
        args.extend(["--prompt", body.prompt])
    if body.name:
        args.extend(["--name", body.name])
    if len(args) == 2:
        raise HTTPException(status_code=422, detail="at least one of spec/prompt/name required")
    result = await run_for(settings, "cron", args)
    raise_for_exit_code(result, f"hermes cron edit {job_id} failed")
    return ApiResponse(ok=True, data={"job_id": job_id, **cli_payload(result)})


@router.post("/{job_id}/pause", response_model=ApiResponse)
async def pause(
    job_id: str,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_id(job_id)
    result = await run_for(settings, "cron", ["pause", job_id])
    raise_for_exit_code(result, f"hermes cron pause {job_id} failed")
    return ApiResponse(ok=True, data={"job_id": job_id, **cli_payload(result)})


@router.post("/{job_id}/resume", response_model=ApiResponse)
async def resume(
    job_id: str,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_id(job_id)
    result = await run_for(settings, "cron", ["resume", job_id])
    raise_for_exit_code(result, f"hermes cron resume {job_id} failed")
    return ApiResponse(ok=True, data={"job_id": job_id, **cli_payload(result)})


@router.delete("/{job_id}", response_model=ApiResponse)
async def remove(
    job_id: str,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_id(job_id)
    result = await run_for(settings, "cron", ["remove", job_id])
    raise_for_exit_code(result, f"hermes cron remove {job_id} failed")
    return ApiResponse(ok=True, data={"job_id": job_id, **cli_payload(result)})
