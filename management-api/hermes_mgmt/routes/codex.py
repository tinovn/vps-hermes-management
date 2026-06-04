"""OpenAI Codex OAuth — device-code login from the dashboard.

Codex authenticates via device code: `hermes auth add codex-oauth` prints a URL
+ user code, then polls OpenAI until the user enters the code in a browser and
writes the token to ~/.hermes/auth.json. That flow is interactive, so for a
low-tech dashboard we:

  start  → spawn `hermes auth add codex-oauth` detached, scrape the URL + code
           from its output, return them. The process keeps polling in the
           background and writes auth.json when the user completes the browser
           step.
  status → report whether auth.json now has a codex/openai-codex entry. On first
           success, set config.yaml model.provider=codex + restart the gateway
           so the bot starts using Codex immediately.
  import → fallback: paste an auth.json obtained elsewhere (Codex CLI / another
           machine) instead of doing the device flow here.

Token lives in ~/.hermes/auth.json (HERMES_HOME). No secrets are returned by the
API — only the public device URL + user code.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, status

from hermes_mgmt.config import Settings
from hermes_mgmt.deps import get_settings_dep, require_auth
from hermes_mgmt.models import ApiResponse
from hermes_mgmt.systemd_ctl import restart

logger = logging.getLogger(__name__)

router = APIRouter(tags=["codex"], dependencies=[Depends(require_auth)])

_HERMES_BIN = "/usr/local/bin/hermes"
_AUTH_PROVIDER = "openai-codex"
_MODEL_PROVIDER = "codex"
# Keys an auth.json entry may use for the Codex/OpenAI-Codex provider.
_CODEX_AUTH_KEYS = ("codex", "openai-codex", "codex-oauth")
# Strip ANSI colour codes the CLI emits around the URL/code.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
# Device-flow output (verified against live CLI):
#   1. Open this URL: https://auth.openai.com/codex/device
#   2. Enter this code: 41JU-ST9W8   (4 chars - 5 chars, hyphenated)
_URL_RE = re.compile(r"https://\S+")
_CODE_RE = re.compile(r"\b([A-Z0-9]{3,5}-[A-Z0-9]{3,5})\b")

# In-process handle for the running device-flow subprocess (one at a time).
_flow: dict = {"proc": None, "url": None, "code": None, "started": 0.0, "output": ""}


def _auth_file(settings: Settings) -> Path:
    return settings.hermes_home / "auth.json"


def _has_codex_token(settings: Settings) -> bool:
    """True if auth.json contains a Codex/OpenAI-Codex credential entry."""
    f = _auth_file(settings)
    if not f.exists():
        return False
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    # auth.json shape varies; accept either top-level keys or a providers map.
    candidates = set(data.keys()) if isinstance(data, dict) else set()
    if isinstance(data, dict) and isinstance(data.get("providers"), dict):
        candidates |= set(data["providers"].keys())
    return any(k in candidates for k in _CODEX_AUTH_KEYS)


def _set_codex_model(settings: Settings) -> None:
    """Point config.yaml at the codex provider (idempotent)."""
    import yaml

    cfg = settings.hermes_home / "config.yaml"
    try:
        data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        data = {}
    model = data.get("model")
    if not isinstance(model, dict):
        model = {}
    model["provider"] = _MODEL_PROVIDER
    data["model"] = model
    cfg.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


# ─── start ───────────────────────────────────────────────────────────────────


@router.post("/api/codex/auth/start", response_model=ApiResponse)
async def codex_start() -> ApiResponse:
    """Start the Codex device-code login. Returns {url, code} to show the user.

    The subprocess keeps polling OpenAI in the background; poll /status to learn
    when the user has completed the browser step.
    """
    # If a flow is already live and recent, return its URL/code instead of
    # spawning a second one.
    existing = _flow.get("proc")
    if existing is not None and existing.returncode is None and _flow.get("url"):
        return ApiResponse(
            ok=True,
            data={"status": "pending", "url": _flow["url"], "code": _flow["code"]},
        )

    env = os.environ.copy()
    env.setdefault("HERMES_HOME", "/root/.hermes")
    env.setdefault("HOME", "/root")
    # --no-browser + --manual-paste forces the headless device-code path (print
    # URL + code instead of trying to open a browser on the server).
    env["PYTHONUNBUFFERED"] = "1"
    try:
        proc = await asyncio.create_subprocess_exec(
            _HERMES_BIN, "auth", "add", _AUTH_PROVIDER,
            "--type", "oauth", "--no-browser", "--manual-paste",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL,
            env=env,
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="hermes CLI chưa sẵn sàng.",
        )

    _flow.update({"proc": proc, "url": None, "code": None, "started": time.time(), "output": ""})

    # Read output for up to ~15s to capture the URL + code, then leave the
    # process running in the background to keep polling.
    url = code = None
    deadline = time.time() + 15.0
    buf = ""
    while time.time() < deadline and proc.stdout is not None:
        try:
            chunk = await asyncio.wait_for(proc.stdout.read(256), timeout=2.0)
        except asyncio.TimeoutError:
            chunk = b""
        if chunk:
            buf += chunk.decode(errors="replace")
            _flow["output"] = buf
            clean = _ANSI_RE.sub("", buf)  # drop colour codes before matching
            if url is None:
                m = _URL_RE.search(clean)
                if m:
                    url = m.group(0).rstrip(".,)")
            if code is None:
                m = _CODE_RE.search(clean)
                if m:
                    code = m.group(1)
        if url and code:
            break
        if proc.returncode is not None:
            break

    _flow["url"], _flow["code"] = url, code

    if not url:
        # Couldn't parse — surface raw output so we can adjust the regex.
        return ApiResponse(
            ok=False,
            data={"status": "error", "raw": buf[-500:]},
            error="Không đọc được URL device-code từ hermes. Xem 'raw' để chỉnh.",
        )
    return ApiResponse(ok=True, data={"status": "pending", "url": url, "code": code})


# ─── status ──────────────────────────────────────────────────────────────────


@router.get("/api/codex/auth/status", response_model=ApiResponse)
async def codex_status(
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    """Report Codex auth state. On first success, set model + restart gateway."""
    if _has_codex_token(settings):
        # Wire the bot to Codex the first time we observe a token. Idempotent —
        # config write + restart are cheap and only matter once.
        try:
            import yaml

            cfg = settings.hermes_home / "config.yaml"
            data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
            already = (data.get("model") or {}).get("provider") == _MODEL_PROVIDER
        except Exception:
            already = False
        if not already:
            _set_codex_model(settings)

            async def _restart_gw() -> None:
                try:
                    await restart("hermes-gateway", settings.allowed_services)
                except Exception as exc:
                    logger.error("gateway restart after Codex auth failed: %s", exc)

            background_tasks.add_task(_restart_gw)
        return ApiResponse(ok=True, data={"status": "connected", "model_set": True})

    proc = _flow.get("proc")
    if proc is not None and proc.returncode is None:
        return ApiResponse(
            ok=True,
            data={"status": "pending", "url": _flow.get("url"), "code": _flow.get("code")},
        )
    return ApiResponse(ok=True, data={"status": "disconnected"})


# ─── import (fallback) ───────────────────────────────────────────────────────


@router.post("/api/codex/auth/import", response_model=ApiResponse)
async def codex_import(
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    body: dict = Body(...),
) -> ApiResponse:
    """Import an auth.json obtained elsewhere (Codex CLI / another machine).

    Body: { "auth_json": <object|string> } — full ~/.hermes/auth.json content.
    Validates it parses + has a codex entry, writes it, sets model + restarts.
    """
    raw = body.get("auth_json")
    if raw is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Thiếu auth_json.")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"auth_json không phải JSON hợp lệ: {exc}",
            )
    elif isinstance(raw, dict):
        parsed = raw
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="auth_json sai kiểu.")

    keys = set(parsed.keys())
    if isinstance(parsed.get("providers"), dict):
        keys |= set(parsed["providers"].keys())
    if not any(k in keys for k in _CODEX_AUTH_KEYS):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"auth.json không có entry Codex ({', '.join(_CODEX_AUTH_KEYS)}).",
        )

    f = _auth_file(settings)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(parsed, indent=2), encoding="utf-8")
    f.chmod(0o600)
    _set_codex_model(settings)

    async def _restart_gw() -> None:
        try:
            await restart("hermes-gateway", settings.allowed_services)
        except Exception as exc:
            logger.error("gateway restart after Codex import failed: %s", exc)

    background_tasks.add_task(_restart_gw)
    return ApiResponse(ok=True, data={"status": "connected", "imported": True})
