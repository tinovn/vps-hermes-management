"""v2 skills endpoints — wrap `hermes skills <action>`.

CLI surface:
    hermes skills install <identifier>  -> POST   /api/v2/skills/install
    hermes skills uninstall <name>      -> DELETE /api/v2/skills/{name}
    hermes skills list                  -> GET    /api/v2/skills
    hermes skills check                 -> POST   /api/v2/skills/check
    hermes skills update                -> POST   /api/v2/skills/update
    hermes skills reset <name>          -> POST   /api/v2/skills/{name}/reset
    hermes skills search <query>        -> GET    /api/v2/skills/search?q=...
    hermes skills inspect <identifier>  -> GET    /api/v2/skills/inspect?identifier=...
"""
from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from hermes_mgmt.config import Settings
from hermes_mgmt.deps import get_settings_dep, require_auth
from hermes_mgmt.models import ApiResponse
from hermes_mgmt.routes.v2._base import cli_payload, raise_for_exit_code, run_for
from hermes_mgmt.routes.v2._parsers import parse_skills_list

router = APIRouter(
    prefix="/api/v2/skills",
    tags=["v2:skills"],
    dependencies=[Depends(require_auth)],
)


# Skill identifiers can be hub-style (`hub/name`), tap-style (`tap:name`),
# or path-style. Keep validation permissive but reject shell metacharacters.
_IDENT_RE = re.compile(r"^[A-Za-z0-9_./@:-]{1,256}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def _check_ident(s: str, label: str = "identifier") -> None:
    if not _IDENT_RE.match(s):
        raise HTTPException(
            status_code=422,
            detail=f"{label} must match ^[A-Za-z0-9_./@:-]{{1,256}}$",
        )


def _check_name(name: str) -> None:
    if not _NAME_RE.match(name):
        raise HTTPException(
            status_code=422,
            detail="name must match ^[A-Za-z0-9_.-]{1,128}$",
        )


class InstallRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=256)


@router.get("", response_model=ApiResponse)
async def list_skills(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "skills", ["list"])
    raise_for_exit_code(result, "hermes skills list failed")
    return ApiResponse(ok=True, data=cli_payload(result, parse_skills_list))


@router.post("/install", response_model=ApiResponse)
async def install(
    body: InstallRequest,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_ident(body.identifier)
    result = await run_for(settings, "skills", ["install", body.identifier], timeout=120)
    raise_for_exit_code(result, f"hermes skills install {body.identifier} failed")
    return ApiResponse(
        ok=True, data={"identifier": body.identifier, **cli_payload(result)}
    )


@router.delete("/{name}", response_model=ApiResponse)
async def uninstall(
    name: str,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_name(name)
    result = await run_for(settings, "skills", ["uninstall", name])
    raise_for_exit_code(result, f"hermes skills uninstall {name} failed")
    return ApiResponse(ok=True, data={"name": name, **cli_payload(result)})


@router.post("/check", response_model=ApiResponse)
async def check(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "skills", ["check"], timeout=60)
    return ApiResponse(ok=result.exit_code == 0, data=cli_payload(result))


@router.post("/update", response_model=ApiResponse)
async def update(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "skills", ["update"], timeout=180)
    raise_for_exit_code(result, "hermes skills update failed")
    return ApiResponse(ok=True, data=cli_payload(result))


@router.post("/{name}/reset", response_model=ApiResponse)
async def reset(
    name: str,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_name(name)
    result = await run_for(settings, "skills", ["reset", name])
    raise_for_exit_code(result, f"hermes skills reset {name} failed")
    return ApiResponse(ok=True, data={"name": name, **cli_payload(result)})


@router.get("/search", response_model=ApiResponse)
async def search(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    q: str = Query(min_length=1, max_length=256),
) -> ApiResponse:
    result = await run_for(settings, "skills", ["search", q], timeout=60)
    raise_for_exit_code(result, f"hermes skills search {q!r} failed")
    return ApiResponse(ok=True, data={"query": q, **cli_payload(result)})


@router.get("/inspect", response_model=ApiResponse)
async def inspect(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    identifier: str = Query(min_length=1, max_length=256),
) -> ApiResponse:
    _check_ident(identifier)
    result = await run_for(settings, "skills", ["inspect", identifier])
    raise_for_exit_code(result, f"hermes skills inspect {identifier} failed")
    return ApiResponse(
        ok=True, data={"identifier": identifier, **cli_payload(result)}
    )
