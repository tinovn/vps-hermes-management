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
from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, status
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
# Plugin location. For a flat plugin the registry key Hermes matches against
# plugins.enabled is the `name:` from plugin.yaml (zalo-personal-platform), NOT
# the directory name — see hermes_cli/plugins.py `key = prefix/dir if prefix
# else name`. We read it from the manifest so we always enable the right key.
_PLUGIN_DIR = Path("/root/.hermes/plugins/zalo-personal")
_PLUGIN_FALLBACK_KEY = "zalo-personal-platform"
# Platform id the adapter registers via ctx.register_platform() — distinct from
# the plugin registry key. The gateway only STARTS a platform whose
# config.yaml `platforms.<id>.enabled` is true (see gateway/run.py), so enabling
# the plugin alone is not enough; we must flip this too.
_PLATFORM_ID = "zalo-personal"
_HERMES_BIN = "/usr/local/bin/hermes"


def _plugin_key() -> str:
    """Registry key = `name:` in plugin.yaml (falls back to a known default)."""
    manifest = _PLUGIN_DIR / "plugin.yaml"
    try:
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if line.startswith("name:"):
                val = line.split(":", 1)[1].strip()
                if val:
                    return val
    except OSError:
        pass
    return _PLUGIN_FALLBACK_KEY


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
    key = _plugin_key()
    if key not in enabled:
        enabled.append(key)
    plugins["enabled"] = enabled
    plugins.setdefault("disabled", [])
    data["plugins"] = plugins

    # Also flip platforms.<id>.enabled — the gateway only STARTS (and spawns the
    # sidecar for) a platform marked enabled here. Without this the plugin loads
    # but the gateway logs "No messaging platforms enabled" and never connects.
    platforms = data.get("platforms")
    if not isinstance(platforms, dict):
        platforms = {}
    entry = platforms.get(_PLATFORM_ID)
    if not isinstance(entry, dict):
        entry = {}
    entry["enabled"] = True
    platforms[_PLATFORM_ID] = entry
    data["platforms"] = platforms

    cfg_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


async def _activate_plugin_and_handover(settings: Settings) -> None:
    """After OWNER_UID is set: enable plugin in config.yaml + restart gateway.

    The gateway then satisfies check_requirements() (owner uid present) and takes
    over sidecar lifecycle. Run as a background task — restart cycles the gateway.
    """
    try:
        _enable_plugin_in_config(settings)
    except Exception as exc:
        logger.error("Failed to enable Zalo plugin in config.yaml: %s", exc)
    try:
        await restart("hermes-gateway", settings.allowed_services)
    except Exception as exc:
        logger.error("gateway restart after Zalo owner-set failed: %s", exc)


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


def _set_owner_uid(settings: Settings, uid: str) -> None:
    """Write ZALO_PERSONAL_OWNER_UID to both env stores (overwrites).

    NOTE: owner = the BOSS who messages the bot to give admin commands — a
    DIFFERENT Zalo account from the bot (the QR-scanned account). The bot's own
    uid (getOwnId/health.uid) must NOT be used here. Owner is resolved from the
    boss's phone number via /api/zalo/set-owner.
    """
    if not uid:
        return
    hermes_env_file = settings.hermes_home / ".env"
    set_env(hermes_env_file, _OWNER_UID_KEY, uid)
    set_env(settings.env_file, _OWNER_UID_KEY, uid)
    logger.info("Set %s=%s", _OWNER_UID_KEY, uid)


def _owner_uid(settings: Settings) -> str:
    merged = read_env(settings.env_file)
    merged.update(read_env(settings.hermes_home / ".env"))
    return merged.get(_OWNER_UID_KEY, "").strip()


