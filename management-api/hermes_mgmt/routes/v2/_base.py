"""Shared helpers for v2 routes."""
from __future__ import annotations

from fastapi import HTTPException, status

from hermes_mgmt.cli_runner import run_hermes
from hermes_mgmt.config import Settings
from hermes_mgmt.models import CliResponse


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


def cli_payload(result: CliResponse) -> dict:
    """Standard data payload for endpoints that just expose CLI output.

    Frontend can render `stdout` directly or use `exit_code` to branch.
    """
    return {
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
