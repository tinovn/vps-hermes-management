from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import AsyncIterator

import yaml

logger = logging.getLogger(__name__)

# Hermes writes its log files under HERMES_HOME/logs/ (e.g. agent.log,
# errors.log, gateway.log, gateway-exit-diag.log, gateway-restart.log).
_LOGS_SUBDIR = "logs"
# Log name validation: letters, digits, dash, underscore, dot. Disallows `/`
# and `..` so a caller can't escape the logs dir via path traversal.
_VALID_LOG_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_POLL_INTERVAL = 0.5  # seconds between polls for follow_log_file


def read_config_yaml(hermes_home: Path) -> dict:
    """Read config.yaml from HERMES_HOME. Returns empty dict if missing or unreadable."""
    config_path = hermes_home / "config.yaml"
    if not config_path.exists():
        return {}
    try:
        with config_path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
            return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("Could not read %s: %s", config_path, exc)
        return {}


def tail_log_file(hermes_home: Path, log_name: str, lines: int = 100) -> str:
    """Return the last N lines of <log_name>.log from HERMES_HOME/logs/."""
    if not _VALID_LOG_NAME_RE.match(log_name) or ".." in log_name:
        raise ValueError(
            f"Log name '{log_name}' not allowed. Must match [A-Za-z0-9._-]+"
        )
    logs_dir = hermes_home / _LOGS_SUBDIR
    log_path = logs_dir / f"{log_name}.log"
    # Defence in depth: ensure the resolved path is inside logs_dir.
    try:
        log_path.resolve().relative_to(logs_dir.resolve())
    except (ValueError, OSError) as exc:
        raise ValueError(f"Log name '{log_name}' resolves outside logs dir: {exc}")
    if not log_path.exists():
        return ""
    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as fh:
            all_lines = fh.readlines()
        return "".join(all_lines[-lines:])
    except Exception as exc:
        logger.warning("Could not read log %s: %s", log_path, exc)
        return f"Error reading log: {exc}"


def list_log_files(hermes_home: Path) -> list[dict]:
    """List all .log files in HERMES_HOME/logs/ with name, size, and mtime."""
    result: list[dict] = []
    logs_dir = hermes_home / _LOGS_SUBDIR
    if not logs_dir.exists():
        return result
    try:
        for entry in logs_dir.iterdir():
            if entry.suffix == ".log" and entry.is_file():
                stat = entry.stat()
                result.append(
                    {
                        "name": entry.name,
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                    }
                )
    except Exception as exc:
        logger.warning("Could not list log files in %s: %s", logs_dir, exc)
    result.sort(key=lambda x: x["name"])
    return result


async def follow_log_file(path: Path) -> AsyncIterator[str]:
    """Async generator that tails a log file and yields new lines as they appear."""
    # Seek to end on first open
    offset = 0
    if path.exists():
        offset = path.stat().st_size

    while True:
        await asyncio.sleep(_POLL_INTERVAL)
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                fh.seek(offset)
                chunk = fh.read()
                if chunk:
                    offset = fh.tell()
                    for line in chunk.splitlines():
                        yield line
        except Exception as exc:
            logger.warning("Error following log file %s: %s", path, exc)
