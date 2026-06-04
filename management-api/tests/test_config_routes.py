"""Unit tests for hermes_mgmt.routes.config_routes pure helpers."""
from __future__ import annotations

import yaml

from hermes_mgmt.routes.config_routes import _strip_model_default


class TestStripModelDefault:
    """PUT /api/config/provider clears model.default for codex (ChatGPT account
    rejects an explicit model), but keeps everything else intact."""

    def test_removes_model_default(self, tmp_path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text("model:\n  provider: codex\n  default: gpt-5.1-codex-max\n")
        _strip_model_default(cfg)
        data = yaml.safe_load(cfg.read_text())
        assert "default" not in data["model"]
        assert data["model"]["provider"] == "codex"  # provider preserved

    def test_noop_when_no_default(self, tmp_path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text("model:\n  provider: codex\n")
        _strip_model_default(cfg)  # must not raise
        assert yaml.safe_load(cfg.read_text())["model"]["provider"] == "codex"

    def test_noop_when_file_missing(self, tmp_path) -> None:
        _strip_model_default(tmp_path / "nope.yaml")  # must not raise

    def test_preserves_other_keys(self, tmp_path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "model:\n  provider: codex\n  default: x\nplugins:\n  enabled:\n  - zalo-personal-platform\n"
        )
        _strip_model_default(cfg)
        data = yaml.safe_load(cfg.read_text())
        assert data["plugins"]["enabled"] == ["zalo-personal-platform"]
