"""v2 webhook endpoints — wrap `hermes webhook <action>`.

CLI surface:
    hermes webhook list                              -> GET    /api/v2/webhook
    hermes webhook subscribe <name> [--prompt ...]   -> POST   /api/v2/webhook
    hermes webhook remove <name>                     -> DELETE /api/v2/webhook/{name}
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
from hermes_mgmt.routes.v2._parsers import parse_webhook_list

router = APIRouter(
    prefix="/api/v2/webhook",
    tags=["v2:webhook"],
    dependencies=[Depends(require_auth)],
)


_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def _check_name(name: str) -> None:
    if not _NAME_RE.match(name):
        raise HTTPException(
            status_code=422,
            detail="name must match ^[A-Za-z0-9_.-]{1,128}$",
        )


class SubscribeRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    prompt: str | None = Field(default=None, max_length=4096)
    events: list[str] = Field(default_factory=list, max_length=64)
    skills: list[str] = Field(default_factory=list, max_length=128)


@router.get("", response_model=ApiResponse)
async def list_webhooks(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "webhook", ["list"])
    raise_for_exit_code(result, "hermes webhook list failed")
    return ApiResponse(ok=True, data=cli_payload(result, parse_webhook_list))


@router.post("", response_model=ApiResponse)
async def subscribe(
    body: SubscribeRequest,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_name(body.name)
    args = ["subscribe", body.name]
    if body.prompt:
        args.extend(["--prompt", body.prompt])
    for ev in body.events:
        args.extend(["--events", ev])
    for sk in body.skills:
        args.extend(["--skills", sk])
    result = await run_for(settings, "webhook", args)
    raise_for_exit_code(result, f"hermes webhook subscribe {body.name} failed")
    return ApiResponse(ok=True, data={"name": body.name, **cli_payload(result)})


@router.delete("/{name}", response_model=ApiResponse)
async def remove(
    name: str,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_name(name)
    result = await run_for(settings, "webhook", ["remove", name])
    raise_for_exit_code(result, f"hermes webhook remove {name} failed")
    return ApiResponse(ok=True, data={"name": name, **cli_payload(result)})
