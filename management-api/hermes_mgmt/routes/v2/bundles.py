"""v2 bundles endpoints — wrap `hermes bundles <action>`.

CLI surface:
    hermes bundles list                    -> GET    /api/v2/bundles
    hermes bundles create <name> [--skill] -> POST   /api/v2/bundles
    hermes bundles delete <name>           -> DELETE /api/v2/bundles/{name}
    hermes bundles reload                  -> POST   /api/v2/bundles/reload
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
    prefix="/api/v2/bundles",
    tags=["v2:bundles"],
    dependencies=[Depends(require_auth)],
)


_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


class CreateBundleRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    skills: list[str] = Field(default_factory=list, max_length=256)


def _check_name(name: str) -> None:
    if not _NAME_RE.match(name):
        raise HTTPException(
            status_code=422,
            detail="name must match ^[A-Za-z0-9_.-]{1,128}$",
        )


@router.get("", response_model=ApiResponse)
async def list_bundles(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "bundles", ["list"])
    raise_for_exit_code(result, "hermes bundles list failed")
    return ApiResponse(ok=True, data=cli_payload(result))


@router.post("", response_model=ApiResponse)
async def create(
    body: CreateBundleRequest,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_name(body.name)
    args = ["create", body.name]
    for skill in body.skills:
        args.extend(["--skill", skill])
    result = await run_for(settings, "bundles", args)
    raise_for_exit_code(result, f"hermes bundles create {body.name} failed")
    return ApiResponse(
        ok=True, data={"name": body.name, "skills": body.skills, **cli_payload(result)}
    )


@router.delete("/{name}", response_model=ApiResponse)
async def delete_bundle(
    name: str,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_name(name)
    result = await run_for(settings, "bundles", ["delete", name])
    raise_for_exit_code(result, f"hermes bundles delete {name} failed")
    return ApiResponse(ok=True, data={"name": name, **cli_payload(result)})


@router.post("/reload", response_model=ApiResponse)
async def reload(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "bundles", ["reload"])
    raise_for_exit_code(result, "hermes bundles reload failed")
    return ApiResponse(ok=True, data=cli_payload(result))
