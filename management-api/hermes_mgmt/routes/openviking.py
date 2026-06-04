"""OpenViking — optional context-database / memory backend lifecycle API.

OpenViking (github.com/volcengine/OpenViking) is NOT installed by default. These
routes let the dashboard install / configure / enable / disable / uninstall it
on demand, so users decide whether to pay the RAM cost. The install + uninstall
shell scripts live in the repo's scripts/ and are fetched from the canonical raw
URL at call time (same pattern as /api/upgrade-mgmt).

Lifecycle (dashboard drives this top-to-bottom):
  status     → is it installed? service active? healthy? wired into Hermes?
  install    → run install-openviking.sh (venv + pip + config + systemd unit)
  config     → GET current (masked) / PUT embedding + VLM keys into ov.conf
  test-key   → validate an LLM key against the provider before saving
  enable     → start service + set OPENVIKING_ENDPOINT in .env + restart gateway
  disable    → stop service + remove OPENVIKING_ENDPOINT + restart gateway
  restart    → restart the OpenViking service
  upgrade    → pip install --upgrade openviking + restart
  stats      → memory/data-dir/uptime summary
  uninstall  → run uninstall-openviking.sh
  logs       → journal tail of hermes-openviking.service
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, status

from hermes_mgmt.config import Settings
from hermes_mgmt.deps import get_settings_dep, require_auth
from hermes_mgmt.env_file import delete_env, mask_value, read_env, set_env
from hermes_mgmt.models import ApiResponse
from hermes_mgmt.systemd_ctl import (
    active_since,
    is_active,
    journal_tail,
    restart,
    start,
    stop,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["openviking"], dependencies=[Depends(require_auth)])

_SERVICE = "hermes-openviking"
_OV_DIR = "/opt/hermes-openviking"
_OV_BIN = f"{_OV_DIR}/.venv/bin/openviking-server"
_OV_VENV_UV = f"{_OV_DIR}/.venv/bin/uv"
_OV_HOME = Path("/root/.openviking")
_OV_CONF = _OV_HOME / "ov.conf"
_OV_PORT = 1933
_OV_ENDPOINT = f"http://127.0.0.1:{_OV_PORT}"
_ENDPOINT_ENV_KEY = "OPENVIKING_ENDPOINT"
_REPO_RAW = "https://raw.githubusercontent.com/tinovn/vps-hermes-management/main"
_INSTALL_SCRIPT = "scripts/install-openviking.sh"
_UNINSTALL_SCRIPT = "scripts/uninstall-openviking.sh"
_HEALTH_TIMEOUT = 4.0


def _is_installed() -> bool:
    return Path(_OV_BIN).exists()


async def _health() -> bool:
    """True if the OpenViking server answers /health with status ok."""
    try:
        async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT) as client:
            resp = await client.get(f"{_OV_ENDPOINT}/health")
        return resp.status_code == 200
    except httpx.RequestError:
        return False


async def _run_script(raw_path: str, *args: str) -> tuple[int, str]:
    """Fetch a repo script to /tmp and run it with bash. Returns (rc, output)."""
    local = f"/tmp/{Path(raw_path).name}"
    fetch = await asyncio.create_subprocess_exec(
        "curl", "-fsSL", f"{_REPO_RAW}/{raw_path}", "-o", local,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, ferr = await fetch.communicate()
    if fetch.returncode != 0:
        return fetch.returncode or 1, f"fetch failed: {ferr.decode(errors='replace')}"

    proc = await asyncio.create_subprocess_exec(
        "bash", local, *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace")


def _config_has_keys() -> bool:
    """True if ov.conf exists and both embedding + vlm api_key are non-empty."""
    if not _OV_CONF.exists():
        return False
    try:
        conf = json.loads(_OV_CONF.read_text(encoding="utf-8"))
        emb = conf.get("embedding", {}).get("dense", {}).get("api_key", "")
        vlm = conf.get("vlm", {}).get("api_key", "")
        return bool(emb) and bool(vlm)
    except (json.JSONDecodeError, OSError):
        return False


# ─── status ──────────────────────────────────────────────────────────────


@router.get("/api/openviking/status", response_model=ApiResponse)
async def ov_status(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    """Full lifecycle state for the dashboard to render the management panel."""
    installed = _is_installed()
    active = await is_active(_SERVICE, settings.allowed_services) if installed else False
    healthy = await _health() if active else False

    merged = read_env(settings.env_file)
    merged.update(read_env(settings.hermes_home / ".env"))
    wired = bool(merged.get(_ENDPOINT_ENV_KEY, "").strip())

    return ApiResponse(
        ok=True,
        data={
            "installed": installed,
            "config_ready": _config_has_keys(),
            "service_active": active,
            "healthy": healthy,
            "wired_into_hermes": wired,
            "endpoint": _OV_ENDPOINT,
        },
    )


# ─── install ───────────────────────────────────────────────────────────────


async def _do_install() -> None:
    rc, out = await _run_script(_INSTALL_SCRIPT)
    if rc != 0:
        logger.error("OpenViking install failed (rc=%d):\n%s", rc, out[-2000:])
    else:
        logger.info("OpenViking install OK")


@router.post(
    "/api/openviking/install",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ApiResponse,
)
async def ov_install(background_tasks: BackgroundTasks) -> ApiResponse:
    """Install OpenViking in the background (venv + pip can take a minute)."""
    if _is_installed():
        return ApiResponse(ok=True, data={"message": "already installed"})
    background_tasks.add_task(_do_install)
    return ApiResponse(
        ok=True,
        data={"message": "Đang cài OpenViking ở chế độ nền, theo dõi qua /status."},
    )


# ─── config ──────────────────────────────────────────────────────────────


@router.post("/api/openviking/config", response_model=ApiResponse)
async def ov_config(
    body: dict = Body(...),
) -> ApiResponse:
    """Write embedding + VLM config into ov.conf.

    Body (all optional; defaults reuse existing values / sensible OpenAI ones):
      { "api_key", "api_base", "embedding_model", "vlm_model",
        "provider", "dimension" }
    A single api_key is applied to both embedding + VLM unless split keys given.
    """
    if not _is_installed():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="OpenViking chưa được cài. Gọi /api/openviking/install trước.",
        )

    api_key = (body.get("api_key") or "").strip()
    api_base = (body.get("api_base") or "https://api.openai.com/v1").strip()
    provider = (body.get("provider") or "openai").strip()
    emb_model = (body.get("embedding_model") or "text-embedding-3-small").strip()
    vlm_model = (body.get("vlm_model") or "gpt-4o-mini").strip()
    dimension = int(body.get("dimension") or 1536)

    # Preserve an existing key if the caller didn't supply one.
    if not api_key and _OV_CONF.exists():
        try:
            existing = json.loads(_OV_CONF.read_text(encoding="utf-8"))
            api_key = existing.get("vlm", {}).get("api_key", "") or existing.get(
                "embedding", {}
            ).get("dense", {}).get("api_key", "")
        except (json.JSONDecodeError, OSError):
            api_key = ""

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Thiếu api_key (cần cho embedding + VLM của OpenViking).",
        )

    conf = {
        "embedding": {
            "dense": {
                "api_base": api_base,
                "api_key": api_key,
                "provider": provider,
                "dimension": dimension,
                "model": emb_model,
                "input": "multimodal",
            }
        },
        "vlm": {
            "api_base": api_base,
            "api_key": api_key,
            "provider": provider,
            "max_retries": 2,
            "model": vlm_model,
        },
    }
    _OV_CONF.parent.mkdir(parents=True, exist_ok=True)
    _OV_CONF.write_text(json.dumps(conf, indent=2), encoding="utf-8")
    _OV_CONF.chmod(0o600)
    logger.info("OpenViking config written to %s", _OV_CONF)
    return ApiResponse(ok=True, data={"config_ready": True})


# ─── enable / disable ──────────────────────────────────────────────────────


@router.post("/api/openviking/enable", response_model=ApiResponse)
async def ov_enable(
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    """Start the service + wire OPENVIKING_ENDPOINT into Hermes + restart gateway."""
    if not _is_installed():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="OpenViking chưa được cài.",
        )
    if not _config_has_keys():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Chưa cấu hình api_key. Gọi /api/openviking/config trước.",
        )

    code, msg = await start(_SERVICE, settings.allowed_services)
    if code != 0:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Không khởi động được {_SERVICE}: {msg}",
        )

    # Wire into Hermes (dual-write like channels.py) + restart gateway so the
    # plugin picks up the env var.
    hermes_env = settings.hermes_home / ".env"
    set_env(hermes_env, _ENDPOINT_ENV_KEY, _OV_ENDPOINT)
    set_env(settings.env_file, _ENDPOINT_ENV_KEY, _OV_ENDPOINT)

    async def _restart_gw() -> None:
        try:
            await restart("hermes-gateway", settings.allowed_services)
        except Exception as exc:
            logger.error("gateway restart after OpenViking enable failed: %s", exc)

    background_tasks.add_task(_restart_gw)
    return ApiResponse(ok=True, data={"enabled": True, "endpoint": _OV_ENDPOINT})


@router.post("/api/openviking/disable", response_model=ApiResponse)
async def ov_disable(
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    """Stop the service + unwire from Hermes + restart gateway. Keeps install."""
    await stop(_SERVICE, settings.allowed_services)
    hermes_env = settings.hermes_home / ".env"
    delete_env(hermes_env, _ENDPOINT_ENV_KEY)
    delete_env(settings.env_file, _ENDPOINT_ENV_KEY)

    async def _restart_gw() -> None:
        try:
            await restart("hermes-gateway", settings.allowed_services)
        except Exception as exc:
            logger.error("gateway restart after OpenViking disable failed: %s", exc)

    background_tasks.add_task(_restart_gw)
    return ApiResponse(ok=True, data={"enabled": False})


# ─── uninstall ─────────────────────────────────────────────────────────────


async def _do_uninstall(purge: bool) -> None:
    args = ("--purge",) if purge else ()
    rc, out = await _run_script(_UNINSTALL_SCRIPT, *args)
    if rc != 0:
        logger.error("OpenViking uninstall failed (rc=%d):\n%s", rc, out[-2000:])
    else:
        logger.info("OpenViking uninstall OK (purge=%s)", purge)


@router.post(
    "/api/openviking/uninstall",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ApiResponse,
)
async def ov_uninstall(
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    body: dict = Body(default={}),
) -> ApiResponse:
    """Uninstall OpenViking. Body {"purge": true} also deletes config + data.

    Always unwire from Hermes first so the gateway stops pointing at a dead
    endpoint, then run the uninstall script in the background.
    """
    hermes_env = settings.hermes_home / ".env"
    delete_env(hermes_env, _ENDPOINT_ENV_KEY)
    delete_env(settings.env_file, _ENDPOINT_ENV_KEY)
    try:
        await stop(_SERVICE, settings.allowed_services)
    except Exception:
        pass

    purge = bool(body.get("purge"))
    background_tasks.add_task(_do_uninstall, purge)

    async def _restart_gw() -> None:
        try:
            await restart("hermes-gateway", settings.allowed_services)
        except Exception as exc:
            logger.error("gateway restart after OpenViking uninstall failed: %s", exc)

    background_tasks.add_task(_restart_gw)
    return ApiResponse(
        ok=True,
        data={"message": f"Đang gỡ OpenViking ở chế độ nền (purge={purge})."},
    )


# ─── logs ──────────────────────────────────────────────────────────────────


@router.get("/api/openviking/logs", response_model=ApiResponse)
async def ov_logs(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    lines: int = 100,
) -> ApiResponse:
    """Recent journal lines for the OpenViking service."""
    if not _is_installed():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="OpenViking chưa được cài.",
        )
    text = await journal_tail(_SERVICE, lines=lines, allowed=settings.allowed_services)
    return ApiResponse(ok=True, data={"service": _SERVICE, "logs": text})


# ─── config (read) ───────────────────────────────────────────────────────────


@router.get("/api/openviking/config", response_model=ApiResponse)
async def ov_get_config() -> ApiResponse:
    """Return the current ov.conf with api_key values masked (sk-****<last4>)."""
    if not _OV_CONF.exists():
        return ApiResponse(ok=True, data={"configured": False})
    try:
        conf = json.loads(_OV_CONF.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"ov.conf không đọc được: {exc}",
        )
    emb = conf.get("embedding", {}).get("dense", {})
    vlm = conf.get("vlm", {})
    return ApiResponse(
        ok=True,
        data={
            "configured": True,
            "embedding": {
                "api_base": emb.get("api_base"),
                "provider": emb.get("provider"),
                "model": emb.get("model"),
                "dimension": emb.get("dimension"),
                "api_key": mask_value("api_key", emb.get("api_key", "")),
            },
            "vlm": {
                "api_base": vlm.get("api_base"),
                "provider": vlm.get("provider"),
                "model": vlm.get("model"),
                "api_key": mask_value("api_key", vlm.get("api_key", "")),
            },
        },
    )


# ─── test-key ────────────────────────────────────────────────────────────────


@router.post("/api/openviking/test-key", response_model=ApiResponse)
async def ov_test_key(body: dict = Body(...)) -> ApiResponse:
    """Validate an LLM key by listing models at the provider before saving.

    Body: { "api_key", "api_base"? }. Hits GET <api_base>/models with the key —
    same cheap check /api/config/test-key uses for chat providers.
    """
    api_key = (body.get("api_key") or "").strip()
    api_base = (body.get("api_base") or "https://api.openai.com/v1").strip().rstrip("/")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Thiếu api_key."
        )
    url = f"{api_base}/models"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {api_key}"})
    except httpx.RequestError as exc:
        return ApiResponse(ok=False, error=f"Không gọi được {url}: {exc}")
    if resp.status_code == 200:
        return ApiResponse(ok=True, data={"valid": True})
    return ApiResponse(
        ok=False,
        data={"valid": False, "http_status": resp.status_code},
        error=f"Provider trả HTTP {resp.status_code} — key có thể sai.",
    )


# ─── restart ─────────────────────────────────────────────────────────────────


@router.post("/api/openviking/restart", response_model=ApiResponse)
async def ov_restart(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    """Restart the OpenViking service (recover from a hung server)."""
    if not _is_installed():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="OpenViking chưa được cài."
        )
    code, msg = await restart(_SERVICE, settings.allowed_services)
    if code != 0:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Restart {_SERVICE} lỗi: {msg}",
        )
    return ApiResponse(ok=True, data={"restarted": True})


# ─── upgrade ─────────────────────────────────────────────────────────────────


async def _do_upgrade(settings: Settings) -> None:
    uv = _OV_VENV_UV if Path(_OV_VENV_UV).exists() else "uv"
    proc = await asyncio.create_subprocess_exec(
        uv, "pip", "install", "--python", f"{_OV_DIR}/.venv/bin/python",
        "--upgrade", "openviking",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    if proc.returncode != 0:
        logger.error("OpenViking upgrade failed:\n%s", out.decode(errors="replace")[-2000:])
        return
    logger.info("OpenViking upgraded; restarting service")
    try:
        await restart(_SERVICE, settings.allowed_services)
    except Exception as exc:
        logger.error("restart after OpenViking upgrade failed: %s", exc)


@router.post(
    "/api/openviking/upgrade",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ApiResponse,
)
async def ov_upgrade(
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    """pip install --upgrade openviking (background) + restart the service."""
    if not _is_installed():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="OpenViking chưa được cài."
        )
    background_tasks.add_task(_do_upgrade, settings)
    return ApiResponse(ok=True, data={"message": "Đang nâng cấp OpenViking ở chế độ nền."})


# ─── stats ───────────────────────────────────────────────────────────────────


def _dir_size_bytes(path: Path) -> int:
    """Total size of a directory tree in bytes (0 if missing)."""
    if not path.exists():
        return 0
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


@router.get("/api/openviking/stats", response_model=ApiResponse)
async def ov_stats(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    """Memory/data-dir/uptime summary for the dashboard.

    Pulls live counts from the server's API when reachable; always reports the
    on-disk data size + service uptime so the panel shows something useful even
    when the server is down.
    """
    if not _is_installed():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="OpenViking chưa được cài."
        )
    active = await is_active(_SERVICE, settings.allowed_services)
    since = await active_since(_SERVICE, settings.allowed_services) if active else None
    data_bytes = _dir_size_bytes(_OV_HOME)

    # Best-effort live stats from the server (endpoint may not exist on all
    # versions — treat any failure as "unavailable", don't error the request).
    server_stats = None
    if active:
        try:
            async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT) as client:
                resp = await client.get(f"{_OV_ENDPOINT}/stats")
            if resp.status_code == 200:
                server_stats = resp.json()
        except (httpx.RequestError, json.JSONDecodeError):
            server_stats = None

    return ApiResponse(
        ok=True,
        data={
            "service_active": active,
            "active_since": since,
            "data_dir": str(_OV_HOME),
            "data_size_bytes": data_bytes,
            "data_size_mb": round(data_bytes / (1024 * 1024), 2),
            "server_stats": server_stats,
        },
    )
