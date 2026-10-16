"""Authentication happens once: a live token is reused, a stale one triggers login."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from trading_desk.adapters.dhan.consent import TokenGrant
from trading_desk.adapters.dhan.login import (
    MIN_REMAINING_HOURS,
    hours_remaining,
    load_valid_token,
)
from trading_desk.adapters.dhan.token_store import save_token


def _grant(hours_from_now: float) -> TokenGrant:
    return TokenGrant(
        access_token="token-value",
        expiry_time=datetime.now(UTC) + timedelta(hours=hours_from_now),
        client_id="1000000001",
    )


def test_a_live_token_is_reused(tmp_path: Path) -> None:
    path = tmp_path / "dhan_token.json"
    save_token(_grant(hours_from_now=10), path)

    assert load_valid_token(path) is not None


def test_a_missing_token_file_means_login(tmp_path: Path) -> None:
    assert load_valid_token(tmp_path / "absent.json") is None


def test_an_expired_token_means_login(tmp_path: Path) -> None:
    path = tmp_path / "dhan_token.json"
    save_token(_grant(hours_from_now=-1), path)

    assert load_valid_token(path) is None


def test_a_token_about_to_expire_means_login(tmp_path: Path) -> None:
    """A run takes ~4 minutes. Starting one with minutes left leaves a partial capture."""
    path = tmp_path / "dhan_token.json"
    save_token(_grant(hours_from_now=MIN_REMAINING_HOURS / 2), path)

    assert load_valid_token(path) is None


def test_a_corrupt_token_file_means_login_not_a_crash(tmp_path: Path) -> None:
    path = tmp_path / "dhan_token.json"
    path.write_text("{ not json")

    assert load_valid_token(path) is None


def test_hours_remaining_is_measured_from_now(tmp_path: Path) -> None:
    assert hours_remaining(_grant(hours_from_now=5)) == __import__("pytest").approx(5.0, abs=0.01)
