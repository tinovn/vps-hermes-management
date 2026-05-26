"""Unit tests for v2 stdout parsers."""
from __future__ import annotations

from hermes_mgmt.routes.v2._parsers import (
    parse_check,
    parse_config_set,
    parse_config_show,
    parse_single_line,
    parse_table,
    strip_ansi,
    strip_decorations,
)


class TestStripAnsi:
    def test_drops_color_codes(self) -> None:
        assert strip_ansi("\x1b[31mred\x1b[0m") == "red"

    def test_preserves_unicode(self) -> None:
        assert strip_ansi("⚕ Hermes ✓") == "⚕ Hermes ✓"


class TestStripDecorations:
    def test_drops_box_borders(self) -> None:
        s = "┌───┐\n│ X │\n└───┘"
        assert strip_decorations(s).strip() == "X"


class TestParseConfigShow:
    def test_real_sample(self) -> None:
        s = """┌──┐
│ ⚕ Hermes Configuration │
└──┘

◆ Paths
  Config:       /opt/hermes/.hermes/config.yaml
  Secrets:      /opt/hermes/.hermes/.env

◆ API Keys
  OpenRouter     (not set)
  OpenAI (STT/TTS) (not set)
  Anthropic      (set, sk-****abcd)

◆ Model
  Model:        {'default': 'deepseek-chat', 'provider': 'deepseek'}
  Max turns:    90

────────────────────────────────────────────
  hermes config edit
"""
        out = parse_config_show(s)
        assert out["Paths"]["Config"] == "/opt/hermes/.hermes/config.yaml"
        assert out["Paths"]["Secrets"] == "/opt/hermes/.hermes/.env"

        # API Keys with parens-in-name
        assert out["API Keys"]["OpenRouter"] == "(not set)"
        assert out["API Keys"]["OpenAI (STT/TTS)"] == "(not set)"
        assert out["API Keys"]["Anthropic"] == "(set, sk-****abcd)"

        # Python literal value is parsed
        assert out["Model"]["Model"] == {
            "default": "deepseek-chat",
            "provider": "deepseek",
        }
        # Numbers become ints
        assert out["Model"]["Max turns"] == 90

    def test_empty_input(self) -> None:
        assert parse_config_show("") == {}

    def test_no_section(self) -> None:
        # Lines without a `◆` header are ignored
        assert parse_config_show("some bare text\nanother line") == {}


class TestParseConfigSet:
    def test_with_value(self) -> None:
        s = "✓ Set model.default = claude-sonnet-4-6 in /opt/hermes/.hermes/config.yaml"
        out = parse_config_set(s)
        assert out["key"] == "model.default"
        assert out["value"] == "claude-sonnet-4-6"
        assert out["file"] == "/opt/hermes/.hermes/config.yaml"

    def test_env_key_form(self) -> None:
        # Some keys (like ANTHROPIC_API_KEY) don't echo the value
        s = "✓ Set ANTHROPIC_API_KEY in /opt/hermes/.hermes/.env"
        out = parse_config_set(s)
        assert out["key"] == "ANTHROPIC_API_KEY"
        assert out["value"] is None
        assert out["file"] == "/opt/hermes/.hermes/.env"

    def test_unparseable_returns_raw(self) -> None:
        s = "completely unexpected output"
        out = parse_config_set(s)
        assert out["key"] is None
        assert "_unparsed" in out


class TestParseSingleLine:
    def test_strips_ansi_and_trailing_newline(self) -> None:
        assert parse_single_line("\x1b[32m/foo/bar\x1b[0m\n") == "/foo/bar"


class TestParseCheck:
    def test_clean(self) -> None:
        out = parse_check("All config values present.\n")
        assert out["clean"] is True
        assert out["issues"] == []

    def test_drift(self) -> None:
        out = parse_check("✗ memory.provider is missing\n⚠ display.skin is outdated")
        assert out["clean"] is False
        assert len(out["issues"]) == 2


class TestParseTable:
    def test_basic_table(self) -> None:
        s = """ID    Name        Status
abc1  hourly      running
def2  daily-sync  paused"""
        rows = parse_table(s)
        assert len(rows) == 2
        assert rows[0] == {"ID": "abc1", "Name": "hourly", "Status": "running"}
        assert rows[1] == {"ID": "def2", "Name": "daily-sync", "Status": "paused"}

    def test_no_table(self) -> None:
        assert parse_table("just one line") == []
