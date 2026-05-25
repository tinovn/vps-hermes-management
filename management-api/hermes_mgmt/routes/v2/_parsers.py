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
# API-key pseudo-row. Two forms exist in the wild:
#   config show:     "  OpenRouter     (not set)"
#   status --deep:   "  OpenRouter    ✗ (not set)"   (extra ✓/✗/⚠ marker)
# Capture optional marker as group 2 so the name is clean.
_API_KEY_ROW_RE = re.compile(
    r"^\s+(.+?)\s+([✓✗⚠])?\s*(\([^)]+\))\s*$"
)


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
                name = m.group(1).strip()
                marker = m.group(2) or ""
                status = m.group(3).strip()
                sections[current][name] = (
                    f"{marker} {status}".strip() if marker else status
                )
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


# ---------------------------------------------------------------------------
# Bespoke parsers (sample-driven). Each handles one CLI command's output.
# Real samples live in tests/fixtures/hermes_cli_samples.txt.
# ---------------------------------------------------------------------------


# `auth status <provider>` -> "anthropic: logged out"
_AUTH_STATUS_RE = re.compile(r"^([a-z0-9_-]+):\s*(.+?)\s*$")


def parse_auth_status(stdout: str) -> dict[str, Any]:
    """Parse `hermes auth status <provider>` into {provider, status, logged_in}."""
    text = strip_ansi(stdout).strip()
    for line in text.splitlines():
        m = _AUTH_STATUS_RE.match(line.strip())
        if m:
            status = m.group(2).strip()
            return {
                "provider": m.group(1),
                "status": status,
                "logged_in": "logged in" in status.lower(),
            }
    return {"provider": None, "status": text or None, "logged_in": False}


def parse_auth_list(stdout: str) -> dict[str, Any]:
    """`hermes auth list` — empty when no pools, else list of pool entries."""
    text = strip_decorations(stdout).strip()
    if not text:
        return {"pools": [], "empty": True}
    # Best-effort: each non-empty, non-bullet line is a pool entry. Bespoke
    # parsing requires a sample with at least one pool — fall back to lines.
    return {"pools": parse_lines(stdout), "empty": False}


def _empty_marker_message(text: str, markers: list[str]) -> bool:
    lower = text.lower()
    return any(m.lower() in lower for m in markers)


def parse_fallback_list(stdout: str) -> dict[str, Any]:
    """`hermes fallback list` — text message when empty, else provider chain."""
    text = strip_decorations(stdout).strip()
    if _empty_marker_message(text, ["no fallback providers"]):
        return {"providers": [], "empty": True}
    # Parse lines like "1. <provider> (<model>)" or " - <provider>"
    providers: list[dict[str, str]] = []
    for line in text.splitlines():
        s = line.strip().lstrip("-•").strip()
        m = re.match(r"^(?:\d+\.\s+)?([a-z0-9_-]+)(?:\s*[\(/]\s*(.+?)\s*\)?)?$", s)
        if m and not s.lower().startswith(("add ", "remove ", "clear", "no ", "hermes ")):
            providers.append({"provider": m.group(1), "model": m.group(2) or ""})
    return {"providers": providers, "empty": not providers}


def parse_sessions_list(stdout: str) -> dict[str, Any]:
    """`hermes sessions list` — empty marker or list of sessions."""
    text = strip_decorations(stdout).strip()
    if _empty_marker_message(text, ["no sessions found"]):
        return {"sessions": [], "empty": True}
    return {"sessions": parse_table(stdout), "empty": False}


def parse_sessions_stats(stdout: str) -> dict[str, Any]:
    """`hermes sessions stats` — kv pairs."""
    out: dict[str, Any] = {}
    for line in strip_ansi(stdout).splitlines():
        m = re.match(r"^([A-Za-z][A-Za-z _]+):\s+(.+?)\s*$", line.strip())
        if not m:
            continue
        key = m.group(1).strip().lower().replace(" ", "_")
        val = m.group(2).strip()
        # Numbers
        if val.isdigit():
            out[key] = int(val)
        else:
            # "0.1 MB" → keep as string
            out[key] = val
    return out


