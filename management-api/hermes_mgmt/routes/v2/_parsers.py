"""Output parsers for `hermes <subcommand>` stdout.

Hermes CLI prints decorated text (Unicode box-drawing, ANSI colors, kawaii
emoji). Frontend should not have to deal with that — we parse here and
return structured JSON. Raw stdout is still kept in the response under
`raw` for debugging.

Parser contract: each parser takes a string and returns a JSON-serializable
dict / list. Parsers MUST NOT raise on unexpected input — degrade
gracefully by returning what they could extract (or an empty container).
"""
from __future__ import annotations

import ast
import re
from typing import Any

# Strip ANSI escape sequences (CSI, OSC, simple ESC[...m)
_ANSI_RE = re.compile(r"\x1b(?:\[[0-9;?]*[A-Za-z]|\][^\x07\x1b]*(?:\x07|\x1b\\))")

# Box-drawing + decorative glyphs Hermes uses
_BOX_CHARS = "┌─┐│└┘├┤┬┴┼━┃┏┓┗┛"


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences."""
    return _ANSI_RE.sub("", text)


def strip_decorations(text: str) -> str:
    """Strip ANSI + box-drawing lines + leading checkmarks.

    Result is plain text suitable for further structural parsing.
    """
    out_lines: list[str] = []
    for line in strip_ansi(text).splitlines():
        stripped = line.strip()
        # Drop pure box-drawing lines (top/bottom borders)
        if stripped and all(c in _BOX_CHARS + " " for c in stripped):
            continue
        # Drop "│ ... │" framing — keep content
        if stripped.startswith("│") and stripped.endswith("│"):
            inner = stripped[1:-1].strip()
            if inner:
                out_lines.append(inner)
            continue
        out_lines.append(line)
    return "\n".join(out_lines)


def parse_single_line(stdout: str) -> str:
    """For commands like `config path` that print one line."""
    return strip_ansi(stdout).strip()


# Pattern: "✓ Set <key> = <value> in <file>" or "✓ Set <KEY> in <file>"
_SET_OK_RE = re.compile(
    r"^[✓✔✓]\s*Set\s+([A-Za-z0-9_.]+)(?:\s*=\s*(.+?))?\s+in\s+(.+)$"
)


def parse_config_set(stdout: str) -> dict[str, Any]:
    """Parse `hermes config set <key> <value>` confirmation line."""
    text = strip_ansi(stdout).strip()
    for line in text.splitlines():
        m = _SET_OK_RE.match(line.strip())
        if m:
            return {
                "key": m.group(1),
                "value": m.group(2),
                "file": m.group(3).strip(),
            }
    # Fallback: couldn't parse, surface raw line
    return {"key": None, "value": None, "file": None, "_unparsed": text}


# Section header: "◆ Section Name"
_SECTION_RE = re.compile(r"^[◆◇◆◇▶■]+\s*(.+?)\s*$")
# Inner row: "  Key Name:   value"  (2+ spaces of indent, then "Key: value")
_KV_RE = re.compile(r"^\s+([A-Za-z][A-Za-z0-9 _/().+-]*?)\s*:\s*(.*?)\s*$")
# API-key pseudo-row: status is the FINAL parenthesized group on the line,
# everything before (after the indent) is the name. Greedy `.+` + a single
# whitespace lets us anchor on the last `(...)`, so names like
# "OpenAI (STT/TTS)" are kept intact even when only one space separates
# them from the trailing `(not set)`.
_API_KEY_ROW_RE = re.compile(r"^\s+(.+)\s+(\([^)]+\))\s*$")


def _try_python_literal(value: str) -> Any:
    """If value looks like a Python repr (dict/list/number/bool), eval it.

    Hermes prints e.g.  Model: {'default': 'deepseek-chat', 'provider': 'deepseek'}
    — recover the dict so the frontend doesn't have to parse it.
    """
    s = value.strip()
    if not s:
        return ""
    first = s[0]
    if first in "{[" or s in ("True", "False", "None"):
        try:
            return ast.literal_eval(s)
        except (ValueError, SyntaxError):
            return s
    # Bare integer
    if s.lstrip("-").isdigit():
        try:
            return int(s)
        except ValueError:
            return s
    return s


def parse_config_show(stdout: str) -> dict[str, Any]:
    """Parse `hermes config show` into a section -> {key: value} dict.

    Special-cases ``◆ API Keys`` — those rows use 2+ spaces between
    provider name and status (`(not set)` / `(set, …last4)`), not a colon.
    """
    text = strip_decorations(stdout)
    sections: dict[str, dict[str, Any]] = {}
    current: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        # Hint footer commands ("hermes config edit ...") start at column 0
        # without a section header — stop parsing once we hit them.
        if not line.startswith(" ") and not _SECTION_RE.match(line):
            # Plain line at left margin = footer or stray help text
            if current is None:
                continue
            # Once we left a section without a new one, drop the rest
            current = None
            continue

        m_section = _SECTION_RE.match(line.strip())
        if m_section and not line.startswith(" "):
            current = m_section.group(1).strip()
            sections[current] = {}
            continue

        if current is None:
            continue

        # API Keys section uses "<name>   <status>" with multi-space gap
        if current.lower().startswith("api key"):
            m = _API_KEY_ROW_RE.match(line)
            if m:
                sections[current][m.group(1).strip()] = m.group(2).strip()
                continue

        m_kv = _KV_RE.match(line)
        if m_kv:
            key = m_kv.group(1).strip()
            sections[current][key] = _try_python_literal(m_kv.group(2))

    return sections


def parse_check(stdout: str) -> dict[str, Any]:
    """Parse `hermes config check` — surface ok flag + lines mentioning issues."""
    text = strip_ansi(stdout)
    issues: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        # Heuristic: lines with ✗ ⚠ "missing" / "stale" / "outdated" are issues
        if (
            "✗" in s
            or "⚠" in s
            or "missing" in s.lower()
            or "stale" in s.lower()
            or "outdated" in s.lower()
        ):
            issues.append(s)
    return {"clean": not issues, "issues": issues}


# Generic table parser: heuristically split rows into columns by ≥2 spaces.
_TABLE_SEP_RE = re.compile(r"\s{2,}")


def parse_table(stdout: str) -> list[dict[str, str]]:
    """Best-effort table parser.

    Assumes first non-blank line that has ≥2 columns is the header row.
    Subsequent rows aligned to the same column positions become records.
    Returns list of {column_name: cell_value}. Empty list on no match.
    """
    text = strip_decorations(stdout)
    lines = [
        ln for ln in text.splitlines()
        if ln.strip() and not ln.lstrip().startswith(("#", "-", "─", "=", "·"))
    ]
    header: list[str] | None = None
    rows: list[dict[str, str]] = []
    for line in lines:
        cells = [c.strip() for c in _TABLE_SEP_RE.split(line.strip()) if c.strip()]
        if not cells or len(cells) < 2:
            continue
        if header is None:
            header = cells
            continue
        # Pad / truncate to header length
        if len(cells) < len(header):
            cells = cells + [""] * (len(header) - len(cells))
        elif len(cells) > len(header):
            # Merge trailing cells into last column
            cells = cells[: len(header) - 1] + [" ".join(cells[len(header) - 1 :])]
        rows.append(dict(zip(header, cells)))
    return rows


def parse_lines(stdout: str) -> list[str]:
    """Plain non-empty lines (ANSI stripped). Useful when nothing else fits."""
    return [ln for ln in strip_decorations(stdout).splitlines() if ln.strip()]
