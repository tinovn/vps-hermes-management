"""Parser tests driven by real `hermes <cmd>` stdout samples.

Samples live in ``tests/fixtures/hermes_cli_samples.txt`` and were captured
from a live VPS. Each test asserts the shape and at least one concrete
value, so regressions in the parser (e.g. a regex tweak that drops a row)
fail loudly.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from hermes_mgmt.routes.v2 import _parsers as p

_FIXTURE = Path(__file__).parent / "fixtures" / "hermes_cli_samples.txt"


def _load_samples() -> dict[str, str]:
    text = _FIXTURE.read_text(encoding="utf-8")
    chunks: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^=== (.+?) ===$", line)
        if m:
            if current is not None:
                chunks[current] = "\n".join(buf).strip("\n")
            current = m.group(1)
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        chunks[current] = "\n".join(buf)
    return chunks


@pytest.fixture(scope="module")
def samples() -> dict[str, str]:
    return _load_samples()


def test_auth_status(samples: dict[str, str]) -> None:
    out = p.parse_auth_status(samples["auth status anthropic"])
    assert out == {"provider": "anthropic", "status": "logged out", "logged_in": False}


def test_fallback_list_empty(samples: dict[str, str]) -> None:
    out = p.parse_fallback_list(samples["fallback list"])
    assert out["empty"] is True
    assert out["providers"] == []


def test_sessions_list_empty(samples: dict[str, str]) -> None:
    out = p.parse_sessions_list(samples["sessions list"])
    assert out == {"sessions": [], "empty": True}


def test_sessions_stats(samples: dict[str, str]) -> None:
    out = p.parse_sessions_stats(samples["sessions stats"])
    assert out["total_sessions"] == 0
    assert out["total_messages"] == 0
    assert out["database_size"] == "0.1 MB"


def test_memory_status(samples: dict[str, str]) -> None:
    out = p.parse_memory_status(samples["memory status"])
    assert out["built_in"] == "always active"
    assert "built-in only" in out["provider"]
    names = [pl["name"] for pl in out["plugins"]]
    assert "byterover" in names and "honcho" in names


def test_skills_list(samples: dict[str, str]) -> None:
    out = p.parse_skills_list(samples["skills list"])
    assert out["summary"] == {
        "hub_installed": 0,
        "builtin": 85,
        "local": 0,
        "enabled": 85,
        "disabled": 0,
    }
    # First row has empty Category (should NOT be dropped)
    first = out["skills"][0]
    assert first["Name"] == "dogfood"
    assert first["Category"] == ""
    assert first["Source"] == "builtin"
    assert first["Status"] == "enabled"


def test_bundles_list_empty(samples: dict[str, str]) -> None:
    out = p.parse_bundles_list(samples["bundles list"])
    assert out["empty"] is True
    assert out["bundles_dir"] == "/opt/hermes/.hermes/skill-bundles"


def test_tools_summary(samples: dict[str, str]) -> None:
    out = p.parse_tools_summary(samples["tools --summary"])
    cli = next(pl for pl in out["platforms"] if pl["name"] == "CLI")
    assert cli["enabled"] == 18
    assert cli["total"] == 26
    assert any("Browser Automation" in t["name"] for t in cli["tools"])


def test_webhook_list_disabled(samples: dict[str, str]) -> None:
    out = p.parse_webhook_list(samples["webhook list"])
    assert out == {"webhooks": [], "enabled": False}


def test_gateway_status_running(samples: dict[str, str]) -> None:
    out = p.parse_gateway_status(samples["gateway status"])
    assert out["service"] == "hermes-gateway.service"
    assert out["running"] is True
    assert out["active_state"] == "active"
    assert out["main_pid"] == 8108


def test_gateway_list(samples: dict[str, str]) -> None:
    out = p.parse_gateway_list(samples["gateway list"])
    assert out["gateways"] == [
        {
            "name": "default",
            "current": True,
            "status": "not running",
            "running": False,
        }
    ]


def test_cron_list_empty(samples: dict[str, str]) -> None:
    assert p.parse_cron_list(samples["cron list"]) == {"jobs": [], "empty": True}


def test_kanban_list_empty(samples: dict[str, str]) -> None:
    assert p.parse_kanban_list(samples["kanban list"]) == {
        "tasks": [],
        "empty": True,
    }


def test_curator_status(samples: dict[str, str]) -> None:
    out = p.parse_curator_status(samples["curator status"])
    assert out["enabled"] is True
    assert out["runs"] == 0
    assert out["interval"] == "every 7d"


def test_status_deep_api_keys_clean(samples: dict[str, str]) -> None:
    """API key names must NOT include the ✗/✓ marker — those go in value."""
    out = p.parse_status_deep(samples["status --deep"])
    assert out["Environment"]["Provider"] == "DeepSeek"
    assert out["API Keys"]["OpenRouter"] == "✗ (not set)"
    assert "✗" not in next(iter(out["API Keys"].keys()))
    assert out["Gateway Service"]["Status"] == "✓ running"


def test_doctor(samples: dict[str, str]) -> None:
    out = p.parse_doctor(samples["doctor"])
    assert out["healthy"] is False
    assert out["issue_count"] == 2
    py_section = out["sections"]["Python Environment"]
    assert {"status": "✓", "message": "Python 3.11.15"} in py_section


def test_dump(samples: dict[str, str]) -> None:
    out = p.parse_dump(samples["dump"])
    assert out["model"] == "deepseek-chat"
    assert out["provider"] == "deepseek"
    assert out["api_keys"]["openrouter"] == "not set"
    assert out["features"]["memory_provider"] == "built-in"


def test_insights_empty(samples: dict[str, str]) -> None:
    out = p.parse_insights(samples["insights --days 7"])
    assert out["empty"] is True
    assert "No sessions found" in out["summary"]


def test_checkpoints_status(samples: dict[str, str]) -> None:
    out = p.parse_checkpoints_status(samples["checkpoints status"])
    assert out["total_size"] == "0 B"
    assert out["projects"] == "0"


def test_profile(samples: dict[str, str]) -> None:
    out = p.parse_profile(samples["profile (no subcmd)"])
    assert out["active_profile"] == "default"
    assert out["model"] == "deepseek-chat"
    assert out["provider"] == "deepseek"


def test_version(samples: dict[str, str]) -> None:
    out = p.parse_version(samples["version"])
    assert out["version"] == "0.14.0"
    assert out["update_available"] is True
    assert "30 commits behind" in (out["update_message"] or "")


def test_config_status(samples: dict[str, str]) -> None:
    out = p.parse_config_status(samples["config check"])
    assert out["version"] == "23"
    assert out["version_ok"] is True
    optional_keys = {item["key"] for item in out["optional"]}
    assert "OPENROUTER_API_KEY" in optional_keys
    assert "EXA_API_KEY" in optional_keys
    # OPENROUTER_API_KEY → vision_analyze, mixture_of_agents
    or_item = next(i for i in out["optional"] if i["key"] == "OPENROUTER_API_KEY")
    assert or_item["uses"] == ["vision_analyze", "mixture_of_agents"]
