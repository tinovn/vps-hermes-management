from __future__ import annotations

from pathlib import Path

import pytest

from hermes_mgmt.env_file import delete_env, list_env, mask_value, read_env, set_env


def test_read_env_basic(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("FOO=bar\nBAZ=qux\n", encoding="utf-8")
    result = read_env(env)
    assert result == {"FOO": "bar", "BAZ": "qux"}


def test_read_env_ignores_comments_and_blanks(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("# comment\n\nFOO=bar\n", encoding="utf-8")
    result = read_env(env)
    assert result == {"FOO": "bar"}


def test_read_env_handles_quoted_values(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text('FOO="hello world"\nBAR=\'single\'\n', encoding="utf-8")
    result = read_env(env)
    assert result["FOO"] == "hello world"
    assert result["BAR"] == "single"


def test_read_env_missing_file(tmp_path: Path) -> None:
    result = read_env(tmp_path / "nonexistent.env")
    assert result == {}


def test_set_env_adds_new_key(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("EXISTING=value\n", encoding="utf-8")
    set_env(env, "NEW_KEY", "new_value")
    result = read_env(env)
    assert result["NEW_KEY"] == "new_value"
    assert result["EXISTING"] == "value"


def test_set_env_updates_existing_key(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("FOO=old\nBAR=keep\n", encoding="utf-8")
    set_env(env, "FOO", "new")
    result = read_env(env)
    assert result["FOO"] == "new"
    assert result["BAR"] == "keep"


def test_set_env_preserves_comments(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("# top comment\nFOO=old\n# bottom comment\n", encoding="utf-8")
    set_env(env, "FOO", "new")
    content = env.read_text()
    assert "# top comment" in content
    assert "# bottom comment" in content
    assert "FOO=new" in content


def test_set_env_preserves_order(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("A=1\nB=2\nC=3\n", encoding="utf-8")
    set_env(env, "B", "updated")
    lines = [ln for ln in env.read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    keys = [ln.split("=")[0] for ln in lines]
    assert keys == ["A", "B", "C"]


def test_set_env_invalid_key_raises(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid env key"):
        set_env(env, "invalid-key", "value")


def test_set_env_creates_file_if_missing(tmp_path: Path) -> None:
    env = tmp_path / "new_dir" / ".env"
    set_env(env, "KEY", "val")
    assert read_env(env) == {"KEY": "val"}


def test_delete_env_removes_key(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("FOO=bar\nBAZ=qux\n", encoding="utf-8")
    found = delete_env(env, "FOO")
    assert found is True
    result = read_env(env)
    assert "FOO" not in result
    assert result["BAZ"] == "qux"


def test_delete_env_returns_false_if_missing(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("FOO=bar\n", encoding="utf-8")
    found = delete_env(env, "NONEXISTENT")
    assert found is False


def test_delete_env_missing_file(tmp_path: Path) -> None:
    found = delete_env(tmp_path / "ghost.env", "KEY")
    assert found is False


def test_mask_value_api_key() -> None:
    masked = mask_value("OPENAI_API_KEY", "sk-abcdefgh1234")
    assert "1234" in masked
    assert "sk-abcdefgh" not in masked
    assert "****" in masked


def test_mask_value_token() -> None:
    masked = mask_value("TELEGRAM_BOT_TOKEN", "1234567890:ABCDEFGHIJ")
    assert "****" in masked


def test_mask_value_secret() -> None:
    masked = mask_value("HERMES_MGMT_SESSION_SECRET", "supersecretvalue")
    assert "****" in masked
    assert "alue" in masked  # last 4 chars


def test_mask_value_password() -> None:
    masked = mask_value("HERMES_LOGIN_HASH", "$2b$12$somehash")
    assert "****" in masked


def test_mask_value_unmasked_fields() -> None:
    assert mask_value("DOMAIN", "example.com") == "example.com"
    assert mask_value("HERMES_MGMT_PORT", "9997") == "9997"
    assert mask_value("HERMES_LOGIN_USER", "admin") == "admin"


def test_list_env_masking(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "OPENAI_API_KEY=sk-verylongkey1234\nDOMAIN=example.com\n",
        encoding="utf-8",
    )
    result = list_env(env, mask=True)
    assert "1234" in result["OPENAI_API_KEY"]
    assert "verylongkey" not in result["OPENAI_API_KEY"]
    assert result["DOMAIN"] == "example.com"


def test_list_env_no_masking(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=sk-secret\nDOMAIN=example.com\n", encoding="utf-8")
    result = list_env(env, mask=False)
    assert result["OPENAI_API_KEY"] == "sk-secret"