def parse_memory_status(stdout: str) -> dict[str, Any]:
    """`hermes memory status` — Built-in / Provider lines + bulleted plugins."""
    text = strip_decorations(stdout)
    result: dict[str, Any] = {"built_in": None, "provider": None, "plugins": []}
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("•"):
            # "• name  (description)"
            m = re.match(r"^•\s+([A-Za-z0-9_-]+)(?:\s+\((.+?)\))?\s*$", s)
            if m:
                result["plugins"].append(
                    {"name": m.group(1), "description": m.group(2) or ""}
                )
            continue
        m = re.match(r"^Built-in:\s+(.+?)\s*$", s)
        if m:
            result["built_in"] = m.group(1)
            continue
        m = re.match(r"^Provider:\s+(.+?)\s*$", s)
        if m:
            result["provider"] = m.group(1)
            continue
    return result


# `skills list` — Rich table with ┃/│ column separators.
def parse_rich_table(stdout: str) -> dict[str, Any]:
    """Parse a Rich-rendered table (┃/│ separators, ━ borders).

    Returns ``{"rows": [...], "footer": "..."}`` — footer is the line(s)
    after the table close that summarise counts.
    """
    text = strip_ansi(stdout)
    lines = text.splitlines()
    rows: list[dict[str, str]] = []
    header: list[str] | None = None
    table_done = False
    footer_lines: list[str] = []

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            continue
        # Header row uses ┃ as column separator
        if "┃" in line and header is None:
            cells = [c.strip() for c in line.split("┃") if c.strip()]
            if cells:
                header = cells
            continue
        # Data rows use │ (light vertical). Keep empty cells (e.g. a skill
        # with no Category) — only the outermost empty edges from the leading
        # and trailing │ are stripped.
        if "│" in line and header is not None and not table_done:
            parts = line.split("│")
            # Drop the leading-empty (before first │) and trailing-empty
            # (after last │) from line edges.
            if parts and not parts[0].strip():
                parts = parts[1:]
            if parts and not parts[-1].strip():
                parts = parts[:-1]
            cells = [c.strip() for c in parts]
            if len(cells) >= len(header):
                cells = cells[: len(header)]
            else:
                cells = cells + [""] * (len(header) - len(cells))
            rows.append(dict(zip(header, cells)))
            continue
        # Border closing the table
        if line.strip().startswith(("└", "┴")):
            table_done = True
            continue
        # Anything after table close is footer
        if table_done and line.strip() and not all(c in _BOX_CHARS + " " for c in line.strip()):
            footer_lines.append(line.strip())

    return {
        "rows": rows,
        "footer": " ".join(footer_lines).strip() or None,
    }


def parse_skills_list(stdout: str) -> dict[str, Any]:
    """`hermes skills list` — Rich table + summary footer.

    Footer like "0 hub-installed, 85 builtin, 0 local — 85 enabled, 0 disabled"
    is also extracted into structured counts.
    """
    tbl = parse_rich_table(stdout)
    summary: dict[str, int] = {}
    footer = tbl.get("footer") or ""
    # Parse footer: "0 hub-installed, 85 builtin, 0 local — 85 enabled, 0 disabled"
    for m in re.finditer(r"(\d+)\s+([A-Za-z][A-Za-z0-9_-]*)", footer):
        summary[m.group(2).replace("-", "_")] = int(m.group(1))
    return {
        "skills": tbl.get("rows", []),
        "summary": summary,
        "footer": footer or None,
    }


def parse_bundles_list(stdout: str) -> dict[str, Any]:
    text = strip_decorations(stdout).strip()
    if _empty_marker_message(text, ["no bundles installed"]):
        # Extract bundles directory if present
        m = re.search(r"Bundles directory:\s*(.+?)\s*$", text, re.MULTILINE)
        return {
            "bundles": [],
            "empty": True,
            "bundles_dir": m.group(1).strip() if m else None,
        }
    return {"bundles": parse_table(stdout), "empty": False}


