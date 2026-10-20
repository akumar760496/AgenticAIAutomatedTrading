"""A registered secret must never reach a log line."""

from __future__ import annotations

import logging
from decimal import Decimal

import httpx
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


class TestANonStringArgumentThatRendersToASecret:
    """The gap that leaked a live Telegram bot token on 2026-10-19.

    httpx logs `'HTTP Request: %s %s "%s %d %s"'` and passes `request.url` as an
    `httpx.URL` object. The filter skipped it as "not a string", and the
    formatter stringified it afterwards - so the token in the URL path reached
    stderr in full, with every piece of the redaction machinery working as
    designed and none of it looking at that argument.
    """

    def test_an_object_whose_text_holds_a_secret_is_redacted(self) -> None:
        register_secret(SECRET)

        class Url:
            """Stands in for httpx.URL: not a str, renders to one."""

            def __str__(self) -> str:
                return f"https://api.telegram.org/bot{SECRET}/sendMessage"

        record = _record('HTTP Request: %s %s "%s %d %s"', ("POST", Url(), "HTTP/1.1", 200, "OK"))

        SecretRedactingFilter().filter(record)

        rendered = record.getMessage()
        assert SECRET not in rendered
        assert REDACTED in rendered
        # The status code must survive as an int, or %d raises.
        assert isinstance(record.args, tuple)
        assert record.args[3] == 200

    def test_the_real_httpx_url_type_is_redacted(self) -> None:
        """Against the actual class, not only a stand-in. If httpx changes how
        a URL renders, this is what notices."""
        register_secret(SECRET)
        url = httpx.URL(f"https://api.telegram.org/bot{SECRET}/sendMessage")
        record = _record("HTTP Request: %s %s", ("POST", url))

        SecretRedactingFilter().filter(record)

        assert SECRET not in record.getMessage()

    def test_a_clean_object_keeps_its_identity(self) -> None:
        """Only arguments that actually carry a secret are replaced, so a
        format specifier still receives the type it was written for."""
        register_secret(SECRET)
        value = Decimal("1400.50")
        record = _record("price %s", (value,))

        SecretRedactingFilter().filter(record)

        assert isinstance(record.args, tuple)
        assert record.args[0] is value

    def test_an_object_whose_str_raises_does_not_take_the_log_line_down(self) -> None:
        """Losing the log line would hide the event, which is the thing the
        filter exists to avoid (it never drops a record)."""
        register_secret(SECRET)

        class Hostile:
            def __str__(self) -> str:
                raise RuntimeError("no")

        hostile = Hostile()
        record = _record("value %r", (hostile,))

        assert SecretRedactingFilter().filter(record) is True
        assert isinstance(record.args, tuple)
        assert record.args[0] is hostile