@router.get("/api/zalo/status", response_model=ApiResponse)
async def zalo_status(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    """Connection state for the dashboard to poll.

    data.status ∈ {disconnected, pending, scanned, connected, error}.
    `bot_uid` = the logged-in (QR-scanned) bot account. `owner_set` tells the
    GUI whether the boss's owner UID has been configured yet — until it is, the
    bot won't accept admin commands and the gateway won't run the plugin.
    """
    try:
        resp = await _sidecar_get(settings, "/health")
    except httpx.RequestError:
        return ApiResponse(
            ok=True,
            data={"status": "disconnected", "bot_uid": None, "name": None,
                  "sidecar": False, "owner_set": bool(_owner_uid(settings))},
        )

    if resp.status_code != 200:
        return ApiResponse(
            ok=True,
            data={"status": "disconnected", "bot_uid": None, "name": None,
                  "sidecar": True, "owner_set": bool(_owner_uid(settings))},
        )

    health = resp.json()
    return ApiResponse(
        ok=True,
        data={
            "status": health.get("status", "disconnected"),
            "bot_uid": health.get("uid"),          # the bot account, NOT the owner
            "name": health.get("name"),
            "error": health.get("error"),
            "sidecar": True,
            "owner_set": bool(_owner_uid(settings)),
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
        # bot_uid = the logged-in account, NOT the owner. Owner is set
        # separately via /api/zalo/set-owner (boss's phone → uid).
        return ApiResponse(ok=True, data={"status": "connected", "bot_uid": body.get("uid")})

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


# ─── owner (the boss who controls the bot) ──────────────────────────────────


@router.get("/api/zalo/owner", response_model=ApiResponse)
async def zalo_get_owner(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    """Current owner UID (the boss who can give the bot admin commands)."""
    uid = _owner_uid(settings)
    return ApiResponse(ok=True, data={"owner_uid": uid or None, "owner_set": bool(uid)})


@router.post("/api/zalo/set-owner", response_model=ApiResponse)
async def zalo_set_owner(
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    body: dict = Body(...),
) -> ApiResponse:
    """Set the owner (boss) by phone number or explicit UID.

    The owner is the boss's PERSONAL Zalo account — DIFFERENT from the bot
    (QR-scanned) account. Body one of:
      { "phone": "09..." }  → resolved to a UID via the sidecar
      { "uid": "123..." }   → set directly (advanced)
    On success: set ZALO_PERSONAL_OWNER_UID, enable the plugin, restart gateway.
    The bot (sidecar) must be connected to resolve a phone.
    """
    uid = str(body.get("uid", "")).strip()
    phone = str(body.get("phone", "")).strip()

    if not uid and phone:
        # Resolve phone → uid via the connected sidecar.
        try:
            url = f"{_sidecar_base_url(settings)}/users/by-phones"
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(url, json={"phones": [phone]})
        except httpx.RequestError:
            raise _sidecar_unreachable()
        if r.status_code == 503:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Bot Zalo chưa đăng nhập — quét QR kết nối trước rồi mới tra số sếp.",
            )
        if r.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Không tra được số (HTTP {r.status_code}).",
            )
        users = r.json().get("users") or []
        if not users or not users[0].get("uid"):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Không tìm thấy tài khoản Zalo cho số {phone}. Kiểm tra lại số.",
            )
        uid = str(users[0]["uid"])

    if not uid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cần 'phone' (số Zalo của sếp) hoặc 'uid'.",
        )

    _set_owner_uid(settings, uid)
    background_tasks.add_task(_activate_plugin_and_handover, settings)
    return ApiResponse(ok=True, data={"owner_uid": uid, "owner_set": True})


# ─── runtime control: chat mode / group list / add friend / logs ─────────────
# Cron jobs đã có API chung tại /api/cron (cron_routes.py) — không lặp lại đây.

# Mirror của _VALID_CHAT_MODES trong adapter.py (plugin hermes-zalo-plugin).
_VALID_CHAT_MODES = {
    "default", "active", "mention_only", "listen_only", "mute", "sales_active",
}


def _zalo_session_dir(settings: Settings) -> Path:
    merged = read_env(settings.env_file)
    merged.update(read_env(settings.hermes_home / ".env"))
    return Path(merged.get("ZALO_PERSONAL_SESSION_DIR", "").strip() or "/opt/data/zalo")


def _chat_settings_path(settings: Settings) -> Path:
    """chat_settings.json — adapter đọc file này MỖI tin nhắn, nên mgmt ghi
    trực tiếp là có hiệu lực ngay, không cần restart gateway."""
    return _zalo_session_dir(settings) / "chat_settings.json"


async def _sidecar_post_json(
    settings: Settings, path: str, payload: dict, timeout: float = 20.0
) -> httpx.Response:
    url = f"{_sidecar_base_url(settings)}{path}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.post(url, json=payload)


@router.get("/api/zalo/chat-modes", response_model=ApiResponse)
async def zalo_list_chat_modes(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    """Mode hiện tại của từng chat (active / mention_only / listen_only / mute...)."""
    import json as _json

    path = _chat_settings_path(settings)
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    modes = {
        cid: (rec or {}).get("mode", "default")
        for cid, rec in data.items()
        if isinstance(rec, dict)
    }
    return ApiResponse(ok=True, data={"chat_modes": modes, "valid_modes": sorted(_VALID_CHAT_MODES)})


@router.post("/api/zalo/chat-mode", response_model=ApiResponse)
async def zalo_set_chat_mode(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    body: dict = Body(...),
) -> ApiResponse:
    """Đổi mode bot trong 1 chat: {chat_id, mode}.

    mode=active → bot trả lời MỌI tin trong group đó (không cần @tag);
    mention_only → chỉ khi tag/reply; listen_only → chỉ nghe; mute → bỏ qua.
    Ghi thẳng chat_settings.json — adapter áp dụng ngay tin kế tiếp.
    """
    import json as _json

    chat_id = str(body.get("chat_id", "")).strip()
    mode = str(body.get("mode", "")).strip().lower()
    if not chat_id or mode not in _VALID_CHAT_MODES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cần chat_id và mode hợp lệ ({', '.join(sorted(_VALID_CHAT_MODES))}).",
        )
    path = _chat_settings_path(settings)
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    rec = data.get(chat_id)
    if not isinstance(rec, dict):
        rec = {}
    rec["mode"] = mode
    data[chat_id] = rec
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    logger.info("zalo chat-mode: %s -> %s", chat_id, mode)
    return ApiResponse(ok=True, data={"chat_id": chat_id, "mode": mode})


