from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import time
from typing import Annotated

import psutil
from fastapi import APIRouter, BackgroundTasks, Depends

from hermes_mgmt import __version__
from hermes_mgmt.cli_runner import run_hermes
from hermes_mgmt.config import Settings
from hermes_mgmt.deps import get_settings_dep, require_auth
from hermes_mgmt.env_file import set_env
from hermes_mgmt.models import (
    ApiResponse,
    DomainRequest,
    InfoResponse,
    ServiceStatus,
    StatusResponse,
    SystemMetrics,
)
from hermes_mgmt.systemd_ctl import active_since, is_active, restart, sub_state

logger = logging.getLogger(__name__)

router = APIRouter(tags=["status"], dependencies=[Depends(require_auth)])


def _resolve_public_ip(fallback: str) -> str:
    """Best-effort: report the IP a remote client would reach this VPS on.

    Order: first non-loopback IPv4 from `hostname -I` → settings default.
    Avoids 127.0.0.1 leaking out when .env didn't preseed HERMES_DROPLET_IP.
    """
    try:
        out = subprocess.check_output(["hostname", "-I"], text=True, timeout=2)
        for ip in out.strip().split():
            if "." in ip and not ip.startswith("127."):
                return ip
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass
    return fallback


@router.get("/api/info", response_model=ApiResponse)
async def get_info(settings: Annotated[Settings, Depends(get_settings_dep)]) -> ApiResponse:
    version_result = await run_hermes("version", [])
    hermes_ver = version_result.stdout.strip() or "unknown"
    public_ip = _resolve_public_ip(settings.droplet_ip)
    # When HERMES_AUTH_TOKEN is set, dashboard_url is the one-click link Caddy
    # consumes (`?token=…` -> sets 30-day cookie -> redirects to /). Otherwise
    # fall back to the bare URL.
    if settings.auth_token:
        dashboard_url = f"https://{settings.domain}/?token={settings.auth_token}"
    else:
        dashboard_url = f"https://{settings.domain}/"
    return ApiResponse(
        ok=True,
        data=InfoResponse(
            domain=settings.domain,
            ip=public_ip,
            hermes_version=hermes_ver,
            mgmt_version=__version__,
            dashboard_url=dashboard_url,
            auth_token=settings.auth_token or None,
        ).model_dump(),
    )


@router.get("/api/status", response_model=ApiResponse)
async def get_status(settings: Annotated[Settings, Depends(get_settings_dep)]) -> ApiResponse:
    allowed = settings.allowed_services

    async def fetch_service(name: str) -> ServiceStatus:
        active, state, since = await asyncio.gather(
            is_active(name, allowed),
            sub_state(name, allowed),
            active_since(name, allowed),
        )
        return ServiceStatus(name=name, active=active, sub_state=state, since=since)

    services = await asyncio.gather(*[fetch_service(svc) for svc in allowed])
    return ApiResponse(ok=True, data=StatusResponse(services=list(services)).model_dump())


@router.get("/api/version", response_model=ApiResponse)
async def get_version() -> ApiResponse:
    result = await run_hermes("version", [])
    return ApiResponse(ok=True, data={"version": result.stdout.strip()})


@router.get("/api/system", response_model=ApiResponse)
async def get_system_metrics() -> ApiResponse:
    cpu = psutil.cpu_percent(interval=0.2)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    boot_time = psutil.boot_time()
    uptime = time.time() - boot_time
    load = list(os.getloadavg())

    metrics = SystemMetrics(
        cpu_percent=cpu,
        memory={
            "total": mem.total,
            "available": mem.available,
            "used": mem.used,
            "percent": mem.percent,
        },
        disk={
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
            "percent": disk.percent,
        },
        uptime_seconds=uptime,
        load_avg=load,
    )
    return ApiResponse(ok=True, data=metrics.model_dump())


@router.get("/api/domain", response_model=ApiResponse)
async def get_domain(settings: Annotated[Settings, Depends(get_settings_dep)]) -> ApiResponse:
    return ApiResponse(ok=True, data={"domain": settings.domain})


@router.put("/api/domain", response_model=ApiResponse)
async def set_domain(
    body: DomainRequest,
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    set_env(settings.env_file, "DOMAIN", body.domain)

    async def restart_caddy() -> None:
        try:
            await restart("caddy", settings.allowed_services)
        except Exception as exc:
            logger.error("Failed to restart caddy: %s", exc)

    background_tasks.add_task(restart_caddy)
    return ApiResponse(ok=True, data={"domain": body.domain})