def parse_tools_summary(stdout: str) -> dict[str, Any]:
    """`hermes tools --summary` — grouped by platform header like "🖥️ CLI (18/26)".

    Returns ``{"platforms": [{"name": "CLI", "enabled": 18, "total": 26, "tools": [...]}, ...]}``.
    """
    text = strip_ansi(stdout)
    platforms: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    # Header line: optional emoji + name + "(<enabled>/<total>)"
    header_re = re.compile(r"^\s{2,4}\S+\s+(.+?)\s+\((\d+)/(\d+)\)\s*$")
    item_re = re.compile(r"^\s{4,}([✓✗⚠○])\s+(.+?)\s*$")

    for line in text.splitlines():
        if not line.strip():
            continue
        m = header_re.match(line)
        if m and "(" in line and ")" in line:
            current = {
                "name": m.group(1).strip(),
                "enabled": int(m.group(2)),
                "total": int(m.group(3)),
                "tools": [],
            }
            platforms.append(current)
            continue
        m = item_re.match(line)
        if m and current is not None:
            current["tools"].append(
                {"status": m.group(1), "name": m.group(2).strip()}
            )

    return {"platforms": platforms}


def parse_webhook_list(stdout: str) -> dict[str, Any]:
    text = strip_decorations(stdout).strip()
    if _empty_marker_message(text, ["webhook platform is not enabled", "not enabled"]):
        return {"webhooks": [], "enabled": False}
    return {"webhooks": parse_table(stdout), "enabled": True}


def parse_gateway_status(stdout: str) -> dict[str, Any]:
    """Parse `hermes gateway status` (systemctl-style output)."""
    text = strip_ansi(stdout)
    out: dict[str, Any] = {
        "service": None,
        "loaded": None,
        "active": None,
        "active_state": None,  # "active" | "inactive" | "failed"
        "since": None,
        "main_pid": None,
        "tasks": None,
        "memory": None,
        "cpu": None,
        "running": False,
    }
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"^●\s+(\S+)", s)
        if m:
            out["service"] = m.group(1)
            continue
        if s.startswith("Loaded:"):
            out["loaded"] = s.split(":", 1)[1].strip()
            continue
        if s.startswith("Active:"):
            v = s.split(":", 1)[1].strip()
            out["active"] = v
            m = re.match(r"^(\w+)\s*(?:\(([^)]+)\))?(?:\s+since\s+(.+))?$", v)
            if m:
                out["active_state"] = m.group(1)
                out["since"] = m.group(3) or None
            out["running"] = v.startswith("active")
            continue
        if s.startswith("Main PID:"):
            m = re.match(r"^Main PID:\s+(\d+)", s)
            if m:
                out["main_pid"] = int(m.group(1))
            continue
        if s.startswith("Tasks:"):
            out["tasks"] = s.split(":", 1)[1].strip()
            continue
        if s.startswith("Memory:"):
            out["memory"] = s.split(":", 1)[1].strip()
            continue
        if s.startswith("CPU:"):
            out["cpu"] = s.split(":", 1)[1].strip()
            continue
    return out


def parse_gateway_list(stdout: str) -> dict[str, Any]:
    """`hermes gateway list` — `✓/✗ <name> (current?) — <status>`."""
    text = strip_ansi(stdout)
    gateways: list[dict[str, Any]] = []
    for line in text.splitlines():
        s = line.strip()
        m = re.match(
            r"^([✓✗])\s+(\S+)(?:\s+\((current)\))?\s+—\s+(.+?)\s*$", s
        )
        if m:
            gateways.append(
                {
                    "name": m.group(2),
                    "current": m.group(3) == "current",
                    "status": m.group(4),
                    "running": m.group(1) == "✓",
                }
            )
    return {"gateways": gateways}


def parse_cron_list(stdout: str) -> dict[str, Any]:
    text = strip_decorations(stdout).strip()
    if _empty_marker_message(text, ["no scheduled jobs"]):
        return {"jobs": [], "empty": True}
    return {"jobs": parse_table(stdout), "empty": False}


def parse_kanban_list(stdout: str) -> dict[str, Any]:
    text = strip_decorations(stdout).strip()
    if _empty_marker_message(text, ["no matching tasks", "no tasks"]):
        return {"tasks": [], "empty": True}
    return {"tasks": parse_table(stdout), "empty": False}


def parse_curator_status(stdout: str) -> dict[str, Any]:
    """`hermes curator status` — first line `curator: ENABLED` + kv pairs."""
    text = strip_ansi(stdout)
    out: dict[str, Any] = {"enabled": None}
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        m = re.match(r"^curator:\s+(\S+)", s, re.IGNORECASE)
        if m:
            out["enabled"] = m.group(1).upper() == "ENABLED"
            continue
        m = re.match(r"^([A-Za-z][A-Za-z _]+):\s+(.+?)\s*$", s)
        if m:
            key = m.group(1).strip().lower().replace(" ", "_")
            val = m.group(2).strip()
            out[key] = int(val) if val.isdigit() else val
    return out