@router.get("/api/zalo/groups", response_model=ApiResponse)
async def zalo_list_groups(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    """Danh sách nhóm bot đang tham gia (id + tên + sĩ số) — để GUI chọn nhóm
    rồi gọi /api/zalo/chat-mode bật active cho đúng nhóm."""
    try:
        r = await _sidecar_post_json(settings, "/api/call", {"method": "getAllGroups", "args": []})
    except httpx.RequestError:
        raise _sidecar_unreachable()
    if r.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Sidecar getAllGroups lỗi (HTTP {r.status_code}): {r.text[:200]}",
        )
    grid_ver = (r.json().get("result") or {}).get("gridVerMap") or {}
    group_ids = [str(g).split("_")[0] for g in grid_ver.keys()]
    groups: list[dict] = []
    # getGroupInfo nhận mảng id → trả gridInfoMap {id: {name, totalMember...}}.
    if group_ids:
        try:
            r2 = await _sidecar_post_json(
                settings, "/api/call", {"method": "getGroupInfo", "args": [group_ids]},
                timeout=30.0,
            )
            info_map = (r2.json().get("result") or {}).get("gridInfoMap") or {} \
                if r2.status_code == 200 else {}
        except httpx.RequestError:
            info_map = {}
        for gid in group_ids:
            info = info_map.get(gid) or {}
            groups.append({
                "group_id": gid,
                "name": info.get("name") or info.get("groupName") or "",
                "total_member": info.get("totalMember"),
            })
    return ApiResponse(ok=True, data={"count": len(groups), "groups": groups})


@router.post("/api/zalo/friend-request", response_model=ApiResponse)
async def zalo_friend_request(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    body: dict = Body(...),
) -> ApiResponse:
    """Gửi lời mời kết bạn từ tài khoản bot: {uid} hoặc {phone}, kèm message?.

    Dùng khi cần bot chủ động kết bạn khách (sau đó nhắn tin được 1-1).
    """
    uid = str(body.get("uid", "")).strip()
    phone = str(body.get("phone", "")).strip()
    message = str(body.get("message", "")).strip() or "Xin chào, kết bạn với mình nhé!"

    if not uid and phone:
        try:
            r = await _sidecar_post_json(settings, "/users/by-phones", {"phones": [phone]}, timeout=15.0)
        except httpx.RequestError:
            raise _sidecar_unreachable()
        users = (r.json().get("users") or []) if r.status_code == 200 else []
        if not users or not users[0].get("uid"):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Không tìm thấy tài khoản Zalo cho số {phone}.",
            )
        uid = str(users[0]["uid"])
    if not uid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cần 'uid' hoặc 'phone' của người muốn kết bạn.",
        )
    try:
        r = await _sidecar_post_json(settings, "/friend/request", {"uid": uid, "msg": message}, timeout=20.0)
    except httpx.RequestError:
        raise _sidecar_unreachable()
    if r.status_code != 200:
        detail = ""
        try:
            detail = str(r.json().get("error") or "")[:200]
        except ValueError:
            detail = r.text[:200]
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Gửi lời mời thất bại: {detail or f'HTTP {r.status_code}'}",
        )
    return ApiResponse(ok=True, data={"uid": uid, "message": message})


@router.get("/api/zalo/logs", response_model=ApiResponse)
async def zalo_logs(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    lines: int = 200,
) -> ApiResponse:
    """Tail log liên quan Zalo (lọc từ agent.log + gateway.log của Hermes).

    Cron jobs xem/sửa qua API chung /api/cron — không trùng lặp ở đây.
    """
    lines = max(10, min(int(lines or 200), 1000))
    out: list[str] = []
    for fname in ("agent.log", "gateway.log"):
        p = settings.hermes_home / "logs" / fname
        try:
            with open(p, "rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - 1024 * 1024))  # đọc tối đa 1MB cuối
                text = fh.read().decode("utf-8", errors="replace")
        except OSError:
            continue
        out.extend(
            f"[{fname}] {ln}"
            for ln in text.splitlines()
            if "zalo" in ln.lower()
        )
    return ApiResponse(ok=True, data={"lines": out[-lines:], "count": min(len(out), lines)})
