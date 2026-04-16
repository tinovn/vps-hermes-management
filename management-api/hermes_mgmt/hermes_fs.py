from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import AsyncIterator

import yaml

logger = logging.getLogger(__name__)

_ALLOWED_LOG_NAMES = frozenset({"agent", "errors", "gateway"})
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
    """Return the last N lines of <log_name>.log from HERMES_HOME."""
    if log_name not in _ALLOWED_LOG_NAMES:
        raise ValueError(
            f"Log name '{log_name}' not allowed. Valid: {sorted(_ALLOWED_LOG_NAMES)}"
        )
    log_path = hermes_home / f"{log_name}.log"
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
    """List all .log files in HERMES_HOME with name, size, and mtime."""
    result: list[dict] = []
    if not hermes_home.exists():
        return result
    try:
        for entry in hermes_home.iterdir():
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
        logger.warning("Could not list log files in %s: %s", hermes_home, exc)
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