def parse_status_deep(stdout: str) -> dict[str, Any]:
    """`hermes status --deep` — same `◆ Section` pattern as `config show`.

    Values may start with ✓/✗/⚠ markers; we keep them verbatim so the FE
    can decide how to render (badge / icon).
    """
    return parse_config_show(stdout)


# `hermes doctor` summary footer: "Found 3 issue(s) to address:" then numbered list.
_DOCTOR_FOOTER_RE = re.compile(r"Found\s+(\d+)\s+issue\(s\)", re.IGNORECASE)
_DOCTOR_ITEM_RE = re.compile(r"^\s*(\d+)\.\s+(.+?)\s*$")


# Doctor item: "  ✓ Python 3.11.15" or "  ✗ model.provider ... " or "  ⚠ Foo"
_DOCTOR_ITEM_BULLET_RE = re.compile(r"^\s+([✓✗⚠])\s+(.+?)\s*$")


def parse_doctor(stdout: str) -> dict[str, Any]:
    """`hermes doctor` — sections (◆ Title) of ✓/✗/⚠ items + footer issues.

    Returns:
        {
          "sections": {
            "Python Environment": [
              {"status": "✓", "message": "Python 3.11.15"},
              ...
            ],
            ...
          },
          "issues": [{"num": 1, "message": "..."}, ...],
          "issue_count": N,
          "healthy": bool,    # no ✗ items anywhere AND no numbered issues
        }
    """
    text = strip_decorations(stdout)
    sections: dict[str, list[dict[str, str]]] = {}
    current: str | None = None
    issues: list[dict[str, Any]] = []
    seen_footer = False

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if _DOCTOR_FOOTER_RE.search(line):
            seen_footer = True
            current = None
            continue
        if seen_footer:
            m = _DOCTOR_ITEM_RE.match(line)
            if m:
                issues.append({"num": int(m.group(1)), "message": m.group(2)})
            continue
        # Section header at left margin
        m = _SECTION_RE.match(line.strip())
        if m and not line.startswith(" "):
            current = m.group(1).strip()
            sections[current] = []
            continue
        if current is None:
            continue
        m = _DOCTOR_ITEM_BULLET_RE.match(line)
        if m:
            sections[current].append(
                {"status": m.group(1), "message": m.group(2)}
            )

    any_failed = any(
        item["status"] == "✗"
        for items in sections.values()
        for item in items
    )
    return {
        "sections": sections,
        "issues": issues,
        "issue_count": len(issues),
        "healthy": not issues and not any_failed,
    }


def parse_dump(stdout: str) -> dict[str, Any]:
    """`hermes dump` — kv pairs at top, then `api_keys:` and `features:` blocks."""
    text = strip_ansi(stdout)
    top: dict[str, Any] = {}
    api_keys: dict[str, str] = {}
    features: dict[str, Any] = {}
    section: str | None = None

    for raw in text.splitlines():
        line = raw.rstrip()
        s = line.strip()
        if not s or s.startswith("---"):
            continue
        # Section headers ("api_keys:", "features:") have no value after colon
        m = re.match(r"^([a-z_]+):\s*$", s)
        if m:
            section = m.group(1)
            continue
        # Indented `  key   value` rows under a section
        if line.startswith(" ") and section is not None:
            m = re.match(r"^\s+([A-Za-z0-9_./-]+)\s{2,}(.+?)\s*$", line)
            if m:
                bucket = api_keys if section == "api_keys" else features
                bucket[m.group(1)] = m.group(2).strip()
                continue
        # Top-level kv ("version: ...")
        m = re.match(r"^([a-z_]+):\s+(.+?)\s*$", s)
        if m:
            top[m.group(1)] = m.group(2).strip()
            section = None

    out = dict(top)
    if api_keys:
        out["api_keys"] = api_keys
    if features:
        out["features"] = features
    return out


