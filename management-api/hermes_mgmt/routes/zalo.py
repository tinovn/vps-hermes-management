"""Zalo personal plugin control — dashboard-friendly proxy to the Node sidecar.

The Zalo plugin ships a Node.js sidecar (zca-js) that the gateway spawns and
binds to 127.0.0.1:<port> (default 3838) — unreachable from outside the VPS.
This router proxies the few endpoints a low-tech user needs (QR connect / status
/ QR image / disconnect) through the Management API, which the dashboard at
tino.vn already reaches. So the whole flow becomes: click "Kết nối Zalo" →
scan the QR shown on the page → done. No SSH, no curl, no UID hunting.

On successful connect we auto-persist ZALO_PERSONAL_OWNER_UID into both .env
stores (the sidecar already knows the owner uid via getOwnId), so the user never
has to find or type their Zalo UID.

Chicken-and-egg fix: the Hermes plugin only spawns the sidecar once
ZALO_PERSONAL_OWNER_UID is set (check_requirements gates on it), but the UID is
only knowable AFTER a QR login. So for the QR step we spawn the Node sidecar
ourselves (independent of the gateway) on the same localhost port. Once the user
scans and we learn the uid, we persist it, enable the plugin in config.yaml, and
restart the gateway — which then takes over sidecar management (session persists
on disk, so the login survives the handover).
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import Response

from hermes_mgmt.config import Settings
from hermes_mgmt.deps import get_settings_dep, require_auth
from hermes_mgmt.env_file import read_env, set_env
from hermes_mgmt.models import ApiResponse
from hermes_mgmt.systemd_ctl import restart

logger = logging.getLogger(__name__)

router = APIRouter(tags=["zalo"], dependencies=[Depends(require_auth)])

_OWNER_UID_KEY = "ZALO_PERSONAL_OWNER_UID"
_SIDECAR_PORT_KEY = "ZALO_PERSONAL_SIDECAR_PORT"
_DEFAULT_SIDECAR_PORT = 3838
# Short timeout: the sidecar is local. /login/qr returns immediately (QR is
# generated async and fetched separately via /qr.png).
_SIDECAR_TIMEOUT = 8.0
# Plugin location + registry key (dir-based, see Hermes hermes_cli/plugins.py).
_PLUGIN_DIR = Path("/root/.hermes/plugins/zalo-personal")
_PLUGIN_KEY = "zalo-personal"
_HERMES_BIN = "/usr/local/bin/hermes"


def _port_open(port: int) -> bool:
    """True if something is already listening on 127.0.0.1:<port>."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _sidecar_port(settings: Settings) -> int:
    merged = read_env(settings.env_file)
    merged.update(read_env(settings.hermes_home / ".env"))
    raw = merged.get(_SIDECAR_PORT_KEY, "").strip() or str(_DEFAULT_SIDECAR_PORT)
    try:
        return int(raw)
    except ValueError:
        return _DEFAULT_SIDECAR_PORT


async def _ensure_sidecar(settings: Settings) -> bool:
    """Spawn the Node sidecar for the QR step if nothing is on its port yet.

    Returns True if the sidecar is (now) reachable. The gateway-managed sidecar,
    when the plugin is active, already holds the port — we leave it alone.
    """
    port = _sidecar_port(settings)
    if _port_open(port):
        return True

    server_js = _PLUGIN_DIR / "sidecar" / "server.js"
    if not server_js.exists():
        logger.error("Zalo sidecar not found at %s", server_js)
        return False

    # Inherit .env (port + session dir + proxy) so our sidecar matches the one
    # the gateway would spawn — same session file, so login survives handover.
    env = os.environ.copy()
    merged = read_env(settings.env_file)
    merged.update(read_env(settings.hermes_home / ".env"))
    for key in (
        _SIDECAR_PORT_KEY,
        "ZALO_PERSONAL_SESSION_DIR",
        "ZALO_PERSONAL_PROXY",
        "HOME",
    ):
        if merged.get(key):
            env[key] = merged[key]
    env.setdefault("HOME", "/root")

    try:
        await asyncio.create_subprocess_exec(
            "node", str(server_js),
            cwd=str(server_js.parent),
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,  # detach: outlives this request
        )
    except (FileNotFoundError, OSError) as exc:
        logger.error("Failed to spawn Zalo sidecar: %s", exc)
        return False

    # Poll until it binds the port (zca-js init takes a moment).
    for _ in range(20):
        if _port_open(port):
            return True
        await asyncio.sleep(0.5)
    return _port_open(port)


