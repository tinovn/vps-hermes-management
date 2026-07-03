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
# Upstream provider registry id. "codex" was our legacy alias — recognize it
# when reading, but always WRITE "openai-codex".
_MODEL_PROVIDER = "openai-codex"
_MODEL_PROVIDER_ALIASES = ("codex", "openai-codex")
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
    """True if auth.json contains a Codex/OpenAI-Codex credential entry.

    auth.json shape varies by Hermes version — accept any of:
      - legacy top-level keys:        {"codex": {...}} / {"openai-codex": {...}}
      - providers map:                {"providers": {"openai-codex": {...}}}
      - v1 credential pool:           {"version": 1, "credential_pool":
                                        {"openai-codex": [<credential>, ...]},
                                        "active_provider": "openai-codex"}
    """
    f = _auth_file(settings)
    if not f.exists():
        return False
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(data, dict):
        return False
    candidates = set(data.keys())
    if isinstance(data.get("providers"), dict):
        candidates |= set(data["providers"].keys())
    pool = data.get("credential_pool")
    if isinstance(pool, dict):
        # Only providers that still hold at least one credential entry.
        candidates |= {k for k, v in pool.items() if v}
    return any(k in candidates for k in _CODEX_AUTH_KEYS)


def _set_codex_model(settings: Settings) -> None:
    """Point config.yaml at the codex provider + a supported default model.

    model.default MUST be a non-empty supported slug: the cron scheduler reads
    it directly (no catalog fallback like the chat path) and an empty value
    crashes every cron job with "Codex Responses request 'model' must be a
    non-empty string". Idempotent.
    """
    import yaml

    from hermes_mgmt.routes.config_routes import resolve_codex_model

    cfg = settings.hermes_home / "config.yaml"
    try:
        data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        data = {}
    model = data.get("model")
    if not isinstance(model, dict):
        model = {}
    model["provider"] = _MODEL_PROVIDER
    # Clamp whatever is there (empty, dead slug...) to a supported model.
    model["default"] = resolve_codex_model(str(model.get("default") or ""))
    data["model"] = model
    cfg.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def sync_active_provider(settings: Settings, provider: str) -> None:
    """Keep auth.json active_provider consistent with the chosen chat provider.

    Hermes prefers auth.json active_provider over config.yaml model.provider —
    so switching to an API-key provider while active_provider=openai-codex
    silently keeps routing through Codex, and switching back to Codex needs
    active_provider restored (credentials stay in credential_pool, no re-OAuth).
    """
    af = _auth_file(settings)
    if not af.exists():
        return
    try:
        data = json.loads(af.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if not isinstance(data, dict):
        return

    active = data.get("active_provider")
    changed = False
    if provider in _MODEL_PROVIDER_ALIASES:
        if _has_codex_token(settings) and active not in _CODEX_AUTH_KEYS:
            data["active_provider"] = _AUTH_PROVIDER
            changed = True
    elif active in _CODEX_AUTH_KEYS:
        data["active_provider"] = None
        changed = True

    if changed:
        af.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("auth.json active_provider -> %s", data["active_provider"])


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
    # --no-browser forces the headless device-code path (print URL + code
    # instead of trying to open a browser on the server). Do NOT add
    # --manual-paste: Hermes >= 0.18 dropped that flag and argparse exits with
    # a usage error before printing any URL.
    env["PYTHONUNBUFFERED"] = "1"
    try:
        proc = await asyncio.create_subprocess_exec(
            _HERMES_BIN, "auth", "add", _AUTH_PROVIDER,
            "--type", "oauth", "--no-browser",
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
    """Report Codex auth state. Pin the model only when appropriate.

    The dashboard polls this endpoint to render the ChatGPT badge, so it MUST
    NOT fight an explicit provider choice: if the user configured another
    provider (API key) while a Codex token still sits in auth.json, leave
    config.yaml alone (else every poll flips the provider back to Codex and
    restarts the gateway — "API key config never saves"). We only pin when:
      - a device flow started from the dashboard just completed, or
      - no provider is configured at all (fresh install / stray import), or
      - provider is already codex but default model is missing/dead (repair).
    """
    if _has_codex_token(settings):
        try:
            import yaml

            from hermes_mgmt.routes.config_routes import CODEX_SUPPORTED_MODELS

            cfg = settings.hermes_home / "config.yaml"
            data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
            model_cfg = data.get("model") or {}
            provider_cfg = str(model_cfg.get("provider") or "").strip()
            is_codex_cfg = provider_cfg in _MODEL_PROVIDER_ALIASES
            already = (
                provider_cfg == _MODEL_PROVIDER
                and model_cfg.get("default") in CODEX_SUPPORTED_MODELS
            )
        except Exception:
            provider_cfg, is_codex_cfg, already = "", False, False

        flow_completed = _flow.get("proc") is not None
        should_pin = not already and (flow_completed or not provider_cfg or is_codex_cfg)
        if should_pin:
            _set_codex_model(settings)
            sync_active_provider(settings, _MODEL_PROVIDER)
            _flow["proc"] = None  # consume the flow so later polls stay passive

            async def _restart_gw() -> None:
                try:
                    await restart("hermes-gateway", settings.allowed_services)
                except Exception as exc:
                    logger.error("gateway restart after Codex auth failed: %s", exc)

            background_tasks.add_task(_restart_gw)
        # active: Codex is the provider the bot actually uses. A token can sit
        # in auth.json while another provider is selected ("connected" badge,
        # active=false) — re-selecting Codex then needs no new OAuth.
        return ApiResponse(
            ok=True,
            data={
                "status": "connected",
                "model_set": should_pin or already,
                "active": should_pin or is_codex_cfg,
            },
        )

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
    sync_active_provider(settings, _MODEL_PROVIDER)

    async def _restart_gw() -> None:
        try:
            await restart("hermes-gateway", settings.allowed_services)
        except Exception as exc:
            logger.error("gateway restart after Codex import failed: %s", exc)

    background_tasks.add_task(_restart_gw)
    return ApiResponse(ok=True, data={"status": "connected", "imported": True})


# ─── disable / logout ────────────────────────────────────────────────────────


@router.post("/api/codex/auth/disable", response_model=ApiResponse)
async def codex_disable(
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    body: dict = Body(default={}),
) -> ApiResponse:
    """Disconnect Codex OAuth so another provider can be selected.

    Codex OAuth login records active_provider=openai-codex in auth.json, which
    Hermes prefers over config.yaml — so the dashboard can't switch providers
    until Codex is removed. This runs `hermes auth remove openai-codex`, then
    defensively clears the codex entry + active_provider from auth.json and
    strips model.provider=codex from config.yaml so the gateway stops requesting
    Codex. Body {"to_provider": "deepseek"} optionally repoints config there.
    """
    # 1. Best-effort CLI removal (handles Hermes internal bookkeeping).
    try:
        proc = await asyncio.create_subprocess_exec(
            _HERMES_BIN, "auth", "remove", _AUTH_PROVIDER,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL,
            env={**os.environ, "HERMES_HOME": str(settings.hermes_home), "HOME": "/root"},
        )
        await asyncio.wait_for(proc.communicate(), timeout=15)
    except Exception as exc:
        logger.warning("hermes auth remove openai-codex failed (continuing): %s", exc)

    # 2. Defensive cleanup of auth.json (drop codex entries in every shape:
    #    legacy top-level, providers map, v1 credential_pool + active_provider).
    af = _auth_file(settings)
    if af.exists():
        try:
            data = json.loads(af.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                provs = data.get("providers")
                if isinstance(provs, dict):
                    for k in _CODEX_AUTH_KEYS:
                        provs.pop(k, None)
                pool = data.get("credential_pool")
                if isinstance(pool, dict):
                    for k in _CODEX_AUTH_KEYS:
                        pool.pop(k, None)
                for k in _CODEX_AUTH_KEYS:
                    data.pop(k, None)
                if data.get("active_provider") in _CODEX_AUTH_KEYS:
                    data["active_provider"] = None
                af.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("auth.json cleanup failed: %s", exc)

    # 3. Repoint config.yaml away from codex (so gateway stops requesting it).
    import yaml

    to_provider = (body.get("to_provider") or "").strip()
    cfg = settings.hermes_home / "config.yaml"
    try:
        cfg_data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        cfg_data = {}
    from hermes_mgmt.routes.config_routes import CODEX_SUPPORTED_MODELS

    model_cfg = cfg_data.get("model") if isinstance(cfg_data.get("model"), dict) else {}
    if model_cfg.get("provider") in _MODEL_PROVIDER_ALIASES:
        if to_provider:
            model_cfg["provider"] = to_provider
        else:
            model_cfg.pop("provider", None)
        # Don't let the new provider inherit a Codex-only model.default —
        # the follow-up PUT /api/config/provider sets the right one.
        if model_cfg.get("default") in CODEX_SUPPORTED_MODELS:
            model_cfg.pop("default", None)
        cfg_data["model"] = model_cfg
        cfg.write_text(yaml.safe_dump(cfg_data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    async def _restart_gw() -> None:
        try:
            await restart("hermes-gateway", settings.allowed_services)
        except Exception as exc:
            logger.error("gateway restart after Codex disable failed: %s", exc)

    background_tasks.add_task(_restart_gw)
    return ApiResponse(
        ok=True,
        data={"status": "disconnected", "to_provider": to_provider or None},
    )
