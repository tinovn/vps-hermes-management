"""v2 backup / import / checkpoints endpoints.

CLI surface:
    hermes backup [--output] [--quick]   -> POST   /api/v2/backup
    hermes import <zipfile>              -> POST   /api/v2/backup/import
    hermes checkpoints status            -> GET    /api/v2/checkpoints/status
    hermes checkpoints prune             -> POST   /api/v2/checkpoints/prune
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from hermes_mgmt.config import Settings
from hermes_mgmt.deps import get_settings_dep, require_auth
from hermes_mgmt.models import ApiResponse
from hermes_mgmt.routes.v2._base import cli_payload, raise_for_exit_code, run_for
from hermes_mgmt.routes.v2._parsers import parse_checkpoints_status

router = APIRouter(tags=["v2:backup"], dependencies=[Depends(require_auth)])


class BackupRequest(BaseModel):
    output: str | None = Field(default=None, max_length=256)
    quick: bool = False


class ImportRequest(BaseModel):
    zipfile: str = Field(min_length=1, max_length=256)


def _resolve_under_home(rel_or_abs: str, hermes_home: Path, label: str) -> Path:
    candidate = (hermes_home / rel_or_abs).resolve()
    try:
        candidate.relative_to(hermes_home.resolve())
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"{label} must resolve under {hermes_home}",
        ) from exc
    return candidate


@router.post("/api/v2/backup", response_model=ApiResponse)
async def backup(
    body: BackupRequest,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    args = ["backup"] if False else []  # backup is itself the subcommand
    # `hermes backup` is a top-level command, not a subcommand of something.
    # We route via run_hermes("backup", [...])
    cli_args: list[str] = []
    if body.output:
        out = _resolve_under_home(body.output, settings.hermes_home, "output")
        cli_args.extend(["--output", str(out)])
    if body.quick:
        cli_args.append("--quick")
    result = await run_for(settings, "backup", cli_args, timeout=600)
    raise_for_exit_code(result, "hermes backup failed")
    return ApiResponse(ok=True, data=cli_payload(result))


@router.post("/api/v2/backup/import", response_model=ApiResponse)
async def import_backup(
    body: ImportRequest,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    zip_path = _resolve_under_home(body.zipfile, settings.hermes_home, "zipfile")
    if not zip_path.exists():
        raise HTTPException(status_code=404, detail=f"zipfile not found: {zip_path}")
    result = await run_for(settings, "import", [str(zip_path)], timeout=600)
    raise_for_exit_code(result, "hermes import failed")
    return ApiResponse(ok=True, data={"zipfile": str(zip_path), **cli_payload(result)})


@router.get("/api/v2/checkpoints/status", response_model=ApiResponse)
async def checkpoints_status(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "checkpoints", ["status"])
    raise_for_exit_code(result, "hermes checkpoints status failed")
    return ApiResponse(ok=True, data=cli_payload(result, parse_checkpoints_status))


@router.post("/api/v2/checkpoints/prune", response_model=ApiResponse)
async def checkpoints_prune(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "checkpoints", ["prune"], timeout=120)
    raise_for_exit_code(result, "hermes checkpoints prune failed")
    return ApiResponse(ok=True, data=cli_payload(result))
