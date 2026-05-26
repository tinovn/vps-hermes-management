from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from hermes_mgmt.models import CliResponse

logger = logging.getLogger(__name__)

HERMES_WHITELIST: frozenset[str] = frozenset(
    {
        # diagnostic / read-only
        "version",
        "status",
        "doctor",
        "dump",
        "debug",
        "insights",
        "logs",
        # configuration
        "config",
        "model",
        "fallback",
        # auth + credentials
        "auth",
        # sessions / memory / context
        "sessions",
        "memory",
        "checkpoints",
        # skills + tools + bundles
        "skills",
        "bundles",
        "tools",
        # gateway + messaging
        "gateway",
        "webhook",
        "whatsapp",
        # scheduling + kanban
        "cron",
        "kanban",
        # maintenance
        "curator",
        "profile",
        "backup",
        "import",
        "lsp",
        "pairing",
        # one-shot — surface kept narrow; setup is interactive in CLI but
        # callers may need to run with --non-interactive
        "setup",
    }
)

HERMES_BIN = "/usr/local/bin/hermes"


async def run_hermes(
    subcommand: str,
    args: list[str],
    env_overrides: dict[str, Any] | None = None,
    timeout: int = 30,
) -> CliResponse:
    """Run a hermes CLI subcommand and return structured output.

    Raises ValueError if subcommand is not in the whitelist.
    Returns CliResponse with stdout/stderr/exit_code on subprocess errors.
    """
    if subcommand not in HERMES_WHITELIST:
        raise ValueError(
            f"Subcommand '{subcommand}' is not allowed. "
            f"Permitted: {sorted(HERMES_WHITELIST)}"
        )

    env = os.environ.copy()
    if env_overrides:
        env.update({k: str(v) for k, v in env_overrides.items()})

    cmd = [HERMES_BIN, subcommand, *args]
    logger.debug("Running hermes: %s", cmd)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            return CliResponse(
                stdout="",
                stderr=f"Command timed out after {timeout}s",
                exit_code=124,
            )

        return CliResponse(
            stdout=stdout_b.decode(errors="replace"),
            stderr=stderr_b.decode(errors="replace"),
            exit_code=proc.returncode if proc.returncode is not None else 1,
        )

    except FileNotFoundError:
        return CliResponse(
            stdout="",
            stderr=f"hermes binary not found at {HERMES_BIN}",
            exit_code=127,
        )
    except Exception as exc:
        logger.exception("Unexpected error running hermes %s", subcommand)
        return CliResponse(stdout="", stderr=str(exc), exit_code=1)
