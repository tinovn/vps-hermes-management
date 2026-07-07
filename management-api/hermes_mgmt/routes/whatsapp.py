"""WhatsApp (Baileys bridge) control — dashboard-friendly QR pairing + enable.

Unlike Zalo, the Hermes WhatsApp bridge does NOT expose its pairing QR over
HTTP — it only prints ASCII art to stdout, and in pair-only mode opens no
server at all. So to give the Tino dashboard the same "click connect → scan QR
on the page" flow, we drive pairing with a small sidecar (``assets/whatsapp_pair.mjs``)
that reuses the bridge's already-installed Baileys, captures the RAW QR string,
and serves it on loopback. This router renders that string to a PNG.

Flow:
  1. POST /connect  → ensure bridge deps + spawn the pairing sidecar → QR.
  2. GET  /qr       → PNG of the current QR (poll until the phone scans).
  3. (phone scans)  → Baileys writes creds.json to the shared session dir; the
                      sidecar exits, freeing the WhatsApp socket.
  4. POST /enable   → set WHATSAPP_ENABLED/MODE/ALLOWED_USERS in both .env
                      stores + restart the gateway, which reconnects with creds.

Enabling WhatsApp needs no config.yaml edit: ``WHATSAPP_ENABLED=true`` alone
makes gateway/config.py create ``PlatformConfig(enabled=True)`` for the platform.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import signal
import socket
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, status
from fastapi.responses import Response

from hermes_mgmt.config import Settings
from hermes_mgmt.deps import get_settings_dep, require_auth
from hermes_mgmt.env_file import read_env, set_env
from hermes_mgmt.models import ApiResponse
from hermes_mgmt.systemd_ctl import restart

logger = logging.getLogger(__name__)

router = APIRouter(tags=["whatsapp"], dependencies=[Depends(require_auth)])

_ENABLED_KEY = "WHATSAPP_ENABLED"
_MODE_KEY = "WHATSAPP_MODE"
_ALLOWED_KEY = "WHATSAPP_ALLOWED_USERS"
_PAIR_PORT_KEY = "WHATSAPP_PAIR_PORT"
_BRIDGE_PORT_KEY = "WHATSAPP_BRIDGE_PORT"

_DEFAULT_PAIR_PORT = 3999
_DEFAULT_BRIDGE_PORT = 3000  # gateway bridge default (bridge.js PORT)
_VALID_MODES = {"bot", "self-chat"}
_SIDECAR_TIMEOUT = 8.0

_PAIR_ASSET = Path(__file__).resolve().parent.parent / "assets" / "whatsapp_pair.mjs"
_PAIR_ASSET_RAW = (
    "https://raw.githubusercontent.com/tinovn/vps-hermes-management/main"
    "/management-api/hermes_mgmt/assets/whatsapp_pair.mjs"
)


# ── path / env helpers ──────────────────────────────────────────────────────


def _merged_env(settings: Settings) -> dict[str, str]:
    merged = read_env(settings.env_file)
    merged.update(read_env(settings.hermes_home / ".env"))
    return merged


def _bridge_dir(settings: Settings) -> Path:
    """Where the Hermes WhatsApp bridge (and its node_modules) lives.

    Mirrors gateway/platforms/whatsapp_common.resolve_whatsapp_bridge_dir: the
    editable Hermes clone under the install tree, falling back to HERMES_HOME.
    Overridable via WHATSAPP_BRIDGE_DIR for non-standard layouts.
    """
    override = _merged_env(settings).get("WHATSAPP_BRIDGE_DIR", "").strip()
    if override:
        return Path(override)
    install_bridge = settings.install_dir / "hermes-agent" / "scripts" / "whatsapp-bridge"
    if install_bridge.exists():
        return install_bridge
    return settings.hermes_home / "scripts" / "whatsapp-bridge"


def _legacy_has_content(path: Path) -> bool:
    """Mirror hermes_constants._legacy_path_has_content.

    A populated dir (any entry) or any non-dir file counts. An empty dir does
    not. If the path can't be inspected (permissions), assume occupied so we
    don't orphan real data; only a genuine missing path counts as absent.
    """
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    real = path
    if path.is_symlink():
        try:
            real = path.resolve(strict=True)
        except (OSError, RuntimeError):
            return False  # dangling symlink must not shadow new-layout data
    try:
        if real.is_dir():
            return any(real.iterdir())
        return True
    except OSError:
        return True


def _session_dir(settings: Settings) -> Path:
    """Runtime WhatsApp session dir, matching the adapter's
    ``get_hermes_dir("platforms/whatsapp/session", "whatsapp/session")``.

    Existing installs paired via ``hermes whatsapp`` keep creds under the legacy
    ``whatsapp/session``; new installs use the consolidated ``platforms/...``
    path. We must resolve identically or we'd miss existing creds (reporting
    "not paired") and pair a *second* device into the new path.
    """
    home = settings.hermes_home
    legacy = home / "whatsapp" / "session"
    if _legacy_has_content(legacy):
        return legacy
    return home / "platforms" / "whatsapp" / "session"


def _creds_path(settings: Settings) -> Path:
    return _session_dir(settings) / "creds.json"


def _pidfile(settings: Settings) -> Path:
    # Next to the resolved session dir so its parent always exists once
    # _ensure_sidecar has mkdir'd the session dir.
    return _session_dir(settings).parent / "pair.pid"


def _pair_port(settings: Settings) -> int:
    raw = _merged_env(settings).get(_PAIR_PORT_KEY, "").strip() or str(_DEFAULT_PAIR_PORT)
    try:
        return int(raw)
    except ValueError:
        return _DEFAULT_PAIR_PORT


def _bridge_port(settings: Settings) -> int:
    raw = _merged_env(settings).get(_BRIDGE_PORT_KEY, "").strip() or str(_DEFAULT_BRIDGE_PORT)
    try:
        return int(raw)
    except ValueError:
        return _DEFAULT_BRIDGE_PORT


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


# ── bridge dependency install (explicit, async — dashboard "Install" button) ─
# Baileys is a git-pinned dep that compiles TypeScript at install time, so
# `npm install` takes minutes and peaks >1GB RAM. We run it as a DETACHED
# background job (never blocks the request) and track state via files under the
# bridge dir. The job is self-contained: it fixes the two things that make a
# fresh VPS fail — Baileys' ssh:// git sub-dep (rewrite to HTTPS) and OOM on
# low-RAM boxes (add a swapfile) — then installs and stamps.


def _install_pidfile(settings: Settings) -> Path:
    return _bridge_dir(settings) / ".hermes-install.pid"


def _install_log(settings: Settings) -> Path:
    return _bridge_dir(settings) / ".hermes-install.log"


def _install_fail_marker(settings: Settings) -> Path:
    return _bridge_dir(settings) / ".hermes-install.failed"


def _bridge_installed(settings: Settings) -> bool:
    """True once Baileys is actually built — check the compiled entrypoint, not
    just the package dir. npm creates node_modules/@whiskeysockets/baileys early
    during install (before the tsc build finishes), so the dir alone would report
    "installed" mid-build; lib/index.js only exists once the build completed."""
    return (
        _bridge_dir(settings)
        / "node_modules" / "@whiskeysockets" / "baileys" / "lib" / "index.js"
    ).exists()


def _install_running(settings: Settings) -> bool:
    """True while the detached install job is still alive (by pidfile)."""
    pidfile = _install_pidfile(settings)
    try:
        pid = int(pidfile.read_text().strip())
    except (OSError, ValueError):
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        pidfile.unlink(missing_ok=True)  # stale
        return False
    except PermissionError:
        return True


def _install_state(settings: Settings) -> str:
    """One of: installed | installing | failed | not_installed.

    Check "running" BEFORE "installed": while the job runs, npm may have created
    a partial baileys dir, so a build-artifact check alone could flip to
    installed mid-build. An in-flight job always means "installing".
    """
    if _install_running(settings):
        return "installing"
    if _bridge_installed(settings):
        return "installed"
    if _install_fail_marker(settings).exists():
        return "failed"
    return "not_installed"


# Self-contained installer: HTTPS git rewrite + swap-if-low-RAM + npm + stamp.
_INSTALL_SCRIPT = r"""
set +e
BD={bridge}
rm -f "$BD/.hermes-install.failed"
# Baileys pulls libsignal-node via ssh://git@github.com — no key on a fresh VPS.
# Rewrite SSH GitHub URLs to HTTPS. Both forms share one url key, so plain
# `git config` would have the 2nd overwrite the 1st — use --add (after
# --unset-all for idempotency) to keep BOTH the ssh:// and scp-style rules.
git config --global --unset-all url."https://github.com/".insteadOf 2>/dev/null
git config --global --add url."https://github.com/".insteadOf "ssh://git@github.com/" 2>/dev/null
git config --global --add url."https://github.com/".insteadOf "git@github.com:" 2>/dev/null
# Baileys' tsc build OOMs on ~2GB boxes with no swap — add a 2G swapfile.
if ! swapon --show 2>/dev/null | grep -q .; then
  MEM=$(awk '/MemTotal/{{print int($2/1024)}}' /proc/meminfo)
  if [ "${{MEM:-0}}" -lt 3000 ] && [ ! -e /swapfile ]; then
    if fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none; then
      chmod 600 /swapfile && mkswap /swapfile >/dev/null 2>&1 && swapon /swapfile 2>/dev/null && \
        {{ grep -q /swapfile /etc/fstab 2>/dev/null || echo '/swapfile none swap sw 0 0' >> /etc/fstab; }}
    fi
  fi
