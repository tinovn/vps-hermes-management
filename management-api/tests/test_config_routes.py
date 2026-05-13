"""Unit tests for hermes_mgmt.routes.config_routes pure helpers."""
from __future__ import annotations

import pytest

from hermes_mgmt.routes.config_routes import _normalize_model_string


class TestNormalizeModelString:
    """Regression coverage for the double-prefix bug in PUT /api/config/provider.

    Hermes expects ``model.default`` to be ``<provider>/<bare-model>``.
    Earlier the route built it via ``f"{provider}/{model}"`` blindly, which
    produced ``deepseek/deepseek/deepseek-chat`` when callers passed an
    already-prefixed model.
    """

    def test_bare_model_gets_prefixed(self) -> None:
        assert (
            _normalize_model_string("deepseek", "deepseek-v4-flash")
            == "deepseek/deepseek-v4-flash"
        )

    def test_already_prefixed_model_kept_as_is(self) -> None:
        assert (
            _normalize_model_string("deepseek", "deepseek/deepseek-v4-flash")
            == "deepseek/deepseek-v4-flash"
        )

    def test_anthropic_provider(self) -> None:
        assert (
            _normalize_model_string("anthropic", "claude-sonnet-4-6")
            == "anthropic/claude-sonnet-4-6"
        )
        assert (
            _normalize_model_string("anthropic", "anthropic/claude-sonnet-4-6")
            == "anthropic/claude-sonnet-4-6"
        )

    def test_provider_appearing_inside_model_id_not_stripped(self) -> None:
        # Model ids may legitimately contain the provider word elsewhere —
        # only an exact ``<provider>/`` prefix should be stripped.
        assert (
            _normalize_model_string("openai", "openai-codex-1")
            == "openai/openai-codex-1"
        )

    @pytest.mark.parametrize(
        "provider,model,expected",
        [
            ("openrouter", "anthropic/claude-sonnet-4.6",
             "openrouter/anthropic/claude-sonnet-4.6"),
            ("huggingface", "meta-llama/Llama-4-Maverick-17B",
             "huggingface/meta-llama/Llama-4-Maverick-17B"),
        ],
    )
    def test_routed_models_keep_inner_slashes(
        self, provider: str, model: str, expected: str
    ) -> None:
        assert _normalize_model_string(provider, model) == expected
