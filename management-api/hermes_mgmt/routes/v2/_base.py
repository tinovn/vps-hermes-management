"""Shared helpers for v2 routes."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import HTTPException, status

from hermes_mgmt.cli_runner import run_hermes
from hermes_mgmt.config import Settings
from hermes_mgmt.models import CliResponse
from hermes_mgmt.routes.v2._parsers import parse_lines, strip_ansi


async def run_for(
    settings: Settings,
    subcommand: str,
    args: list[str],
    timeout: int = 30,
) -> CliResponse:
    """Run a hermes subcommand with HERMES_HOME pinned to the install dir.

    Without env_overrides, the systemd EnvironmentFile= for hermes-mgmt does
    not reliably propagate HERMES_HOME — the CLI would default to ~/.hermes
    and edit the wrong store. See memory: project-hermes-vps-dual-env-paths.
    """
    return await run_hermes(
        subcommand,
        args,
        env_overrides={"HERMES_HOME": str(settings.hermes_home)},
        timeout=timeout,
    )


def raise_for_exit_code(result: CliResponse, label: str) -> None:
    """Convert a non-zero CLI exit into a 500 with CLI stderr in the detail."""
    if result.exit_code != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{label} (exit {result.exit_code}): {result.stderr.strip() or result.stdout.strip()}",
        )


def cli_payload(
    result: CliResponse,
    parser: Callable[[str], Any] | None = None,
) -> dict:
    """Standard data payload for v2 endpoints.

    Parameters
    ----------
    parser
        Optional callable applied to stdout to produce structured JSON in
        `parsed`. If omitted, falls back to ``parse_lines`` (list of
        non-empty stripped lines) so frontends always get *something*
        structured. The raw stdout is still exposed for debugging.

    The shape returned is always:
        {
          "exit_code": int,
          "parsed": Any | null,    # structured representation
          "stdout": str,           # raw text (ANSI-stripped for readability)
          "stderr": str,
        }
    """
    try:
        parsed: Any = parser(result.stdout) if parser else parse_lines(result.stdout)
    except Exception:  # pragma: no cover — parsers must not raise, but guard
        parsed = None
    return {
        "exit_code": result.exit_code,
        "parsed": parsed,
        "stdout": strip_ansi(result.stdout),
        "stderr": strip_ansi(result.stderr),
    }
