"""Stores the access token on disk so it survives a restart.

Written with owner-only permissions into a gitignored directory. It is a
credential: treated like one, never logged, never printed.

The store knows the token's expiry, so callers can fail with "expired at X, run
the login script" rather than discovering it as a 401 three layers down.
"""

from __future__ import annotations

import json
import stat
from datetime import UTC, datetime
from pathlib import Path

from trading_desk.adapters.dhan.consent import TokenGrant

DEFAULT_TOKEN_PATH = Path("secrets/dhan_token.json")

# Owner read/write only. A token readable by other accounts is a token leaked.
OWNER_READ_WRITE = stat.S_IRUSR | stat.S_IWUSR


class TokenExpiredError(RuntimeError):
    """The stored token has expired. The message says what to do about it."""


def save_token(grant: TokenGrant, path: Path = DEFAULT_TOKEN_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "access_token": grant.access_token,
                "expiry_time": grant.expiry_time.isoformat(),
                "client_id": grant.client_id,
            },
            indent=2,
        )
    )
    path.chmod(OWNER_READ_WRITE)


def load_token(path: Path = DEFAULT_TOKEN_PATH) -> TokenGrant:
    if not path.is_file():
        raise FileNotFoundError(
            f"No stored token at {path}. Run: uv run python -m scripts.dhan_login"
        )
    raw = json.loads(path.read_text())
    return TokenGrant(
        access_token=raw["access_token"],
        expiry_time=datetime.fromisoformat(raw["expiry_time"]),
        client_id=raw["client_id"],
    )


class StoredTokenProvider:
    """Reads the token from disk and refuses to hand over an expired one.

    Loads on every call rather than caching, so a token refreshed by the login
    script is picked up by a long-running process without a restart.
    """

    def __init__(self, path: Path = DEFAULT_TOKEN_PATH, now: datetime | None = None) -> None:
        self._path = path
        self._now_override = now

    def _now(self) -> datetime:
        """Tz-aware UTC. The grant's expiry carries IST, so the comparison is safe."""
        if self._now_override is not None:
            return self._now_override
        return datetime.now(UTC)

    def __call__(self) -> str:
        grant = load_token(self._path)
        if grant.expiry_time <= self._now():
            raise TokenExpiredError(
                f"Dhan token expired at {grant.expiry_time}. "
                "Run: uv run python -m scripts.dhan_login"
            )
        return grant.access_token

    def expires_in_hours(self) -> float:
        grant = load_token(self._path)
        return (grant.expiry_time - self._now()).total_seconds() / 3600

    def __repr__(self) -> str:
        return f"StoredTokenProvider(path={self._path}, token=***)"