def parse_insights(stdout: str) -> dict[str, Any]:
    text = strip_decorations(stdout).strip()
    if _empty_marker_message(text, ["no sessions found"]):
        return {"empty": True, "summary": text}
    # Otherwise expect structured output — fall back to lines for now
    return {"empty": False, "lines": parse_lines(stdout)}


def parse_checkpoints_status(stdout: str) -> dict[str, Any]:
    text = strip_ansi(stdout)
    out: dict[str, Any] = {}
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"^([A-Za-z][A-Za-z _-]+):\s+(.+?)\s*$", s)
        if m:
            out[m.group(1).strip().lower().replace(" ", "_")] = m.group(2).strip()
            continue
        m = re.match(r"^([A-Za-z][A-Za-z _-]+)\s+(\d+(?:\.\d+)?)\s+(\S+)\s*$", s)
        if m:
            out.setdefault("breakdown", {})[m.group(1).strip()] = (
                f"{m.group(2)} {m.group(3)}"
            )
    return out


def parse_profile(stdout: str) -> dict[str, Any]:
    """`hermes profile` (no subcmd) — current profile info."""
    text = strip_ansi(stdout)
    out: dict[str, Any] = {}
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"^([A-Za-z][A-Za-z ]+):\s+(.+?)\s*$", s)
        if m:
            key = m.group(1).strip().lower().replace(" ", "_")
            val = m.group(2).strip()
            # "deepseek-chat (deepseek)" → split model/provider
            if key == "model":
                mm = re.match(r"^(.+?)\s+\(([^)]+)\)\s*$", val)
                if mm:
                    out["model"] = mm.group(1).strip()
                    out["provider"] = mm.group(2).strip()
                    continue
            out[key] = val
    return out


def parse_version(stdout: str) -> dict[str, Any]:
    """`hermes version` — header line + key info + optional update notice."""
    text = strip_ansi(stdout)
    out: dict[str, Any] = {"update_available": False, "update_message": None}
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        m = re.match(r"^Hermes Agent\s+v?([^\s(]+)(?:\s+\(([^)]+)\))?\s*$", s)
        if m:
            out["version"] = m.group(1)
            if m.group(2):
                out["release_date"] = m.group(2)
            continue
        m = re.match(r"^Project:\s+(.+?)\s*$", s)
        if m:
            out["project"] = m.group(1)
            continue
        m = re.match(r"^Python:\s+(.+?)\s*$", s)
        if m:
            out["python"] = m.group(1)
            continue
        m = re.match(r"^OpenAI SDK:\s+(.+?)\s*$", s)
        if m:
            out["openai_sdk"] = m.group(1)
            continue
        if "Update available" in s or "commits behind" in s.lower():
            out["update_available"] = True
            out["update_message"] = s
            continue
        if s.lower() == "up to date":
            out["update_available"] = False
            out["update_message"] = s
    return out


# `config check` — different from config show; uses ○/✓ markers and arrows
_CHECK_ITEM_RE = re.compile(
    r"^\s*[○•]\s*([A-Z][A-Z0-9_]+)(?:\s*→\s*(.+?))?\s*$"
)


def parse_config_status(stdout: str) -> dict[str, Any]:
    """`hermes config check` — Configuration Status with Required / Optional lists.

    Returns ``{"version": "23", "required": [...], "optional": [...], "version_ok": bool}``.
    Each item is ``{"key": "KEY_NAME", "uses": ["tool1", "tool2"]}``.
    """
    text = strip_ansi(stdout)
    out: dict[str, Any] = {
        "version": None,
        "version_ok": True,
        "required": [],
        "optional": [],
    }
    current: str | None = None
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        m = re.match(r"^Config version:\s+(\S+)\s*(✓|✗)?", s)
        if m:
            out["version"] = m.group(1)
            out["version_ok"] = (m.group(2) or "✓") == "✓"
            continue
        if s.startswith("Required:"):
            current = "required"
            continue
        if s.startswith("Optional:"):
            current = "optional"
            continue
        m = _CHECK_ITEM_RE.match(line)
        if m and current is not None:
            uses_raw = (m.group(2) or "").strip()
            uses = (
                [u.strip() for u in uses_raw.split(",") if u.strip()]
                if uses_raw
                else []
            )
            out[current].append({"key": m.group(1), "uses": uses})
    return out
