"""Typed settings, loaded from the environment or .env.

Settings are split by concern rather than held in one object, so a component asks
only for what it needs. That matters during a phased build: step 1.1 needs Dhan
credentials months before an Anthropic key exists, and loading one big object would
force every key to be present from day one.

Secrets are SecretStr, so printing a settings object or a traceback shows
`SecretStr('**********')` rather than the value. Call get_secret_value() only where
the credential is actually used, and register it for redaction first (see logging.py).
"""

from __future__ import annotations

from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = ".env"


class MissingConfigError(RuntimeError):
    """Raised at startup when required settings are absent.

    Carries the exact environment variable names so the message is actionable.
    A None that surfaces three layers later is the failure this prevents.
    """


def _config(env_prefix: str = "") -> SettingsConfigDict:
    """Shared settings config. A function, so each class gets its own dict."""
    return SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        env_prefix=env_prefix,
        extra="ignore",  # other sections' keys live in the same .env
        case_sensitive=False,
    )


class DhanSettings(BaseSettings):
    """Dhan broker credentials. Needed from step 1.1 onwards."""

    model_config = _config(env_prefix="DHAN_")

    client_id: str
    api_key: SecretStr
    api_secret: SecretStr
    redirect_url: str = "http://localhost:8080/dhan/callback"


class LlmSettings(BaseSettings):
    """Anthropic access and the spend cap. Needed from step 3.1."""

    model_config = _config()

    anthropic_api_key: SecretStr
    llm_monthly_cap_inr: int = Field(default=2000, gt=0)


class TelegramSettings(BaseSettings):
    """Briefing delivery. The whole of v1's output path. Needed from step 3.5."""

    model_config = _config(env_prefix="TELEGRAM_")

    bot_token: SecretStr
    chat_id: str


class InfraSettings(BaseSettings):
    """Database and Redis. Needed from step 0.5."""

    model_config = _config()

    database_url: SecretStr  # contains a password
    redis_url: str


class AppSettings(BaseSettings):
    """Runtime behaviour. Always available, with safe defaults."""

    model_config = _config()

    run_mode: str = "research"
    log_level: str = "INFO"


def _missing_env_names(error: ValidationError, prefix: str) -> list[str]:
    """Turn pydantic's field errors into the env var names the user actually sets."""
    names = []
    for item in error.errors():
        field = str(item["loc"][0]) if item["loc"] else "?"
        names.append(f"{prefix}{field}".upper())
    return sorted(names)


def load_dhan_settings(env_file: str | None = ENV_FILE) -> DhanSettings:
    """Load Dhan credentials, or fail with the exact variable names that are missing.

    env_file=None reads the process environment only. Tests pass None so the suite
    can never pick up real credentials from a developer's .env.
    """
    try:
        return DhanSettings(_env_file=env_file)
    except ValidationError as error:
        missing = _missing_env_names(error, prefix="DHAN_")
        raise MissingConfigError(
            f"Dhan settings incomplete. Set these in {ENV_FILE}: {', '.join(missing)}"
        ) from None  # from None: the original error can echo field context, not values


def load_llm_settings(env_file: str | None = ENV_FILE) -> LlmSettings:
    try:
        return LlmSettings(_env_file=env_file)
    except ValidationError as error:
        missing = _missing_env_names(error, prefix="")
        raise MissingConfigError(
            f"LLM settings incomplete. Set these in {ENV_FILE}: {', '.join(missing)}"
        ) from None


def load_telegram_settings(env_file: str | None = ENV_FILE) -> TelegramSettings:
    try:
        return TelegramSettings(_env_file=env_file)
    except ValidationError as error:
        missing = _missing_env_names(error, prefix="TELEGRAM_")
        raise MissingConfigError(
            f"Telegram settings incomplete. Set these in {ENV_FILE}: {', '.join(missing)}"
        ) from None


def load_infra_settings(env_file: str | None = ENV_FILE) -> InfraSettings:
    try:
        return InfraSettings(_env_file=env_file)
    except ValidationError as error:
        missing = _missing_env_names(error, prefix="")
        raise MissingConfigError(
            f"Infrastructure settings incomplete. Set these in {ENV_FILE}: {', '.join(missing)}"
        ) from None


def load_app_settings(env_file: str | None = ENV_FILE) -> AppSettings:
    """Always succeeds - every field has a default."""
    return AppSettings(_env_file=env_file)
