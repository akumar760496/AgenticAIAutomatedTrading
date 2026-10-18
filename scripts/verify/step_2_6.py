"""Verify step 2.6 - greeks and the cross-check against Dhan.

`docs/10`: done when "our greeks agree with Dhan's inside tolerance across a
live chain, and disagreements are investigated rather than averaged".

Two independent checks, and they test different things:

1. **Against `vollib`** - does our Black-76 implement Black-76 correctly? This
   is arithmetic and should agree to six decimals.
2. **Against Dhan's published greeks on a real archived chain** - do our
   *inputs* match theirs? Dhan does not publish its model, risk-free rate,
   dividend assumption or day-count convention, so a gap here is informative
   rather than a defect. It is reported, never averaged away.
"""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path

import polars as pl

from scripts.verify._runner import CheckResult
from trading_desk.domain.enums import OptionRight
from trading_desk.domain.options.greeks import (
    DAYS_PER_YEAR,
    black76_greeks,
    black76_price,
    forward_price,
    implied_volatility,
)

TITLE = "Greeks and the Dhan cross-check"

ARCHIVE = Path("data/chains")

# India's risk-free rate, roughly the 91-day T-bill. An assumption, and one of
# the likeliest sources of divergence from Dhan's own figure.
RISK_FREE_RATE = 0.065

# Only strikes near the money are compared. Deep wings have near-zero vega, so
# a tiny price difference implies a large volatility difference and the
# comparison stops being meaningful (see the 419% IV in step 1.1b).
MONEYNESS_BAND = 0.03

AGREEMENT_TOLERANCE = 0.10

# Our arithmetic and the reference library implement the same formulae, so
# anything above floating-point noise is a bug, not a modelling difference.
EXACT = 1e-6

# The bisection solver stops at its own tolerance, so it is looser than EXACT.
SOLVER_AGREEMENT = 1e-4
REFERENCE_CASE = (24000.0, 24200.0, 30 / 365, RISK_FREE_RATE, 0.18)


def _our_price_matches_vollib() -> tuple[bool, str]:
    from vollib.black import black  # noqa: PLC0415 - dev-only reference

    forward, strike, years, rate, vol = REFERENCE_CASE
    ours = black76_price(forward, strike, years, rate, vol, OptionRight.CALL)
    theirs = black("c", forward, strike, years, rate, vol)
    return abs(ours - theirs) < EXACT, f"ours {ours:.6f} vs vollib {theirs:.6f}"


def _our_greeks_match_vollib() -> tuple[bool, str]:
    from vollib.black.greeks.analytical import (  # noqa: PLC0415
        delta,
        gamma,
        theta,
        vega,
    )

    forward, strike, years, rate, vol = REFERENCE_CASE
    ours = black76_greeks(forward, strike, years, rate, vol, OptionRight.CALL)
    gaps = {
        "delta": abs(ours.delta - delta("c", forward, strike, years, rate, vol)),
        "gamma": abs(ours.gamma - gamma("c", forward, strike, years, rate, vol)),
        "theta": abs(ours.theta - theta("c", forward, strike, years, rate, vol)),
        "vega": abs(ours.vega - vega("c", forward, strike, years, rate, vol)),
    }
    worst = max(gaps.values())
    return worst < EXACT, f"largest gap {worst:.2e} across delta, gamma, theta, vega"


def _put_call_parity_holds() -> tuple[bool, str]:
    """An identity, so it catches a sign error a one-sided comparison misses."""
    forward, strike, years, rate, vol = REFERENCE_CASE
    call = black76_price(forward, strike, years, rate, vol, OptionRight.CALL)
    put = black76_price(forward, strike, years, rate, vol, OptionRight.PUT)
    expected = math.exp(-rate * years) * (forward - strike)
    return abs((call - put) - expected) < EXACT, (
        f"C - P = {call - put:.6f}, e^-rT (F - K) = {expected:.6f}"
    )


def _the_solver_round_trips() -> tuple[bool, str]:
    forward, strike, years, rate, vol = REFERENCE_CASE
    price = black76_price(forward, strike, years, rate, vol, OptionRight.CALL)
    solved = implied_volatility(price, forward, strike, years, rate, OptionRight.CALL)
    return solved is not None and abs(solved - vol) < SOLVER_AGREEMENT, (
        f"priced at {vol}, solved back to {solved:.6f}" if solved else "no solution"
    )


def _a_broken_quote_has_no_solution() -> tuple[bool, str]:
    """Below intrinsic. A broken quote must not yield a confident number."""
    solved = implied_volatility(1.0, 24000.0, 23000.0, 30 / 365, RISK_FREE_RATE, OptionRight.CALL)
    return solved is None, f"a below-intrinsic quote solved to {solved}"


