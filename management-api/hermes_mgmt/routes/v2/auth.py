"""v2 auth endpoints — wrap `hermes auth <action>`.

CLI surface:
    hermes auth list [provider]                 -> GET    /api/v2/auth
                                                   GET    /api/v2/auth/{provider}
    hermes auth add <provider> --api-key <key>  -> POST   /api/v2/auth/{provider}/api-key
    hermes auth add <provider> --type oauth     -> POST   /api/v2/auth/{provider}/oauth
    hermes auth remove <provider> <index>       -> DELETE /api/v2/auth/{provider}/{index}
    hermes auth reset <provider>                -> POST   /api/v2/auth/{provider}/reset
    hermes auth status <provider>               -> GET    /api/v2/auth/{provider}/status
    hermes auth logout <provider>               -> POST   /api/v2/auth/{provider}/logout
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
    prefix="/api/v2/auth",
    tags=["v2:auth"],
    dependencies=[Depends(require_auth)],
)


_PROVIDER_RE = re.compile(r"^[a-z0-9_-]+$")


def _check_provider(provider: str) -> None:
    if not _PROVIDER_RE.match(provider):
        raise HTTPException(
            status_code=422,
            detail=f"provider must match ^[a-z0-9_-]+$, got {provider!r}",
        )


class AuthAddApiKeyRequest(BaseModel):
    api_key: str = Field(min_length=1, max_length=4096)


@router.get("", response_model=ApiResponse)
async def list_all(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "auth", ["list"])
    raise_for_exit_code(result, "hermes auth list failed")
    return ApiResponse(ok=True, data=cli_payload(result))


@router.get("/{provider}", response_model=ApiResponse)
async def list_provider(
    provider: str,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_provider(provider)
    result = await run_for(settings, "auth", ["list", provider])
    raise_for_exit_code(result, f"hermes auth list {provider} failed")
    return ApiResponse(ok=True, data={"provider": provider, **cli_payload(result)})


@router.post("/{provider}/api-key", response_model=ApiResponse)
async def add_api_key(
    provider: str,
    body: AuthAddApiKeyRequest,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_provider(provider)
    result = await run_for(
        settings, "auth", ["add", provider, "--api-key", body.api_key]
    )
    raise_for_exit_code(result, f"hermes auth add {provider} failed")
    return ApiResponse(ok=True, data={"provider": provider, **cli_payload(result)})


@router.post("/{provider}/oauth", response_model=ApiResponse)
async def add_oauth(
    provider: str,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    """Trigger OAuth flow for a provider. The CLI prompts interactively;
    this endpoint just kicks it off and returns the CLI prompt/URL via stdout."""
    _check_provider(provider)
    result = await run_for(
        settings, "auth", ["add", provider, "--type", "oauth"], timeout=60
    )
    return ApiResponse(
        ok=result.exit_code == 0,
        data={"provider": provider, **cli_payload(result)},
    )


@router.delete("/{provider}/{index}", response_model=ApiResponse)
async def remove(
    provider: str,
    index: int,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_provider(provider)
    if index < 0:
        raise HTTPException(status_code=422, detail="index must be >= 0")
    result = await run_for(settings, "auth", ["remove", provider, str(index)])
    raise_for_exit_code(result, f"hermes auth remove {provider} {index} failed")
    return ApiResponse(
        ok=True, data={"provider": provider, "index": index, **cli_payload(result)}
    )


@router.post("/{provider}/reset", response_model=ApiResponse)
async def reset(
    provider: str,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_provider(provider)
    result = await run_for(settings, "auth", ["reset", provider])
    raise_for_exit_code(result, f"hermes auth reset {provider} failed")
    return ApiResponse(ok=True, data={"provider": provider, **cli_payload(result)})


@router.get("/{provider}/status", response_model=ApiResponse)
async def status_(
    provider: str,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_provider(provider)
    result = await run_for(settings, "auth", ["status", provider])
    raise_for_exit_code(result, f"hermes auth status {provider} failed")
    return ApiResponse(ok=True, data={"provider": provider, **cli_payload(result)})


@router.post("/{provider}/logout", response_model=ApiResponse)
async def logout(
    provider: str,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_provider(provider)
    result = await run_for(settings, "auth", ["logout", provider])
    raise_for_exit_code(result, f"hermes auth logout {provider} failed")
    return ApiResponse(ok=True, data={"provider": provider, **cli_payload(result)})
