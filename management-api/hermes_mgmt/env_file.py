from __future__ import annotations

import fcntl
import logging
import os
import re
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_VALID_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_SENSITIVE_KEY_RE = re.compile(r"(?i)(_KEY|_TOKEN|_SECRET|_PASSWORD|_HASH)$")
_QUOTED_VALUE_RE = re.compile(r'^"(.*)"$|^\'(.*)\'$', re.DOTALL)


def _validate_key(key: str) -> None:
    if not _VALID_KEY_RE.match(key):
        raise ValueError(f"Invalid env key: {key!r}. Must match ^[A-Z_][A-Z0-9_]*$")


def _parse_value(raw: str) -> str:
    """Strip surrounding quotes from a value if present."""
    m = _QUOTED_VALUE_RE.match(raw)
    if m:
        return m.group(1) if m.group(1) is not None else m.group(2)
    return raw


def read_env(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines from an env file. Ignores comments and blank lines."""
    result: dict[str, str] = {}
    if not path.exists():
        return result
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            key, _, raw_value = stripped.partition("=")
            key = key.strip()
            if not key:
                continue
            result[key] = _parse_value(raw_value.strip())
    return result


def set_env(path: Path, key: str, value: str) -> None:
    """Atomically set a key in the env file. Preserves comments and ordering."""
    _validate_key(key)
    path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_SH)
            lines = fh.readlines()
            fcntl.flock(fh, fcntl.LOCK_UN)

    new_line = f"{key}={value}\n"
    found = False
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or not stripped or "=" not in stripped:
            new_lines.append(line)
            continue
        existing_key = stripped.partition("=")[0].strip()
        if existing_key == key:
            new_lines.append(new_line)
            found = True
        else:
            new_lines.append(line)

    if not found:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines.append("\n")
        new_lines.append(new_line)

    _atomic_write(path, new_lines)


def delete_env(path: Path, key: str) -> bool:
    """Remove a key from the env file. Returns True if found and removed."""
    _validate_key(key)
    if not path.exists():
        return False

    lines: list[str] = []
    with path.open("r", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_SH)
        lines = fh.readlines()
        fcntl.flock(fh, fcntl.LOCK_UN)

    new_lines: list[str] = []
    found = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or not stripped or "=" not in stripped:
            new_lines.append(line)
            continue
        existing_key = stripped.partition("=")[0].strip()
        if existing_key == key:
            found = True
        else:
            new_lines.append(line)

    if found:
        _atomic_write(path, new_lines)
    return found


def mask_value(key: str, value: str) -> str:
    """Mask sensitive values; leave last 4 chars visible."""
    if _SENSITIVE_KEY_RE.search(key):
        if len(value) <= 4:
            return "****"
        return f"sk-****{value[-4:]}"
    return value


def list_env(path: Path, mask: bool = True) -> dict[str, str]:
    """Return env dict with sensitive values optionally masked."""
    raw = read_env(path)
    if not mask:
        return raw
    return {k: mask_value(k, v) for k, v in raw.items()}


def _atomic_write(path: Path, lines: list[str]) -> None:
    """Write lines to a temp file then rename for atomicity."""
    dir_ = path.parent
    fd, tmp_path = tempfile.mkstemp(dir=dir_, prefix=".env_tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            fh.writelines(lines)
            fh.flush()
            os.fsync(fh.fileno())
            fcntl.flock(fh, fcntl.LOCK_UN)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