def _enable_plugin_in_config(settings: Settings) -> None:
    """Add the plugin key to config.yaml plugins.enabled (idempotent)."""
    import yaml

    cfg_path = settings.hermes_home / "config.yaml"
    try:
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        data = {}
    plugins = data.get("plugins")
    if not isinstance(plugins, dict):
        plugins = {}
    enabled = plugins.get("enabled")
    if not isinstance(enabled, list):
        enabled = []
    if _PLUGIN_KEY not in enabled:
        enabled.append(_PLUGIN_KEY)
    plugins["enabled"] = enabled
    plugins.setdefault("disabled", [])
    data["plugins"] = plugins
    cfg_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


async def _activate_plugin_and_handover(settings: Settings, uid: str) -> None:
    """After QR connect: persist uid, enable plugin, restart gateway.

    The gateway then satisfies check_requirements() (uid present) and takes over
    sidecar lifecycle. Run as a background task — restart cycles the gateway.
    """
    _persist_owner_uid(settings, uid)
    try:
        _enable_plugin_in_config(settings)
    except Exception as exc:
        logger.error("Failed to enable Zalo plugin in config.yaml: %s", exc)
    try:
        await restart("hermes-gateway", settings.allowed_services)
    except Exception as exc:
        logger.error("gateway restart after Zalo connect failed: %s", exc)


def _sidecar_base_url(settings: Settings) -> str:
    """Resolve sidecar URL from .env (port may be customized), localhost-only."""
    merged = read_env(settings.env_file)
    merged.update(read_env(settings.hermes_home / ".env"))
    port = merged.get(_SIDECAR_PORT_KEY, "").strip() or str(_DEFAULT_SIDECAR_PORT)
    try:
        port_int = int(port)
    except ValueError:
        port_int = _DEFAULT_SIDECAR_PORT
    return f"http://127.0.0.1:{port_int}"


async def _sidecar_get(settings: Settings, path: str) -> httpx.Response:
    url = f"{_sidecar_base_url(settings)}{path}"
    async with httpx.AsyncClient(timeout=_SIDECAR_TIMEOUT) as client:
        return await client.get(url)


async def _sidecar_post(settings: Settings, path: str) -> httpx.Response:
    url = f"{_sidecar_base_url(settings)}{path}"
    async with httpx.AsyncClient(timeout=_SIDECAR_TIMEOUT) as client:
        return await client.post(url)


def _sidecar_unreachable() -> HTTPException:
    # The plugin/gateway may not have spawned the sidecar yet (e.g. gateway
    # still starting, or plugin disabled). Surface a clear, user-facing hint.
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=(
            "Zalo sidecar chưa sẵn sàng. Đảm bảo hermes-gateway đang chạy và "
            "plugin Zalo đã cài; thử lại sau vài giây."
        ),
    )


def _persist_owner_uid(settings: Settings, uid: str) -> None:
    """Write ZALO_PERSONAL_OWNER_UID to both env stores if not already set.

    Mirrors the channels.py dual-write pattern: HERMES_HOME/.env (dashboard
    reads it) + /opt/hermes/.env (systemd EnvironmentFile for the gateway).
    """
    if not uid:
        return
    hermes_env_file = settings.hermes_home / ".env"
    merged = read_env(settings.env_file)
    merged.update(read_env(hermes_env_file))
    if merged.get(_OWNER_UID_KEY, "").strip():
        return  # respect any value the user already configured
    set_env(hermes_env_file, _OWNER_UID_KEY, uid)
    set_env(settings.env_file, _OWNER_UID_KEY, uid)
    logger.info("Auto-persisted %s=%s after Zalo connect", _OWNER_UID_KEY, uid)


