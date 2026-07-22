"""Unit tests for hermes_mgmt.routes.config_routes pure helpers."""
from __future__ import annotations

from hermes_mgmt.routes.config_routes import (
    CODEX_DEFAULT_MODEL,
    CODEX_SUPPORTED_MODELS,
    resolve_codex_model,
)


class TestResolveCodexModel:
    """Codex model.default must ALWAYS be a non-empty supported slug.

    Empty → cron jobs crash ("'model' must be a non-empty string": the cron
    scheduler reads config.yaml model.default with no catalog fallback).
    Dead slugs (gpt-5.1-codex-max, gpt-5.2-codex...) → HTTP 400 from the
    ChatGPT Codex backend.
    """

    def test_empty_falls_back_to_default(self) -> None:
        assert resolve_codex_model("") == CODEX_DEFAULT_MODEL
        assert resolve_codex_model("   ") == CODEX_DEFAULT_MODEL

    def test_dead_slug_falls_back_to_default(self) -> None:
        assert resolve_codex_model("gpt-5.1-codex-max") == CODEX_DEFAULT_MODEL
        assert resolve_codex_model("gpt-5.2-codex") == CODEX_DEFAULT_MODEL
        assert resolve_codex_model("gpt-5.3-codex-spark") == CODEX_DEFAULT_MODEL

    def test_supported_slug_kept(self) -> None:
        for slug in CODEX_SUPPORTED_MODELS:
            assert resolve_codex_model(slug) == slug

    def test_supported_slug_stripped(self) -> None:
        assert resolve_codex_model(f"  {CODEX_DEFAULT_MODEL} ") == CODEX_DEFAULT_MODEL

    def test_default_is_supported(self) -> None:
        assert CODEX_DEFAULT_MODEL in CODEX_SUPPORTED_MODELS
