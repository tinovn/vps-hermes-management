from __future__ import annotations

import asyncio
import json
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

_MGMT_DIR = "/opt/hermes-mgmt"
_MGMT_VENV_UV = "/opt/hermes-mgmt/.venv/bin/uv"
_MGMT_REPO_RAW = "https://raw.githubusercontent.com/tinovn/vps-hermes-management/main"
_GH_API = "https://api.github.com/repos/tinovn/vps-hermes-management"
_GH_REF = "main"
# Static base files (package core + pyproject). Everything under routes/,
# config/rules and config/roles is discovered DYNAMICALLY via the GitHub
# contents API at upgrade time — so new routes/rules/roles never need to be
# added here by hand.
_MGMT_FILES = (
    "pyproject.toml",
    "hermes_mgmt/__init__.py",
    "hermes_mgmt/main.py",
    "hermes_mgmt/config.py",
    "hermes_mgmt/auth.py",
    "hermes_mgmt/deps.py",
    "hermes_mgmt/models.py",
    "hermes_mgmt/env_file.py",
    "hermes_mgmt/systemd_ctl.py",
    "hermes_mgmt/cli_runner.py",
    "hermes_mgmt/hermes_fs.py",
)


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


async def _do_upgrade_mgmt(settings: Settings) -> None:
    """Refresh management-api sources from raw GitHub, reinstall, restart unit.

    Two-path strategy:
      - If /opt/hermes-mgmt is a git checkout: ``git pull``.
      - Otherwise (the default install layout): re-download each known file
        from the canonical raw URL.

    Restart is fire-and-forget because the request must return before the
    uvicorn worker dies on `systemctl restart hermes-mgmt`. We give the
    background task ~3s to land the install before triggering restart.
    """
    try:
        mgmt_path = Path(_MGMT_DIR)
        git_dir = mgmt_path / ".git"

        if git_dir.exists():
            logger.info("mgmt upgrade: git pull in %s", _MGMT_DIR)
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", _MGMT_DIR, "pull",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await proc.communicate()
            logger.info(
                "git pull: %s %s",
                stdout_b.decode(errors="replace"),
                stderr_b.decode(errors="replace"),
            )
        else:
            # Fully DYNAMIC refresh — list directories via the GitHub contents
            # API instead of any hardcoded file list, so new routes/rules/roles
            # added upstream are always pulled (no list to keep in sync).
            # Base files (pyproject, package modules) still come from the small
            # static set; everything under routes/, config/rules, config/roles
            # is discovered live.
            async def _fetch(rel_repo_path: str, dest: Path) -> bool:
                dest.parent.mkdir(parents=True, exist_ok=True)
                proc = await asyncio.create_subprocess_exec(
                    "curl", "-fsSL", f"{_MGMT_REPO_RAW}/{rel_repo_path}", "-o", str(dest),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                _, err = await proc.communicate()
                if proc.returncode != 0:
                    logger.error("mgmt fetch failed: %s — %s", rel_repo_path, err.decode(errors="replace"))
                    return False
                return True

            # 1. Static base files (package core + pyproject).
            for rel in _MGMT_FILES:
                await _fetch(f"management-api/{rel}", mgmt_path / rel)

            # 2. Dynamic dirs via GitHub contents API (no jq; parse names).
            async def _gh_list(repo_subdir: str) -> list[str]:
                api = f"{_GH_API}/contents/{repo_subdir}?ref={_GH_REF}"
                proc = await asyncio.create_subprocess_exec(
                    "curl", "-fsSL", "-H", "Accept: application/vnd.github+json", api,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                out, _ = await proc.communicate()
                if proc.returncode != 0:
                    logger.warning("GitHub list failed for %s", repo_subdir)
                    return []
                try:
                    items = json.loads(out.decode(errors="replace"))
                    return [it["name"] for it in items if it.get("type") == "file"]
                except (json.JSONDecodeError, TypeError, KeyError):
                    return []

            for subdir in ("management-api/hermes_mgmt/routes", "config/rules", "config/roles"):
                names = await _gh_list(subdir)
                # Where these files land locally: routes go under mgmt pkg,
                # config goes under /opt/hermes-mgmt/config to match roles.py.
                local_base = mgmt_path / (
                    "hermes_mgmt/routes" if subdir.endswith("/routes")
                    else subdir.replace("config/", "config/")
                )
                for name in names:
                    await _fetch(f"{subdir}/{name}", local_base / name)
                if names:
                    logger.info("mgmt upgrade: refreshed %s (%d files)", subdir, len(names))

        uv_bin = _MGMT_VENV_UV
        if not Path(uv_bin).exists():
            uv_bin = shutil.which("uv") or "uv"
        proc2 = await asyncio.create_subprocess_exec(
            uv_bin, "pip", "install", "-e", ".",
            cwd=_MGMT_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b2, stderr_b2 = await proc2.communicate()
        logger.info(
            "mgmt uv install: %s %s",
            stdout_b2.decode(errors="replace"),
            stderr_b2.decode(errors="replace"),
        )

        # Also refresh the Zalo plugin (git pull + npm) and restart the gateway
        # so plugin fixes land on upgrade — the plugin is only cloned at install
        # time and never updates otherwise.
        await _update_zalo_plugin(settings)

        # Restart self last; this terminates the current uvicorn worker but
        # systemd respawns it immediately (Restart=always).
        await asyncio.sleep(1)
        allowed = settings.allowed_services
        if "hermes-mgmt" in allowed:
            logger.info("Restarting hermes-mgmt to load new code...")
            await restart("hermes-mgmt", allowed)
    except Exception as exc:
        logger.error("Mgmt upgrade failed: %s", exc)


_ZALO_PLUGIN_DIR = "/root/.hermes/plugins/zalo-personal"
_HERMES_ENV_FILE = Path("/opt/hermes/.env")
_HERMES_HOME_REAL = Path("/root/.hermes")


def _remap_zalo_sessions() -> None:
    """Fix the sessions-store mapping on OLD VPS installs (idempotent).

    The plugin adapter's owner-gate reads ``${HERMES_HOME}/sessions/sessions.json``
    and historically defaulted to ``/opt/data`` when HERMES_HOME was unset —
    but the gateway (HOME=/root) writes sessions to ``/root/.hermes/sessions``.
    Result on old installs: gate can't resolve the session → fail-closed →
    every zalo_* tool denied, even for the owner.

    Two-layer fix, both safe to re-run:
      1. Append ``HERMES_HOME=/root/.hermes`` to /opt/hermes/.env if missing
         (deterministic — the adapter checks the env var first).
      2. Symlink ``/opt/data/sessions -> /root/.hermes/sessions`` as belt &
         braces for plugin versions that still read /opt/data directly.
    """
    try:
        from hermes_mgmt.env_file import read_env, set_env

        if _HERMES_ENV_FILE.exists() and "HERMES_HOME" not in read_env(_HERMES_ENV_FILE):
            set_env(_HERMES_ENV_FILE, "HERMES_HOME", str(_HERMES_HOME_REAL))
            logger.info("zalo remap: appended HERMES_HOME=%s to %s", _HERMES_HOME_REAL, _HERMES_ENV_FILE)
    except Exception as exc:
        logger.error("zalo remap: HERMES_HOME env fix failed: %s", exc)

    try:
        real_sessions = _HERMES_HOME_REAL / "sessions"
        opt_sessions = Path("/opt/data/sessions")
        real_sessions.mkdir(parents=True, exist_ok=True)
        opt_sessions.parent.mkdir(parents=True, exist_ok=True)
        # Only create when nothing is there: an existing REAL dir might hold
        # data we must not shadow (the env fix above covers that case anyway).
        if not opt_sessions.exists() and not opt_sessions.is_symlink():
            opt_sessions.symlink_to(real_sessions)
            logger.info("zalo remap: symlinked %s -> %s", opt_sessions, real_sessions)
    except Exception as exc:
        logger.error("zalo remap: sessions symlink failed: %s", exc)


async def _update_zalo_plugin(settings: Settings) -> None:
    """git pull the Zalo plugin + npm install its sidecar, then restart gateway.

    Best-effort: any step failing is logged but never aborts the mgmt upgrade
    (the plugin may be absent if installed with --skip-zalo).
    """
    plugin = Path(_ZALO_PLUGIN_DIR)
    if not (plugin / ".git").exists():
        logger.info("Zalo plugin not a git checkout (%s) — skipping plugin update", plugin)
        return
    # Remap sessions store FIRST so the gateway restart below picks it up —
    # this rescues old VPSes where the owner-gate blocked every zalo_* tool.
    _remap_zalo_sessions()
    try:
        # Stash any runtime hand-edits (e.g. hotfixes the on-VPS agent applied)
        # so --ff-only never fails on a dirty tree; upstream is canonical.
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(plugin), "stash",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        await proc.communicate()
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(plugin), "pull", "--ff-only",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        logger.info("zalo plugin git pull: %s", out.decode(errors="replace")[-500:])
    except Exception as exc:
        logger.error("zalo plugin git pull failed: %s", exc)
        return

    sidecar = plugin / "sidecar"
    if (sidecar / "package.json").exists():
        try:
            proc = await asyncio.create_subprocess_exec(
                "npm", "install", "--no-audit", "--no-fund", "--loglevel=error",
                cwd=str(sidecar),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            await asyncio.wait_for(proc.communicate(), timeout=180)
        except Exception as exc:
            logger.error("zalo sidecar npm install failed: %s", exc)

    # Restart gateway so the updated adapter + sidecar take effect.
    try:
        if "hermes-gateway" in settings.allowed_services:
            await restart("hermes-gateway", settings.allowed_services)
            logger.info("gateway restarted after Zalo plugin update")
    except Exception as exc:
        logger.error("gateway restart after Zalo plugin update failed: %s", exc)


@router.post(
    "/api/upgrade-mgmt",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ApiResponse,
)
async def upgrade_mgmt(
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    """Upgrade the management-api in place + refresh the Zalo plugin.

    Idempotent: re-pulls all Python sources + routes/config (dynamic via the
    GitHub API), re-runs `uv pip install`, git-pulls the Zalo plugin and
    npm-installs its sidecar (restarting the gateway), then restarts
    `hermes-mgmt.service`. Returns 202 before the unit cycles.
    """
    background_tasks.add_task(_do_upgrade_mgmt, settings)
    return ApiResponse(
        ok=True,
        data={"message": "Management API upgrade started in background"},
    )


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