fi
cd "$BD" || exit 1
# The install job is a child of hermes-mgmt.service (MemoryMax=512M). Baileys'
# tsc build peaks >1GB, so running it in that cgroup gets it OOM-killed
# (SIGABRT / code 134). Run npm in its own transient systemd scope so it escapes
# mgmt's memory cap (falls back to plain npm on non-systemd hosts).
if command -v systemd-run >/dev/null 2>&1; then
  systemd-run --scope --quiet -p MemoryMax=infinity -p MemorySwapMax=infinity \
    npm install --no-audit --no-fund --loglevel=error
else
  npm install --no-audit --no-fund --loglevel=error
fi
if [ -f node_modules/@whiskeysockets/baileys/lib/index.js ]; then
  sha256sum package.json | cut -c1-16 > node_modules/.hermes-pkg-hash
else
  echo "npm install failed — see this log above" > "$BD/.hermes-install.failed"
fi
rm -f "$BD/.hermes-install.pid"
"""


async def _start_install(settings: Settings) -> str:
    """Kick off the detached install job. Returns the resulting install state.

    Idempotent: no-op if already installed or an install is in flight.
    """
    if _bridge_installed(settings):
        return "installed"
    if _install_running(settings):
        return "installing"

    bridge_dir = _bridge_dir(settings)
    if not (bridge_dir / "package.json").exists():
        logger.error("WhatsApp bridge not found at %s", bridge_dir)
        return "no_bridge"

    _install_fail_marker(settings).unlink(missing_ok=True)
    script = _INSTALL_SCRIPT.format(bridge=str(bridge_dir))
    try:
        log_fh = open(_install_log(settings), "wb")  # noqa: SIM115 — child inherits fd
    except OSError as exc:
        logger.error("Cannot open WhatsApp install log: %s", exc)
        return "failed"
    try:
        proc = await asyncio.create_subprocess_exec(
            "bash", "-c", script,
            cwd=str(bridge_dir),
            stdout=log_fh,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,  # detach: survives this request
        )
    except (FileNotFoundError, OSError) as exc:
        logger.error("Failed to spawn WhatsApp install job: %s", exc)
        return "failed"
    finally:
        log_fh.close()  # child keeps its own dup of the fd
    try:
        _install_pidfile(settings).write_text(str(proc.pid))
    except OSError:
        pass
    logger.info("WhatsApp bridge install started (pid %s)", proc.pid)
    return "installing"


async def _self_heal_asset() -> bool:
    """Fetch the pairing sidecar from the repo if it's missing locally.

    Old installs whose mgmt-upgrade ran before this asset was added to the
    upgrade file list won't have it on disk. Pull it on demand so /connect
    still works without needing a second upgrade.
    """
    try:
        _PAIR_ASSET.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(_PAIR_ASSET_RAW)
        if resp.status_code == 200 and resp.text.strip():
            _PAIR_ASSET.write_text(resp.text, encoding="utf-8")
            logger.info("Self-healed WhatsApp pairing sidecar asset from repo")
            return True
        logger.error("Pairing sidecar fetch returned HTTP %s", resp.status_code)
    except (httpx.RequestError, OSError) as exc:
        logger.error("Could not self-heal pairing sidecar asset: %s", exc)
    return False


async def _ensure_sidecar(settings: Settings) -> bool:
    """Spawn the pairing sidecar on the pair port if nothing is listening yet."""
    port = _pair_port(settings)
    if _port_open(port):
        return True

    if not _PAIR_ASSET.exists() and not await _self_heal_asset():
        logger.error("Pairing sidecar asset missing and unfetchable at %s", _PAIR_ASSET)
        return False

    bridge_dir = _bridge_dir(settings)
    # Copy the sidecar next to the bridge's node_modules so a bare
    # `import '@whiskeysockets/baileys'` resolves (ESM walks up node_modules).
    dest = bridge_dir / "hermes_pair.mjs"
    try:
        dest.write_text(_PAIR_ASSET.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError as exc:
        logger.error("Could not stage pairing sidecar into %s: %s", bridge_dir, exc)
        return False

    session_dir = _session_dir(settings)
    session_dir.mkdir(parents=True, exist_ok=True)
    pidfile = _pidfile(settings)

    env = os.environ.copy()
    env.setdefault("HOME", "/root")  # session dir is passed explicitly via --session
    merged = _merged_env(settings)
    if merged.get("WHATSAPP_DEBUG"):
        env["WHATSAPP_DEBUG"] = merged["WHATSAPP_DEBUG"]

    try:
        await asyncio.create_subprocess_exec(
            "node", str(dest),
            "--session", str(session_dir),
            "--port", str(port),
            "--pidfile", str(pidfile),
            cwd=str(bridge_dir),
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,  # detach: outlive this request
        )
    except (FileNotFoundError, OSError) as exc:
        logger.error("Failed to spawn WhatsApp pairing sidecar: %s", exc)
        return False

    for _ in range(20):
        if _port_open(port):
            return True
        await asyncio.sleep(0.5)
    return _port_open(port)


def _kill_sidecar(settings: Settings) -> None:
    """Terminate the pairing sidecar via its pidfile (best effort)."""
    pidfile = _pidfile(settings)
    try:
        pid = int(pidfile.read_text().strip())
    except (OSError, ValueError):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        pidfile.unlink()
    except OSError:
        pass


def _sidecar_base_url(settings: Settings) -> str:
    return f"http://127.0.0.1:{_pair_port(settings)}"


async def _sidecar_get(settings: Settings, path: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=_SIDECAR_TIMEOUT) as client:
        return await client.get(f"{_sidecar_base_url(settings)}{path}")


async def _sidecar_post(settings: Settings, path: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=_SIDECAR_TIMEOUT) as client:
        return await client.post(f"{_sidecar_base_url(settings)}{path}")


async def _bridge_connected(settings: Settings) -> bool:
    """True if the gateway-managed bridge is up and reports connected."""
    port = _bridge_port(settings)
    if not _port_open(port):
        return False
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"http://127.0.0.1:{port}/health")
        return resp.status_code == 200 and resp.json().get("status") == "connected"
    except (httpx.RequestError, ValueError):
        return False


# ── env writes ──────────────────────────────────────────────────────────────


def _set_both(settings: Settings, key: str, value: str) -> None:
    """Persist a key to both .env stores (systemd EnvironmentFile + Hermes store)."""
    set_env(settings.env_file, key, value)
    set_env(settings.hermes_home / ".env", key, value)


# ── endpoints ───────────────────────────────────────────────────────────────


@router.get("/api/whatsapp/status", response_model=ApiResponse)
async def whatsapp_status(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    """Aggregate state for the dashboard to poll.

    data.status ∈ {connected, pending, paired, not_installed, installing,
    install_failed, disconnected}:
      not_installed  — bridge deps not installed yet (show "Install" button)
      installing     — install job running (show progress)
      install_failed — last install failed (show retry + log)
      disconnected   — installed, nothing paired yet (show "Connect")
      pending        — pairing sidecar up, waiting for a QR scan
      paired         — creds saved but WhatsApp not enabled yet (call /enable)
      connected      — enabled + gateway bridge is live
    """
    merged = _merged_env(settings)
    enabled = merged.get(_ENABLED_KEY, "").strip().lower() in {"true", "1", "yes"}
    mode = merged.get(_MODE_KEY, "").strip() or "self-chat"
    allowed = merged.get(_ALLOWED_KEY, "").strip()
    install = _install_state(settings)  # installed | installing | failed | not_installed
    paired = _creds_path(settings).exists()

    sidecar_status = None
    qr_ready = False
    if _port_open(_pair_port(settings)):
        try:
            resp = await _sidecar_get(settings, "/health")
            if resp.status_code == 200:
                body = resp.json()
                sidecar_status = body.get("status")
                qr_ready = bool(body.get("qr"))
                if body.get("paired"):
                    paired = True
        except (httpx.RequestError, ValueError):
            pass

    bridge_live = await _bridge_connected(settings) if enabled else False

    if enabled and bridge_live:
        agg = "connected"
    elif sidecar_status == "pending" or qr_ready:
        agg = "pending"
    elif paired:
        agg = "paired"
    elif install == "installing":
        agg = "installing"
    elif install == "failed":
        agg = "install_failed"
    elif install != "installed":
        agg = "not_installed"
    else:
        agg = "disconnected"

    return ApiResponse(
        ok=True,
        data={
            "status": agg,
            "bridge_installed": install == "installed",
            "install_state": install,          # installed|installing|failed|not_installed
            "enabled": enabled,
            "mode": mode,
            "allowed_users": allowed,
            "paired": paired,
            "bridge_connected": bridge_live,
            "pairing": sidecar_status,
            "qr_ready": qr_ready,
            "valid_modes": sorted(_VALID_MODES),
        },
    )


@router.post("/api/whatsapp/install", response_model=ApiResponse)
async def whatsapp_install(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    """Install the WhatsApp bridge dependencies (Baileys) — the dashboard's
    "Cài đặt WhatsApp bridge" button.

    Runs a DETACHED job (git HTTPS rewrite + swap-if-low-RAM + npm install), so
    this returns immediately. Poll /api/whatsapp/install-status (or /status) for
    progress; only allow /connect once install_state == "installed".
    """
    state = await _start_install(settings)
    if state == "no_bridge":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Không tìm thấy thư mục WhatsApp bridge (hermes-agent chưa cài xong?).",
        )
    if state == "failed":
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không khởi động được tiến trình cài đặt. Xem log mgmt-api.",
        )
    return ApiResponse(ok=True, data={"install_state": state})


@router.get("/api/whatsapp/install-status", response_model=ApiResponse)
async def whatsapp_install_status(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    log_lines: int = 20,
) -> ApiResponse:
    """Progress of the bridge install: state + a tail of the install log."""
    state = _install_state(settings)
    log_lines = max(0, min(int(log_lines or 0), 200))
    tail: list[str] = []
    if log_lines:
        try:
            text = _install_log(settings).read_text(encoding="utf-8", errors="replace")
            tail = text.splitlines()[-log_lines:]
        except OSError:
            tail = []
    return ApiResponse(
        ok=True,
        data={"install_state": state, "installed": state == "installed", "log": tail},
    )


@router.post("/api/whatsapp/connect", response_model=ApiResponse)
async def whatsapp_connect(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    body: dict = Body(default={}),
) -> ApiResponse:
    """Start QR pairing. Returns immediately; poll /status + show /qr.

    Optional body {mode} pre-stores WHATSAPP_MODE so /enable can default to it.
    If already paired, returns status=paired (skip straight to /enable).
    """
    mode = str(body.get("mode", "")).strip().lower()
    if mode and mode not in _VALID_MODES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"mode phải là một trong: {', '.join(sorted(_VALID_MODES))}.",
        )
    if mode:
        _set_both(settings, _MODE_KEY, mode)

    if _creds_path(settings).exists():
        return ApiResponse(ok=True, data={"status": "paired", "qr_url": None})

    # Gate: bridge deps must be installed first (dashboard "Install" button).
    if not _bridge_installed(settings):
        state = _install_state(settings)
        detail = (
            "WhatsApp bridge đang được cài, đợi cài xong rồi kết nối."
            if state == "installing"
            else "Chưa cài WhatsApp bridge. Bấm 'Cài đặt WhatsApp bridge' trước khi kết nối."
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)

    if not await _ensure_sidecar(settings):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Không khởi động được tiến trình quét QR WhatsApp. Xem log mgmt-api.",
        )
    return ApiResponse(ok=True, data={"status": "pending", "qr_url": "/api/whatsapp/qr"})


@router.get("/api/whatsapp/qr")
async def whatsapp_qr(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> Response:
    """Render the current pairing QR as a PNG for <img src=/api/whatsapp/qr>.

    404 while the QR is still being generated (retry for a couple seconds after
    /connect) or once the phone has already scanned.
    """
    try:
        resp = await _sidecar_get(settings, "/health")
    except httpx.RequestError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tiến trình quét QR chưa chạy — gọi /api/whatsapp/connect trước.",
        )
    qr_payload = None
    if resp.status_code == 200:
        try:
            qr_payload = resp.json().get("qr")
        except ValueError:
            qr_payload = None
    if not qr_payload:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mã QR chưa sẵn sàng, thử lại sau 1-2 giây.",
        )

    import segno

    buf = io.BytesIO()
    segno.make(qr_payload, error="m").save(buf, kind="png", scale=6, border=2)
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/api/whatsapp/enable", response_model=ApiResponse)
async def whatsapp_enable(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    body: dict = Body(default={}),
) -> ApiResponse:
    """Enable WhatsApp: persist env + restart the gateway.

    Requires a paired session (creds.json). Body:
      { "mode": "bot"|"self-chat", "allowed_users": "15551234567,..." | "*" }
    For mode=bot with no allowed_users the bridge rejects all inbound messages,
    so we require it (use "*" to allow everyone).
    """
    if not _creds_path(settings).exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Chưa quét QR (thiếu creds.json). Gọi /api/whatsapp/connect và quét trước.",
        )

    merged = _merged_env(settings)
    mode = str(body.get("mode", "")).strip().lower() or merged.get(_MODE_KEY, "").strip() or "self-chat"
    if mode not in _VALID_MODES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"mode phải là một trong: {', '.join(sorted(_VALID_MODES))}.",
        )
    allowed = str(body.get("allowed_users", "")).strip()
    if not allowed:
        allowed = merged.get(_ALLOWED_KEY, "").strip()
    if mode == "bot" and not allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "mode=bot cần allowed_users (số điện thoại có mã quốc gia, cách nhau "
                "dấu phẩy) hoặc '*' để cho phép tất cả."
            ),
        )

    _set_both(settings, _MODE_KEY, mode)
    if allowed:
        _set_both(settings, _ALLOWED_KEY, allowed)
    _set_both(settings, _ENABLED_KEY, "true")

    # Free the WhatsApp socket the pairing sidecar may still hold, so the
    # gateway bridge can reconnect without a stream:conflict.
    if _port_open(_pair_port(settings)):
        try:
            await _sidecar_post(settings, "/shutdown")
        except httpx.RequestError:
            _kill_sidecar(settings)
    _kill_sidecar(settings)

    try:
        await restart("hermes-gateway", settings.allowed_services)
    except Exception as exc:  # noqa: BLE001 — restart failure is non-fatal to the write
        logger.error("gateway restart after WhatsApp enable failed: %s", exc)
        return ApiResponse(
            ok=True,
            data={"status": "enabled", "mode": mode, "restarted": False},
        )
    return ApiResponse(ok=True, data={"status": "enabled", "mode": mode, "restarted": True})


@router.post("/api/whatsapp/disconnect", response_model=ApiResponse)
async def whatsapp_disconnect(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    """Log out + clear the WhatsApp session and disable the platform.

    Deletes creds (forces a fresh QR next time), sets WHATSAPP_ENABLED=false,
    and restarts the gateway so the adapter stops.
    """
    # Ask the sidecar to log out if it is running (clears session dir too).
    if _port_open(_pair_port(settings)):
        try:
            await _sidecar_post(settings, "/logout")
        except httpx.RequestError:
            pass
    _kill_sidecar(settings)

    # Remove the shared session dir regardless (sidecar may not be running).
    import shutil

    try:
        shutil.rmtree(_session_dir(settings))
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Could not remove WhatsApp session dir: %s", exc)

    _set_both(settings, _ENABLED_KEY, "false")
    try:
        await restart("hermes-gateway", settings.allowed_services)
    except Exception as exc:  # noqa: BLE001
        logger.error("gateway restart after WhatsApp disconnect failed: %s", exc)
    return ApiResponse(ok=True, data={"status": "disconnected"})


@router.get("/api/whatsapp/logs", response_model=ApiResponse)
async def whatsapp_logs(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    lines: int = 200,
) -> ApiResponse:
    """Tail WhatsApp-related lines from Hermes agent/gateway logs."""
    lines = max(10, min(int(lines or 200), 1000))
    out: list[str] = []
    for fname in ("agent.log", "gateway.log"):
        p = settings.hermes_home / "logs" / fname
        try:
            with open(p, "rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - 1024 * 1024))
                text = fh.read().decode("utf-8", errors="replace")
        except OSError:
            continue
        out.extend(
            f"[{fname}] {ln}"
            for ln in text.splitlines()
            if "whatsapp" in ln.lower()
        )
    return ApiResponse(ok=True, data={"lines": out[-lines:], "count": min(len(out), lines)})
