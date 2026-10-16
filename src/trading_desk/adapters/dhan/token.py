"""Where the Dhan access token comes from.

Three ways exist and the choice is still open, so it is a swappable piece rather
than a hardcoded flow:

1. `StaticTokenProvider` - a 24-hour token generated on web.dhan.co and pasted
   into .env. Simplest, and useless for anything unattended.
2. Consent flow (API key + secret, browser login) - not implemented yet. The
   browser step means it cannot run on a schedule either.
3. PIN + TOTP via auth.dhan.co/app/generateAccessToken - the only fully
   unattended option, and it means the account PIN and TOTP seed live in .env.
   That is a deliberate decision, not a default, so it is not implemented until
   it is chosen.

The snapshotter depends on the Protocol, so switching later changes one line of
wiring.
"""

from __future__ import annotations

from typing import Protocol


class TokenProvider(Protocol):
    def __call__(self) -> str:
        """Return a currently valid access token, or raise if none is available."""
        ...


class StaticTokenProvider:
    """A token supplied once, typically from DHAN_ACCESS_TOKEN in .env.

    Holds the token in memory and never logs it. Expiry is not tracked here: an
    expired token surfaces as a 401 from the API, which is clearer than a local
    guess about validity that can itself be wrong.
    """

    def __init__(self, token: str) -> None:
        if not token or not token.strip():
            raise ValueError(
                "DHAN_ACCESS_TOKEN is empty. Generate one at web.dhan.co "
                "(My Profile -> Access DhanHQ APIs) and put it in .env"
            )
        self._token = token.strip()

    def __call__(self) -> str:
        return self._token

    def __repr__(self) -> str:
        # Never print the token, not even truncated.
        return "StaticTokenProvider(token=***)"
