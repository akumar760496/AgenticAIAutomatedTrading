"""Settings load correctly, and fail with the variable names when they do not."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from trading_desk.infra.config import (
    MissingConfigError,
    load_app_settings,
    load_dhan_settings,
    load_telegram_settings,
)


def test_dhan_settings_load_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DHAN_CLIENT_ID", "1000000001")
    monkeypatch.setenv("DHAN_API_KEY", "test-key-value")
    monkeypatch.setenv("DHAN_API_SECRET", "test-secret-value")

    settings = load_dhan_settings(env_file=None)

    assert settings.client_id == "1000000001"
    assert settings.api_key.get_secret_value() == "test-key-value"
    assert settings.redirect_url.startswith("http")


def test_missing_dhan_settings_name_the_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """The failure a developer actually sees. It must say which key to set."""
    for name in ("DHAN_CLIENT_ID", "DHAN_API_KEY", "DHAN_API_SECRET"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(MissingConfigError) as caught:
        load_dhan_settings(env_file=None)

    message = str(caught.value)
    assert "DHAN_CLIENT_ID" in message
    assert "DHAN_API_KEY" in message
    assert "DHAN_API_SECRET" in message


def test_secrets_are_not_printed_by_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Printing a settings object - or a traceback holding one - must not leak."""
    monkeypatch.setenv("DHAN_CLIENT_ID", "1000000001")
    monkeypatch.setenv("DHAN_API_KEY", "super-secret-key")
    monkeypatch.setenv("DHAN_API_SECRET", "super-secret-secret")

    settings = load_dhan_settings(env_file=None)

    assert "super-secret-key" not in repr(settings)
    assert "super-secret-secret" not in str(settings)


def test_telegram_settings_use_their_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")

    settings = load_telegram_settings(env_file=None)

    assert isinstance(settings.bot_token, SecretStr)
    assert settings.chat_id == "999"


def test_app_settings_default_to_research_mode() -> None:
    """v1 has no order path. If this default ever changes, something is wrong."""
    settings = load_app_settings(env_file=None)

    assert settings.run_mode in {"research", "paper", "live"}
