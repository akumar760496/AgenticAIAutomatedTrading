"""Authenticate with Dhan and store the access token.

    uv run python -m scripts.dhan_login            # log in if needed
    uv run python -m scripts.dhan_login --force    # log in again regardless

Opens your browser, waits for the redirect, saves the token with owner-only
permissions, and prints when it expires. The token itself is never printed.

The expiry is the number that matters: Dhan does not document how long a
consent-flow token lasts, so this measures it. If it is days, the snapshotter can
be scheduled and this is an occasional chore.
"""

from __future__ import annotations

import sys

from trading_desk.adapters.dhan.consent import ConsentError
from trading_desk.adapters.dhan.login import (
    ensure_valid_token,
    hours_remaining,
    load_valid_token,
)
from trading_desk.adapters.dhan.token_store import DEFAULT_TOKEN_PATH
from trading_desk.infra.config import load_dhan_settings

HOURS_PER_DAY = 24


def main() -> int:
    force = "--force" in sys.argv
    settings = load_dhan_settings()

    existing = load_valid_token()
    if existing is not None and not force:
        print(f"Already authenticated. Token valid for {hours_remaining(existing):.1f} more hours.")
        print("Use --force to log in again.")
        return 0

    try:
        grant = ensure_valid_token(settings, force=True, announce=print)
    except ConsentError as error:
        print(f"Consent flow failed: {error}")
        return 1
    except TimeoutError as error:
        print(f"Timed out waiting for the redirect: {error}")
        return 1

    remaining = hours_remaining(grant)
    print()
    print(f"Token saved to {DEFAULT_TOKEN_PATH} (owner-only permissions)")
    print(f"Expires at : {grant.expiry_time}")
    print(f"Valid for  : {remaining:.1f} hours ({remaining / HOURS_PER_DAY:.1f} days)")
    print()
    if remaining > HOURS_PER_DAY:
        print("Longer than 24 hours, so the snapshotter can be scheduled and this")
        print(f"login is needed roughly every {remaining / HOURS_PER_DAY:.0f} day(s).")
    else:
        print("Under 24 hours. Scheduling will need a daily login or the PIN+TOTP")
        print("provider - see docs/state/ISSUES.md ISS-005.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
