from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

logger = logging.getLogger(__name__)


async def systemctl(*args: str) -> tuple[int, str, str]:
    """Run systemctl with given args. Returns (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "systemctl",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    return proc.returncode or 0, stdout_b.decode(errors="replace"), stderr_b.decode(errors="replace")


def _check_allowlist(service: str, allowed: tuple[str, ...] | None) -> None:
    if allowed is not None and service not in allowed:
        raise ValueError(f"Service '{service}' is not in the allowed list: {allowed}")


async def is_active(service: str, allowed: tuple[str, ...] | None = None) -> bool:
    _check_allowlist(service, allowed)
    code, stdout, _ = await systemctl("is-active", "--quiet", service)
    return code == 0


async def sub_state(service: str, allowed: tuple[str, ...] | None = None) -> str:
    _check_allowlist(service, allowed)
    _, stdout, _ = await systemctl(
        "show", "-p", "SubState", "--value", service
    )
    return stdout.strip() or "unknown"


async def active_since(service: str, allowed: tuple[str, ...] | None = None) -> str:
    _check_allowlist(service, allowed)
    _, stdout, _ = await systemctl(
        "show", "-p", "ActiveEnterTimestamp", "--value", service
    )
    value = stdout.strip()
    return value if value and value != "n/a" else "unknown"


async def restart(service: str, allowed: tuple[str, ...] | None = None) -> tuple[int, str]:
    _check_allowlist(service, allowed)
    code, stdout, stderr = await systemctl("restart", service)
    return code, stderr or stdout


async def stop(service: str, allowed: tuple[str, ...] | None = None) -> tuple[int, str]:
    _check_allowlist(service, allowed)
    code, stdout, stderr = await systemctl("stop", service)
    return code, stderr or stdout


async def start(service: str, allowed: tuple[str, ...] | None = None) -> tuple[int, str]:
    _check_allowlist(service, allowed)
    code, stdout, stderr = await systemctl("start", service)
    return code, stderr or stdout


async def journal_tail(
    service: str, lines: int = 100, allowed: tuple[str, ...] | None = None
) -> str:
    _check_allowlist(service, allowed)
    proc = await asyncio.create_subprocess_exec(
        "journalctl",
        "-u", service,
        "-n", str(lines),
        "--no-pager",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, _ = await proc.communicate()
    return stdout_b.decode(errors="replace")


async def journal_follow(
    service: str, allowed: tuple[str, ...] | None = None
) -> AsyncIterator[str]:
    _check_allowlist(service, allowed)
    proc = await asyncio.create_subprocess_exec(
        "journalctl",
        "-u", service,
        "-f",
        "-o", "cat",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    assert proc.stdout is not None
    try:
        while True:
            line_b = await proc.stdout.readline()
            if not line_b:
                break
            yield line_b.decode(errors="replace").rstrip("\n")
    finally:
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
