"""Logging setup with secret redaction.

A credential reaching a log line is the realistic leak path: logs get pasted into
chat windows, uploaded to issue trackers and shipped to third-party services.
SecretStr stops accidental printing of a settings object; this stops the case where
code deliberately formats the value into a message.

Register every secret once at startup, before anything is logged:

    settings = load_dhan_settings()
    register_secret(settings.api_secret)
    configure_logging()
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from pydantic import SecretStr

REDACTED = "***"

# Module-level because a logging filter cannot easily be handed state, and because
# the set of secrets is genuinely global to the process.
_secrets: set[str] = set()

# Short values would match far too much ordinary text ("1", "on", a client id).
# Redaction is for credentials, and real ones are long.
MIN_SECRET_LENGTH = 8


def register_secret(value: SecretStr | str) -> None:
    """Add a value to the redaction set. Safe to call twice; ignores short values."""
    raw = value.get_secret_value() if isinstance(value, SecretStr) else value
    if len(raw) >= MIN_SECRET_LENGTH:
        _secrets.add(raw)


def clear_secrets() -> None:
    """Only for tests. Production registers secrets once and never clears them."""
    _secrets.clear()


def redact(text: str) -> str:
    """Replace every registered secret in `text` with ***."""
    for secret in _secrets:
        text = text.replace(secret, REDACTED)
    return text


def _redact_arg(value: Any) -> Any:
    """One log argument, redacted only if its text holds a secret.

    Returns the value unchanged when it is clean, so the format specifier it
    was written for still receives the type it expects.
    """
    if isinstance(value, str):
        return redact(value)

    # Not a string: it may still render to one that carries a secret, which is
    # exactly how an httpx.URL leaked a bot token. Compare the rendered text.
    try:
        rendered = str(value)
    except Exception:  # a __str__ that raises must not take the log line down
        return value

    cleaned = redact(rendered)
    return cleaned if cleaned != rendered else value


class SecretRedactingFilter(logging.Filter):
    """Redacts registered secrets from the message and its arguments.

    Returns True always - this filter rewrites records, it never drops them.
    Dropping a log line because it contained a secret would hide the event too.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)

        if record.args:
            record.args = self._redact_args(record.args)

        return True

    @staticmethod
    def _redact_args(args: Any) -> Any:
        """Redact any argument whose text contains a secret, whatever its type.

        **A string check is not enough, and that gap leaked a live bot token.**
        httpx logs `"HTTP Request: %s %s ..."` passing `request.url` as an
        `httpx.URL` object, not a str. The old filter skipped it as "not a
        string", and the formatter stringified it afterwards - so the token in
        the URL path reached stderr in full.

        The rule that replaced it: stringify the argument, and only substitute
        when that text actually contains a registered secret. That keeps the
        original behaviour for numbers, which is what the previous version was
        really protecting - httpx logs the status code with `%d`, and handing
        `%d` a str raises "a real number is required, not str". A number's text
        can never contain a 40-character token, so a number is never touched.
        """
        if isinstance(args, dict):
            return {key: _redact_arg(value) for key, value in args.items()}
        if isinstance(args, tuple):
            return tuple(_redact_arg(value) for value in args)
        return _redact_arg(args)


def configure_logging(level: str = "INFO") -> None:
    """Set up stderr logging with redaction attached. Call once, at startup."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s %(message)s"))
    handler.addFilter(SecretRedactingFilter())

    root = logging.getLogger()
    root.handlers.clear()  # replace, so a second call does not double-log
    root.addHandler(handler)
    root.setLevel(level.upper())
