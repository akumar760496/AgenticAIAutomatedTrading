"""A registered secret must never reach a log line."""

from __future__ import annotations

import logging

import pytest
from pydantic import SecretStr

from trading_desk.infra.logging import (
    REDACTED,
    SecretRedactingFilter,
    clear_secrets,
    redact,
    register_secret,
)

SECRET = "abcdef1234567890-very-secret"


@pytest.fixture(autouse=True)
def _clean_secret_registry() -> None:
    clear_secrets()


def _record(message: str, args: tuple[object, ...] = ()) -> logging.LogRecord:
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=args,
        exc_info=None,
    )


def test_registered_secret_is_redacted_from_a_message() -> None:
    register_secret(SECRET)
    record = _record(f"calling Dhan with token {SECRET}")

    SecretRedactingFilter().filter(record)

    assert SECRET not in record.msg
    assert REDACTED in record.msg


def test_secret_is_redacted_from_format_arguments() -> None:
    """The common accident: log.info("token=%s", token)."""
    register_secret(SECRET)
    record = _record("token=%s", (SECRET,))

    SecretRedactingFilter().filter(record)

    assert record.args is not None
    assert SECRET not in str(record.args)


def test_secretstr_can_be_registered_directly() -> None:
    register_secret(SecretStr(SECRET))

    assert redact(f"value={SECRET}") == f"value={REDACTED}"


def test_short_values_are_not_registered() -> None:
    """Redacting "on" or a 3-digit id would mangle ordinary log text."""
    register_secret("abc")

    assert redact("abc appears here") == "abc appears here"


def test_filter_never_drops_a_record() -> None:
    """Dropping the line would hide the event as well as the secret."""
    register_secret(SECRET)
    record = _record(f"error with {SECRET}")

    assert SecretRedactingFilter().filter(record) is True


def test_non_string_arguments_keep_their_type() -> None:
    """Regression: stringifying args broke %d formatting in httpx's own logging."""
    register_secret(SECRET)
    record = _record('HTTP Request: %s "%s %d %s"', ("POST", "HTTP/1.1", 200, "OK"))

    SecretRedactingFilter().filter(record)

    assert isinstance(record.args, tuple)
    assert record.args[2] == 200
    assert record.getMessage() == 'HTTP Request: POST "HTTP/1.1 200 OK"'


def test_string_arguments_are_still_redacted_alongside_numbers() -> None:
    register_secret(SECRET)
    record = _record("%s %d", (SECRET, 500))

    SecretRedactingFilter().filter(record)

    assert isinstance(record.args, tuple)
    assert record.args[0] == REDACTED
    assert record.args[1] == 500
