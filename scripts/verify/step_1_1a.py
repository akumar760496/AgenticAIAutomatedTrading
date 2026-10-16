"""Verify step 1.1a - Dhan chain adapter, rate limiter, starter universe.

Two kinds of check. The offline ones prove the mapping and the spacing rule. The
live one actually calls Dhan, because a fixture can only prove we parse what we
think they send - it cannot prove they send it.
"""

from __future__ import annotations

from pathlib import Path

from scripts.verify._runner import CheckResult
from trading_desk.adapters.clock import SystemClock
from trading_desk.adapters.dhan.chain import (
    INDEX_SYMBOLS,
    DhanOptionChainAdapter,
)
from trading_desk.adapters.dhan.client import (
    OPTION_CHAIN_MIN_INTERVAL_SECONDS,
    DhanClient,
    RateLimiter,
)
from trading_desk.adapters.dhan.instruments import DhanInstrumentMaster
from trading_desk.adapters.dhan.token import StaticTokenProvider
from trading_desk.application.universe import load_universe_request, resolve_universe
from trading_desk.infra.config import load_dhan_settings

TITLE = "Dhan chain adapter and starter universe"

ROOT = Path(__file__).resolve().parents[2]

EXPECTED_INDEX_LOTS = {"NIFTY": 65, "BANKNIFTY": 30}
EXPECTED_INDEX_COUNT = 2
MIN_STRIKES_IN_A_REAL_CHAIN = 10
SPACING_TOLERANCE_SECONDS = 0.01


def _rate_limiter_spaces_calls() -> tuple[bool, str]:
    """A second call 1s after the first must wait out the remainder."""
    waits: list[float] = []
    limiter = RateLimiter(OPTION_CHAIN_MIN_INTERVAL_SECONDS, sleep=waits.append)
    limiter.wait(now=100.0)
    limiter.wait(now=101.0)
    expected = OPTION_CHAIN_MIN_INTERVAL_SECONDS - 1.0
    ok = len(waits) == 1 and abs(waits[0] - expected) < SPACING_TOLERANCE_SECONDS
    return ok, f"waited {waits[0]:.2f}s" if waits else "did not wait"


def _live_chain_check() -> tuple[bool, str]:
    """Call Dhan for a real NIFTY chain. Fails - never skips - without a token."""
    settings = load_dhan_settings()
    if settings.access_token is None or not settings.access_token.get_secret_value():
        return False, "no DHAN_ACCESS_TOKEN in .env"

    catalogue = DhanInstrumentMaster().option_underlyings(SystemClock().utc_now().date())
    universe = resolve_universe(
        load_universe_request(ROOT / "config" / "universe_starter.yaml"), catalogue
    )
    nifty = next(i for i in universe.instruments if i.symbol == "NIFTY")

    client = DhanClient(
        client_id=settings.client_id,
        token_provider=StaticTokenProvider(settings.access_token.get_secret_value()),
    )
    adapter = DhanOptionChainAdapter(client, SystemClock())

    try:
        expiries = adapter.available_expiries(nifty)
        if not expiries:
            return False, "Dhan listed no expiries for NIFTY"
        chain = adapter.fetch_chain(nifty, expiries[0])
    except Exception as error:
        return False, f"{type(error).__name__}: {str(error)[:70]}"
    finally:
        client.close()

    atm = chain.atm_strike()
    has_greeks = atm.call is not None and atm.call.implied_volatility is not None
    detail = (
        f"{len(chain.strikes)} strikes, spot {chain.spot.rupees():.2f}, "
        f"expiry {chain.expiry_date}, greeks={'yes' if has_greeks else 'NO'}"
    )
    ok = len(chain.strikes) >= MIN_STRIKES_IN_A_REAL_CHAIN and has_greeks
    return ok, detail


def run() -> list[CheckResult]:
    catalogue = DhanInstrumentMaster().option_underlyings(SystemClock().utc_now().date())
    universe = resolve_universe(
        load_universe_request(ROOT / "config" / "universe_starter.yaml"), catalogue
    )
    lots = {i.symbol: i.lot_size for i in universe.instruments if i.symbol in EXPECTED_INDEX_LOTS}
    spacing_ok, spacing_detail = _rate_limiter_spaces_calls()
    live_ok, live_detail = _live_chain_check()

    return [
        CheckResult(
            name="universe_resolves",
            passed=not universe.unavailable,
            measured=f"{len(universe)} instruments, unavailable: {list(universe.unavailable)}",
            requirement="need every configured symbol resolved",
        ),
        CheckResult(
            name="lot_sizes_current",
            passed=lots == EXPECTED_INDEX_LOTS,
            measured=str(lots),
            requirement="need NIFTY 65, BANKNIFTY 30, from the instrument master",
        ),
        CheckResult(
            name="index_segment_mapping",
            passed="NIFTY" in INDEX_SYMBOLS and "RELIANCE" not in INDEX_SYMBOLS,
            measured=f"{len(INDEX_SYMBOLS)} index symbols",
            requirement="need indices separated from stocks",
        ),
        CheckResult(
            name="rate_limiter_spacing",
            passed=spacing_ok,
            measured=spacing_detail,
            requirement=f"need >= {OPTION_CHAIN_MIN_INTERVAL_SECONDS}s between calls",
        ),
        CheckResult(
            name="live_chain_with_greeks",
            passed=live_ok,
            measured=live_detail,
            requirement="need a real NIFTY chain with IV",
        ),
    ]
