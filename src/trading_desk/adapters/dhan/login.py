"""Gets a usable Dhan access token, logging in through the browser if needed.

One entry point: `ensure_valid_token()`. It returns a live token, running the
consent flow only when there isn't one. Every script calls it at startup, so
authentication is something that happens once rather than a step to remember.

Opening the browser is done by calling the platform's own opener rather than
`webbrowser.open()`. On macOS that falls back to an AppleScript handler which
returns True and silently does nothing under `uv run` - observed on 2026-10-16.
The URL is always printed as well, so a failed launch is an inconvenience rather
than a dead end.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import webbrowser
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from trading_desk.adapters.clock import SystemClock
from trading_desk.adapters.dhan.callback_server import wait_for_token_id
from trading_desk.adapters.dhan.consent import DhanConsentFlow, TokenGrant
from trading_desk.adapters.dhan.token_store import (
    DEFAULT_TOKEN_PATH,
    load_token,
    save_token,
)
from trading_desk.infra.config import DhanSettings

logger = logging.getLogger("trading_desk.dhan.login")

# How the flow talks to whoever started it. A CLI passes print; a scheduled job
# gets the default and the messages land in the log where they can be read later.
Announce = Callable[[str], None]

DEFAULT_CALLBACK_PORT = 8080

# Refresh when less than this is left, rather than at the moment of expiry. A
# snapshot run takes about four minutes; starting one with two minutes of token
# left would fail halfway through and leave a partial capture.
MIN_REMAINING_HOURS = 1.0


def open_in_browser(url: str) -> bool:
    """Open a URL using the platform opener. Returns whether it was launched."""
    commands = {
        "darwin": ["open", url],
        "linux": ["xdg-open", url],
        "win32": ["cmd", "/c", "start", "", url],
    }
    command = commands.get(sys.platform)

    if command is not None:
        try:
            result = subprocess.run(command, check=False, capture_output=True, timeout=10)
            if result.returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            pass

    # Last resort. Unreliable on macOS, which is why it is not tried first.
    return webbrowser.open(url)


def hours_remaining(grant: TokenGrant, now: datetime | None = None) -> float:
    """Life left in a grant. `now` is injectable so tests need no monkeypatching.

    Defaults through the clock adapter rather than calling datetime.now() here,
    which is CLAUDE.md rule 5 and was caught by `verify 0_4` rather than by review.
    """
    reference = SystemClock().utc_now() if now is None else now
    return (grant.expiry_time - reference).total_seconds() / 3600


def load_valid_token(path: Path = DEFAULT_TOKEN_PATH) -> TokenGrant | None:
    """The stored token if it has useful life left, else None."""
    try:
        grant = load_token(path)
    except (FileNotFoundError, KeyError, ValueError):
        return None
    if hours_remaining(grant) < MIN_REMAINING_HOURS:
        return None
    return grant


def _log(message: str) -> None:
    logger.info("%s", message)


def run_consent_flow(settings: DhanSettings, port: int, announce: Announce = _log) -> TokenGrant:
    """The interactive part: browser login, then exchange the tokenId."""
    flow = DhanConsentFlow(
        client_id=settings.client_id,
        app_id=settings.api_key.get_secret_value(),
        app_secret=settings.api_secret.get_secret_value(),
    )
    try:
        consent_app_id = flow.generate_consent()
        login_url = flow.login_url(consent_app_id)

        launched = open_in_browser(login_url)
        if launched:
            announce("Browser opened for Dhan login. Complete the login there.")
        else:
            announce("Could not open a browser automatically.")
        announce(f"If nothing opened, visit:\n  {login_url}\n")
        announce(f"Waiting for Dhan to redirect to port {port} ...")

        token_id = wait_for_token_id(port=port)
        return flow.consume_consent(token_id)
    finally:
        flow.close()


def ensure_valid_token(
    settings: DhanSettings,
    path: Path = DEFAULT_TOKEN_PATH,
    force: bool = False,
    announce: Announce = _log,
) -> TokenGrant:
    """Return a live token, logging in through the browser only if needed.

    force=True always re-authenticates, which is what the login script does when
    run deliberately.
    """
    if not force:
        existing = load_valid_token(path)
        if existing is not None:
            return existing

    port = urlparse(settings.redirect_url).port or DEFAULT_CALLBACK_PORT
    grant = run_consent_flow(settings, port, announce)
    save_token(grant, path)
    return grant
