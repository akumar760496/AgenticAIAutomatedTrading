"""Verify step 0.3 - configuration and secrets.

Checks the real .env loads and that secrets cannot leak. It never prints a
credential: only presence, length bands and booleans.
"""

from __future__ import annotations

import logging
from io import StringIO
from pathlib import Path

from scripts.verify._runner import CheckResult
from trading_desk.infra.config import (
    MissingConfigError,
    load_app_settings,
    load_dhan_settings,
)
from trading_desk.infra.logging import (
    SecretRedactingFilter,
    clear_secrets,
    register_secret,
)

TITLE = "Configuration and secrets"

ROOT = Path(__file__).resolve().parents[2]

EXPECTED_KEYS = [
    "DHAN_CLIENT_ID",
    "DHAN_API_KEY",
    "DHAN_API_SECRET",
    "DHAN_REDIRECT_URL",
    "ANTHROPIC_API_KEY",
    "LLM_MONTHLY_CAP_INR",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "DATABASE_URL",
    "REDIS_URL",
    "RUN_MODE",
    "LOG_LEVEL",
]


def _example_keys() -> set[str]:
    text = (ROOT / ".env.example").read_text()
    return {
        line.split("=", 1)[0].strip()
        for line in text.splitlines()
        if "=" in line and not line.strip().startswith("#")
    }


def _dhan_loads() -> tuple[bool, str]:
    """Load the real Dhan settings. Reports field presence only, never values."""
    try:
        settings = load_dhan_settings()
    except MissingConfigError as error:
        # The message names env vars, not values - safe to show.
        return False, str(error)[:60]

    has_all = bool(
        settings.client_id
        and settings.api_key.get_secret_value()
        and settings.api_secret.get_secret_value()
    )
    return has_all, "all three present" if has_all else "one or more blank"


def _secret_survives_a_log_line() -> bool:
    """Write a registered secret through a real logger and look for it in the output."""
    clear_secrets()
    secret = "verify-only-fake-secret-value"
    register_secret(secret)

    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(SecretRedactingFilter())
    logger = logging.getLogger("verify.step_0_3")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    logger.info("token=%s suffix", secret)
    logger.info("inline %s here", secret)

    clear_secrets()
    return secret not in stream.getvalue()


def _repr_hides_secrets() -> bool:
    try:
        settings = load_dhan_settings()
    except MissingConfigError:
        return False
    rendered = f"{settings!r} {settings}"
    return settings.api_secret.get_secret_value() not in rendered


def run() -> list[CheckResult]:
    example = _example_keys()
    missing_keys = [key for key in EXPECTED_KEYS if key not in example]
    dhan_ok, dhan_detail = _dhan_loads()
    env_mode = load_app_settings().run_mode

    return [
        CheckResult(
            name="env_example_committed",
            passed=(ROOT / ".env.example").is_file(),
            measured=str((ROOT / ".env.example").is_file()),
            requirement="need true",
        ),
        CheckResult(
            name="env_example_keys",
            passed=not missing_keys,
            measured=f"missing {missing_keys}" if missing_keys else f"{len(example)} keys",
            requirement=f"need all {len(EXPECTED_KEYS)}",
        ),
        CheckResult(
            name="dotenv_present_locally",
            passed=(ROOT / ".env").is_file(),
            measured=str((ROOT / ".env").is_file()),
            requirement="need true (not committed)",
        ),
        CheckResult(
            name="dhan_settings_load",
            passed=dhan_ok,
            measured=dhan_detail,
            requirement="need all three credentials",
        ),
        CheckResult(
            name="secret_redacted_from_logs",
            passed=_secret_survives_a_log_line(),
            measured=str(_secret_survives_a_log_line()),
            requirement="need true - secret must not reach a log line",
        ),
        CheckResult(
            name="secret_hidden_in_repr",
            passed=_repr_hides_secrets(),
            measured=str(_repr_hides_secrets()),
            requirement="need true - printing settings must not leak",
        ),
        CheckResult(
            name="run_mode",
            passed=env_mode == "research",
            measured=env_mode,
            requirement="need research (v1 has no order path)",
        ),
    ]
