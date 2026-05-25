"""v2 profile endpoints — wrap `hermes profile <action>`.

CLI surface:
    hermes profile create <name> [--clone]    -> POST   /api/v2/profile
    hermes profile delete <name>              -> DELETE /api/v2/profile/{name}
    hermes profile use <name>                 -> POST   /api/v2/profile/{name}/use
    hermes profile rename <old> <new>         -> POST   /api/v2/profile/{old}/rename
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

router = APIRouter(
    prefix="/api/v2/profile",
    tags=["v2:profile"],
    dependencies=[Depends(require_auth)],
)


_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _check_name(name: str) -> None:
    if not _NAME_RE.match(name):
        raise HTTPException(
            status_code=422,
            detail="name must match ^[A-Za-z0-9_.-]{1,64}$",
        )


class CreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    clone: str | None = Field(default=None, max_length=64)


class RenameRequest(BaseModel):
    new_name: str = Field(min_length=1, max_length=64)


@router.post("", response_model=ApiResponse)
async def create(
    body: CreateRequest,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_name(body.name)
    if body.clone:
        _check_name(body.clone)
    args = ["create", body.name]
    if body.clone:
        args.extend(["--clone", body.clone])
    result = await run_for(settings, "profile", args)
    raise_for_exit_code(result, f"hermes profile create {body.name} failed")
    return ApiResponse(ok=True, data={"name": body.name, **cli_payload(result)})


@router.delete("/{name}", response_model=ApiResponse)
async def delete(
    name: str,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_name(name)
    result = await run_for(settings, "profile", ["delete", name])
    raise_for_exit_code(result, f"hermes profile delete {name} failed")
    return ApiResponse(ok=True, data={"name": name, **cli_payload(result)})


@router.post("/{name}/use", response_model=ApiResponse)
async def use(
    name: str,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_name(name)
    result = await run_for(settings, "profile", ["use", name])
    raise_for_exit_code(result, f"hermes profile use {name} failed")
    return ApiResponse(ok=True, data={"name": name, **cli_payload(result)})


@router.post("/{name}/rename", response_model=ApiResponse)
async def rename(
    name: str,
    body: RenameRequest,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_name(name)
    _check_name(body.new_name)
    result = await run_for(settings, "profile", ["rename", name, body.new_name])
    raise_for_exit_code(result, f"hermes profile rename {name} -> {body.new_name} failed")
    return ApiResponse(
        ok=True,
        data={"old": name, "new": body.new_name, **cli_payload(result)},
    )
