"""v2 fallback endpoints — wrap `hermes fallback <action>`.

CLI surface:
    hermes fallback list    -> GET    /api/v2/fallback
    hermes fallback add     -> POST   /api/v2/fallback
    hermes fallback remove  -> DELETE /api/v2/fallback/{provider}
    hermes fallback clear   -> DELETE /api/v2/fallback
"""
from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from hermes_mgmt.config import Settings
from hermes_mgmt.deps import get_settings_dep, require_auth
from hermes_mgmt.models import ApiResponse
from hermes_mgmt.routes.v2._base import cli_payload, raise_for_exit_code, run_for

router = APIRouter(
    prefix="/api/v2/fallback",
    tags=["v2:fallback"],
    dependencies=[Depends(require_auth)],
)


_PROVIDER_RE = re.compile(r"^[a-z0-9_-]+$")


class FallbackAddRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    model: str | None = Field(default=None, max_length=256)

    @field_validator("provider")
    @classmethod
    def _provider(cls, v: str) -> str:
        if not _PROVIDER_RE.match(v):
            raise ValueError("provider must match ^[a-z0-9_-]+$")
        return v


@router.get("", response_model=ApiResponse)
async def list_fallback(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "fallback", ["list"])
    raise_for_exit_code(result, "hermes fallback list failed")
    return ApiResponse(ok=True, data=cli_payload(result))


@router.post("", response_model=ApiResponse)
async def add_fallback(
    body: FallbackAddRequest,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    args = ["add", body.provider]
    if body.model:
        args.append(body.model)
    result = await run_for(settings, "fallback", args)
    raise_for_exit_code(result, f"hermes fallback add {body.provider} failed")
    return ApiResponse(
        ok=True,
        data={"provider": body.provider, "model": body.model, **cli_payload(result)},
    )


@router.delete("/{provider}", response_model=ApiResponse)
async def remove_fallback(
    provider: str,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    if not _PROVIDER_RE.match(provider):
        raise HTTPException(
            status_code=422,
            detail=f"provider must match ^[a-z0-9_-]+$, got {provider!r}",
        )
    result = await run_for(settings, "fallback", ["remove", provider])
    raise_for_exit_code(result, f"hermes fallback remove {provider} failed")
    return ApiResponse(ok=True, data={"provider": provider, **cli_payload(result)})


@router.delete("", response_model=ApiResponse)
async def clear_fallback(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "fallback", ["clear"])
    raise_for_exit_code(result, "hermes fallback clear failed")
    return ApiResponse(ok=True, data=cli_payload(result))