def _plugin_active(settings: Settings) -> bool:
    """True if OWNER_UID is set (gateway can run the plugin itself)."""
    merged = read_env(settings.env_file)
    merged.update(read_env(settings.hermes_home / ".env"))
    return bool(merged.get(_OWNER_UID_KEY, "").strip())


@router.get("/api/zalo/status", response_model=ApiResponse)
async def zalo_status(
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    """Connection state for the dashboard to poll.

    data.status ∈ {disconnected, pending, scanned, connected, error}.
    On first successful connect (uid learned, plugin not yet active), persist the
    uid + enable the plugin + restart the gateway so it takes over the sidecar.
    """
    try:
        resp = await _sidecar_get(settings, "/health")
    except httpx.RequestError:
        # Sidecar not up yet — report disconnected rather than erroring, so the
        # dashboard can still render the "Kết nối Zalo" button.
        return ApiResponse(
            ok=True,
            data={"status": "disconnected", "uid": None, "name": None, "sidecar": False},
        )

    if resp.status_code != 200:
        return ApiResponse(
            ok=True,
            data={"status": "disconnected", "uid": None, "name": None, "sidecar": True},
        )

    health = resp.json()
    uid = health.get("uid")
    handover = False
    if health.get("status") == "connected" and uid:
        # First time we see a uid and the plugin isn't wired yet → hand over to
        # the gateway (persist uid + enable + restart). Idempotent thereafter.
        if not _plugin_active(settings):
            handover = True
            background_tasks.add_task(_activate_plugin_and_handover, settings, str(uid))
        else:
            _persist_owner_uid(settings, str(uid))

    return ApiResponse(
        ok=True,
        data={
            "status": health.get("status", "disconnected"),
            "uid": uid,
            "name": health.get("name"),
            "error": health.get("error"),
            "sidecar": True,
            "activating": handover,
        },
    )


@router.post("/api/zalo/connect", response_model=ApiResponse)
async def zalo_connect(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    """Start QR login. Returns immediately; poll /api/zalo/status + show QR.

    Spawns the Node sidecar ourselves if the gateway isn't already running it
    (the plugin only runs it once OWNER_UID is set — which we don't have yet on
    a first connect). The login session persists to disk, so when we later hand
    over to the gateway it resumes the same logged-in session.
    """
    if not await _ensure_sidecar(settings):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Không khởi động được Zalo sidecar (Node.js). Kiểm tra plugin đã "
                "cài + node có trong PATH."
            ),
        )
    try:
        resp = await _sidecar_post(settings, "/login/qr")
    except httpx.RequestError:
        raise _sidecar_unreachable()

    if resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Sidecar trả lỗi khi bắt đầu đăng nhập QR (HTTP {resp.status_code}).",
        )

    body = resp.json()
    if body.get("status") == "already_connected":
        uid = body.get("uid")
        if uid:
            _persist_owner_uid(settings, str(uid))
        return ApiResponse(ok=True, data={"status": "connected", "uid": uid})

    # QR is generated asynchronously; the dashboard fetches it from /api/zalo/qr.
    return ApiResponse(ok=True, data={"status": "pending", "qr_url": "/api/zalo/qr"})


@router.get("/api/zalo/qr")
async def zalo_qr(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> Response:
    """Proxy the QR PNG so the dashboard can render <img src=/api/zalo/qr>.

    Returns the raw image bytes (not the ApiResponse envelope) so it can be used
    directly as an image source. 404 while the QR is still being generated —
    the dashboard should retry for a couple seconds after calling /connect.
    """
    try:
        resp = await _sidecar_get(settings, "/qr.png")
    except httpx.RequestError:
        raise _sidecar_unreachable()

    if resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mã QR chưa sẵn sàng, thử lại sau 1-2 giây.",
        )

    return Response(
        content=resp.content,
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/api/zalo/disconnect", response_model=ApiResponse)
async def zalo_disconnect(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    """Logout + clear the Zalo session (sidecar /logout)."""
    try:
        resp = await _sidecar_post(settings, "/logout")
    except httpx.RequestError:
        raise _sidecar_unreachable()

    if resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Sidecar trả lỗi khi đăng xuất (HTTP {resp.status_code}).",
        )
    return ApiResponse(ok=True, data={"status": "disconnected"})
