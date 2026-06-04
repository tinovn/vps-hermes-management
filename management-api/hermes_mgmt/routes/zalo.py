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
"""

from __future__ import annotations

import logging
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response

from hermes_mgmt.config import Settings
from hermes_mgmt.deps import get_settings_dep, require_auth
from hermes_mgmt.env_file import read_env, set_env
from hermes_mgmt.models import ApiResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["zalo"], dependencies=[Depends(require_auth)])

_OWNER_UID_KEY = "ZALO_PERSONAL_OWNER_UID"
_SIDECAR_PORT_KEY = "ZALO_PERSONAL_SIDECAR_PORT"
_DEFAULT_SIDECAR_PORT = 3838
# Short timeout: the sidecar is local. /login/qr returns immediately (QR is
# generated async and fetched separately via /qr.png).
_SIDECAR_TIMEOUT = 8.0


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


@router.get("/api/zalo/status", response_model=ApiResponse)
async def zalo_status(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    """Connection state for the dashboard to poll.

    data.status ∈ {disconnected, pending, scanned, connected, error}.
    When connected, auto-persist the owner uid so the user never types it.
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
    if health.get("status") == "connected" and uid:
        _persist_owner_uid(settings, str(uid))

    return ApiResponse(
        ok=True,
        data={
            "status": health.get("status", "disconnected"),
            "uid": uid,
            "name": health.get("name"),
            "error": health.get("error"),
            "sidecar": True,
        },
    )


@router.post("/api/zalo/connect", response_model=ApiResponse)
async def zalo_connect(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    """Start QR login. Returns immediately; poll /api/zalo/status + show QR."""
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
