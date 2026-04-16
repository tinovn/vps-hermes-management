from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, status

from hermes_mgmt.config import Settings
from hermes_mgmt.deps import get_settings_dep, require_auth
from hermes_mgmt.models import ApiResponse
from hermes_mgmt.systemd_ctl import restart, start, stop, systemctl

logger = logging.getLogger(__name__)

router = APIRouter(tags=["control"], dependencies=[Depends(require_auth)])

_HERMES_TARGET = "hermes-gateway"
_VENV_UV = "/opt/hermes/hermes-agent/.venv/bin/uv"
_HERMES_AGENT_DIR = "/opt/hermes/hermes-agent"
_HERMES_EXTRAS = "[web,messaging,cron,voice,mcp,honcho]"


@router.post("/api/restart", response_model=ApiResponse)
async def restart_hermes(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    allowed = settings.allowed_services
    results: dict[str, str] = {}
    for svc in ("hermes-gateway", "hermes-dashboard"):
        if svc in allowed:
            code, msg = await restart(svc, allowed)
            results[svc] = "ok" if code == 0 else msg
    return ApiResponse(ok=True, data=results)


@router.post("/api/stop", response_model=ApiResponse)
async def stop_hermes(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    allowed = settings.allowed_services
    results: dict[str, str] = {}
    for svc in ("hermes-gateway", "hermes-dashboard"):
        if svc in allowed:
            code, msg = await stop(svc, allowed)
            results[svc] = "ok" if code == 0 else msg
    return ApiResponse(ok=True, data=results)


@router.post("/api/start", response_model=ApiResponse)
async def start_hermes(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    allowed = settings.allowed_services
    results: dict[str, str] = {}
    for svc in ("hermes-gateway", "hermes-dashboard"):
        if svc in allowed:
            code, msg = await start(svc, allowed)
            results[svc] = "ok" if code == 0 else msg
    return ApiResponse(ok=True, data=results)


@router.post("/api/rebuild", response_model=ApiResponse)
async def rebuild_hermes(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    allowed = settings.allowed_services
    results: dict[str, str] = {}
    for svc in ("hermes-gateway", "hermes-dashboard", "caddy"):
        if svc in allowed:
            code, msg = await restart(svc, allowed)
            results[svc] = "ok" if code == 0 else msg
    return ApiResponse(ok=True, data=results)


async def _do_upgrade(settings: Settings) -> None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", _HERMES_AGENT_DIR, "pull",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await proc.communicate()
        logger.info("git pull: %s %s", stdout_b.decode(errors="replace"), stderr_b.decode(errors="replace"))

        uv_bin = _VENV_UV
        if not Path(uv_bin).exists():
            uv_bin = shutil.which("uv") or "uv"
        proc2 = await asyncio.create_subprocess_exec(
            uv_bin, "pip", "install", "-e", f".{_HERMES_EXTRAS}",
            cwd=_HERMES_AGENT_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b2, stderr_b2 = await proc2.communicate()
        logger.info("uv install: %s %s", stdout_b2.decode(errors="replace"), stderr_b2.decode(errors="replace"))

        allowed = settings.allowed_services
        for svc in ("hermes-gateway", "hermes-dashboard"):
            if svc in allowed:
                await restart(svc, allowed)
        logger.info("Upgrade complete.")
    except Exception as exc:
        logger.error("Upgrade failed: %s", exc)


@router.post("/api/upgrade", status_code=status.HTTP_202_ACCEPTED, response_model=ApiResponse)
async def upgrade_hermes(
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    background_tasks.add_task(_do_upgrade, settings)
    return ApiResponse(ok=True, data={"message": "Upgrade started in background"})


@router.post("/api/reset", response_model=ApiResponse)
async def reset_hermes(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    body: dict = Body(...),
) -> ApiResponse:
    if body.get("confirm") != "RESET":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Must send {"confirm": "RESET"} to proceed.',
        )

    hermes_home = settings.hermes_home
    data_dir = settings.install_dir / "data"

    # Stop services first
    allowed = settings.allowed_services
    for svc in ("hermes-gateway", "hermes-dashboard"):
        if svc in allowed:
            try:
                await stop(svc, allowed)
            except Exception as exc:
                logger.warning("Could not stop %s: %s", svc, exc)

    # Wipe directories
    for target in (hermes_home, data_dir):
        if target.exists():
            try:
                shutil.rmtree(target)
                logger.info("Removed %s", target)
            except Exception as exc:
                logger.error("Failed to remove %s: %s", target, exc)

    # Attempt non-interactive hermes setup
    try:
        proc = await asyncio.create_subprocess_exec(
            "/usr/local/bin/hermes", "config", "show",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.communicate(), timeout=15)
    except Exception:
        pass

    # Restart services
    for svc in ("hermes-gateway", "hermes-dashboard"):
        if svc in allowed:
            try:
                await start(svc, allowed)
            except Exception as exc:
                logger.warning("Could not start %s: %s", svc, exc)

    return ApiResponse(ok=True, data={"message": "Reset complete. Services restarted."})