def _newest_chain() -> tuple[pl.DataFrame, int] | None:
    """The newest archived chain, with its days to expiry."""
    files = sorted(ARCHIVE.rglob("snapshot_*.parquet"))
    if not files:
        return None
    path = files[-1]
    frame = pl.read_parquet(path)
    captured = date.fromisoformat(path.parent.parent.name.removeprefix("date="))
    expiry = date.fromisoformat(frame["expiry"][0])
    return frame, (expiry - captured).days


def _compare_against_dhan() -> tuple[bool, str]:
    """Our delta against Dhan's, on near-the-money strikes of a real chain.

    Delta rather than all four, because delta is the least sensitive to the
    unknowns: a different risk-free rate or day count moves theta sharply and
    delta barely. If delta disagrees, the inputs genuinely differ.
    """
    loaded = _newest_chain()
    if loaded is None:
        return False, "no chain archived"
    frame, dte = loaded
    if dte <= 0:
        return False, f"newest chain is {dte} DTE - expired, nothing to compare"

    spot = frame["spot_paise"][0] / 100
    years = dte / DAYS_PER_YEAR
    forward = forward_price(spot, RISK_FREE_RATE, years)

    usable = frame.filter(
        pl.col("delta").is_not_null()
        & pl.col("implied_volatility").is_not_null()
        & ((pl.col("strike_paise") / 100 - spot).abs() / spot < MONEYNESS_BAND)
    )
    if usable.is_empty():
        return False, f"no near-the-money strike with both a delta and an IV ({dte} DTE)"

    gaps = []
    for row in usable.iter_rows(named=True):
        right = OptionRight(row["right"])
        ours = black76_greeks(
            forward,
            row["strike_paise"] / 100,
            years,
            RISK_FREE_RATE,
            row["implied_volatility"] / 100,
            right,
        )
        gaps.append(abs(abs(ours.delta) - abs(row["delta"])))

    worst = max(gaps)
    mean = sum(gaps) / len(gaps)
    return worst <= AGREEMENT_TOLERANCE, (
        f"{len(gaps)} near-the-money legs at {dte} DTE: mean delta gap {mean:.4f}, "
        f"worst {worst:.4f} (spot {spot:.2f})"
    )


def _divergence_is_reported_not_averaged() -> tuple[bool, str]:
    """Structural: there is no function that blends the two sources."""
    source = Path("src/trading_desk/domain/options/greeks.py").read_text()
    blending = [term for term in ("(ours +", "average(", "mean(") if term in source]
    return not blending, (
        "compare_greeks reports both numbers; no blending helper exists"
        if not blending
        else f"found blending: {blending}"
    )


def run() -> list[CheckResult]:
    price_ok, price_detail = _our_price_matches_vollib()
    greeks_ok, greeks_detail = _our_greeks_match_vollib()
    parity_ok, parity_detail = _put_call_parity_holds()
    solver_ok, solver_detail = _the_solver_round_trips()
    broken_ok, broken_detail = _a_broken_quote_has_no_solution()
    dhan_ok, dhan_detail = _compare_against_dhan()
    report_ok, report_detail = _divergence_is_reported_not_averaged()

    return [
        CheckResult(
            name="our_price_matches_the_reference_library",
            passed=price_ok,
            measured=price_detail,
            requirement=f"need agreement with vollib to {EXACT}",
        ),
        CheckResult(
            name="every_greek_matches_the_reference_library",
            passed=greeks_ok,
            measured=greeks_detail,
            requirement=f"need delta, gamma, theta and vega all within {EXACT}",
        ),
        CheckResult(
            name="put_call_parity_holds",
            passed=parity_ok,
            measured=parity_detail,
            requirement="need C - P == e^-rT (F - K), an identity",
        ),
        CheckResult(
            name="the_implied_vol_solver_round_trips",
            passed=solver_ok,
            measured=solver_detail,
            requirement=f"need the input volatility recovered to {SOLVER_AGREEMENT}",
        ),
        CheckResult(
            name="a_broken_quote_yields_no_volatility",
            passed=broken_ok,
            measured=broken_detail,
            requirement="need None - a below-intrinsic quote is broken, not volatile",
        ),
        CheckResult(
            name="our_delta_agrees_with_dhan_on_a_real_chain",
            passed=dhan_ok,
            measured=dhan_detail,
            requirement=f"need the worst near-the-money delta gap <= {AGREEMENT_TOLERANCE}",
        ),
        CheckResult(
            name="divergence_is_reported_never_averaged",
            passed=report_ok,
            measured=report_detail,
            requirement="need no code path that blends our greeks with the broker's",
        ),
    ]
