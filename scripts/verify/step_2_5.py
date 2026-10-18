"""Verify step 2.5 - options structure builder.

`docs/10`: done when "fixture chains produce the leg plans the spec describes,
and a deliberately thin chain is rejected". Both here, plus the arithmetic
property that makes a vertical a vertical.

**Run against a real captured chain as well as fixtures.** A builder can
satisfy a hand-built chain and still be wrong about the shape of real data -
real chains have missing quotes, uneven strike spacing and deltas that do not
line up neatly with a band.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import polars as pl

from scripts.verify._runner import CheckResult
from trading_desk.domain.enums import Direction, Exchange, OptionRight, Segment
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.market import OptionChainSnapshot, OptionQuote, OptionStrike
from trading_desk.domain.options.structure import (
    StructureError,
    build_debit_vertical,
)
from trading_desk.domain.setups.thresholds import load_setup_thresholds
from trading_desk.domain.values import Money, Price

TITLE = "Options structure builder"

LIMITS = load_setup_thresholds().setup_a
ARCHIVE = Path("data/chains")
NOW = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)

NIFTY = InstrumentRef(
    symbol="NIFTY", exchange=Exchange.NSE, segment=Segment.FNO, security_id="26000", lot_size=75
)


def _quote(right: OptionRight, mid: str, delta: float) -> OptionQuote:
    centre = Decimal(mid)
    return OptionQuote(
        right=right,
        last=Price.from_rupees(centre),
        bid=Price.from_rupees(centre - Decimal("0.5")),
        ask=Price.from_rupees(centre + Decimal("0.5")),
        volume=1000,
        open_interest=5000,
        implied_volatility=18.0,
        delta=delta,
    )


def _fixture_chain() -> OptionChainSnapshot:
    rows = [
        ("23400", "520", 0.80, "40", -0.20),
        ("23600", "420", 0.72, "60", -0.28),
        ("23800", "330", 0.62, "95", -0.38),
        ("24000", "240", 0.52, "150", -0.48),
        ("24200", "165", 0.46, "230", -0.54),
        ("24400", "105", 0.30, "340", -0.70),
        ("24600", "62", 0.22, "480", -0.78),
    ]
    return OptionChainSnapshot(
        underlying=NIFTY,
        expiry_date="2026-11-26",
        strikes=tuple(
            OptionStrike(
                strike=Price.from_rupees(Decimal(strike)),
                call=_quote(OptionRight.CALL, call_mid, call_delta),
                put=_quote(OptionRight.PUT, put_mid, put_delta),
            )
            for strike, call_mid, call_delta, put_mid, put_delta in rows
        ),
        spot=Price.from_rupees(Decimal("24000")),
        exchange_time=NOW,
        received_at=NOW,
    )


def _real_chain(path: Path) -> OptionChainSnapshot:
    """Rebuild a snapshot from a captured Parquet file."""
    frame = pl.read_parquet(path)
    strikes = []
    for strike_paise in sorted(set(frame["strike_paise"].to_list())):
        rows = frame.filter(pl.col("strike_paise") == strike_paise)
        sides: dict[str, OptionQuote] = {}
        for row in rows.iter_rows(named=True):
            sides[row["right"]] = OptionQuote(
                right=OptionRight(row["right"]),
                last=Price(paise=row["last_paise"]),
                bid=Price(paise=row["bid_paise"]),
                ask=Price(paise=row["ask_paise"]),
                volume=row["volume"],
                open_interest=row["open_interest"],
                implied_volatility=row["implied_volatility"],
                delta=row["delta"],
            )
        strikes.append(
            OptionStrike(
                strike=Price(paise=strike_paise),
                call=sides.get("CALL"),
                put=sides.get("PUT"),
            )
        )

    first = frame.row(0, named=True)
    underlying = InstrumentRef(
        symbol=path.parent.name.removeprefix("underlying="),
        exchange=Exchange.NSE,
        segment=Segment.FNO,
        security_id="0",
        lot_size=1,
    )
    return OptionChainSnapshot(
        underlying=underlying,
        expiry_date=first["expiry"],
        strikes=tuple(strikes),
        spot=Price(paise=first["spot_paise"]),
        exchange_time=first["exchange_time"],
        received_at=first["received_at"],
    )


def _the_fixture_produces_the_documented_leg_plan() -> tuple[bool, str]:
    spread = build_debit_vertical(
        _fixture_chain(), Direction.BULLISH, LIMITS.long_leg_delta, LIMITS.short_leg_delta
    )
    long_in_band = LIMITS.long_leg_delta.contains(spread.long_leg.delta or 0)
    short_in_band = LIMITS.short_leg_delta.contains(spread.short_leg.delta or 0)
    return long_in_band and short_in_band, (
        f"long delta {spread.long_leg.delta} in "
        f"{LIMITS.long_leg_delta.minimum}-{LIMITS.long_leg_delta.maximum}, "
        f"short delta {spread.short_leg.delta} in "
        f"{LIMITS.short_leg_delta.minimum}-{LIMITS.short_leg_delta.maximum}"
    )


def _the_economics_are_hand_checkable() -> tuple[bool, str]:
    """Long 24000 call at 240, short 24400 at 105 -> debit 135, width 400."""
    spread = build_debit_vertical(
        _fixture_chain(), Direction.BULLISH, LIMITS.long_leg_delta, LIMITS.short_leg_delta
    )
    correct = (
        spread.net_debit == Money.from_rupees(Decimal("135"))
        and spread.max_gain == Money.from_rupees(Decimal("265"))
        and spread.breakeven == Price.from_rupees(Decimal("24135"))
    )
    return correct, (
        f"debit ₹{spread.net_debit.rupees()}, max gain ₹{spread.max_gain.rupees()}, "
        f"breakeven ₹{spread.breakeven.rupees()} (hand-computed 135 / 265 / 24135)"
    )


def _max_loss_plus_max_gain_equals_the_width() -> tuple[bool, str]:
    """The defining property of a vertical. If it fails, the payoff the
    briefing shows is not the payoff the position has."""
    spread = build_debit_vertical(
        _fixture_chain(), Direction.BULLISH, LIMITS.long_leg_delta, LIMITS.short_leg_delta
    )
    total = spread.max_loss.paise + spread.max_gain.paise
    return total == spread.width.paise, (
        f"max loss ₹{spread.max_loss.rupees()} + max gain ₹{spread.max_gain.rupees()} "
        f"= width ₹{spread.width.rupees()}"
    )


def _a_thin_chain_is_rejected() -> tuple[bool, str]:
    single = OptionChainSnapshot(
        underlying=NIFTY,
        expiry_date="2026-11-26",
        strikes=(
            OptionStrike(
                strike=Price.from_rupees(Decimal("24000")),
                call=_quote(OptionRight.CALL, "240", 0.52),
                put=None,
            ),
        ),
        spot=Price.from_rupees(Decimal("24000")),
        exchange_time=NOW,
        received_at=NOW,
    )
    try:
        build_debit_vertical(
            single, Direction.BULLISH, LIMITS.long_leg_delta, LIMITS.short_leg_delta
        )
    except StructureError as error:
        return True, f"refused: {str(error)[:80]}"
    return False, "a one-strike chain produced a spread"


def _band_coverage() -> list[tuple[str, int, int, int]]:
    """Per archived chain: (symbol, DTE, strikes in the long band, in the short)."""
    rows = []
    for path in sorted(ARCHIVE.rglob("snapshot_*.parquet")):
        frame = pl.read_parquet(path).filter(pl.col("right") == "CALL")
        if frame.is_empty():
            continue
        captured = date.fromisoformat(path.parent.parent.name.removeprefix("date="))
        expiry = date.fromisoformat(frame["expiry"][0])
        with_delta = frame.filter(pl.col("delta").is_not_null())
        long_n = with_delta.filter(
            (pl.col("delta").abs() >= LIMITS.long_leg_delta.minimum)
            & (pl.col("delta").abs() <= LIMITS.long_leg_delta.maximum)
        ).height
        short_n = with_delta.filter(
            (pl.col("delta").abs() >= LIMITS.short_leg_delta.minimum)
            & (pl.col("delta").abs() <= LIMITS.short_leg_delta.maximum)
        ).height
        rows.append(
            (
                path.parent.name.removeprefix("underlying="),
                (expiry - captured).days,
                long_n,
                short_n,
            )
        )
    return rows


def _in_window(dte: int) -> bool:
    return LIMITS.expiry.min_days <= dte <= LIMITS.expiry.max_days


def _it_works_on_a_real_captured_chain() -> tuple[bool, str]:
    """Build from the first archived chain offering both delta bands.

    "First that works" rather than "the newest" on purpose: the newest NIFTY
    capture is a 3-DTE weekly, which Setup A's 10-25 DTE window rejects
    anyway, so failing on it would test the builder against an expiry the
    rulebook never hands it.
    """
    usable = [
        symbol
        for symbol, dte, long_n, short_n in _band_coverage()
        if long_n and short_n and _in_window(dte)
    ]
    if not usable:
        return False, "no archived in-window chain has a strike in both delta bands"

    path = next(ARCHIVE.rglob(f"underlying={usable[0]}/snapshot_*.parquet"))
    chain = _real_chain(path)
    try:
        spread = build_debit_vertical(
            chain, Direction.BULLISH, LIMITS.long_leg_delta, LIMITS.short_leg_delta
        )
    except StructureError as error:
        return False, f"{usable[0]} ({len(chain.strikes)} strikes): {str(error)[:90]}"

    consistent = spread.max_loss.paise + spread.max_gain.paise == spread.width.paise
    return consistent, f"{usable[0]}, {len(chain.strikes)} real strikes -> {spread.describe()}"


def _the_delta_bands_are_satisfiable_across_the_universe() -> tuple[bool, str]:
    """**A real constraint surfaced now rather than at step 2.8.**

    docs/10's Phase 2 exit: "If the dry run shows the setups almost never
    fire, fix that now - it's a threshold problem, and discovering it after
    the agents are built means rebuilding them." Real chains are already
    archived, so the discovery is available today.
    """
    coverage = [row for row in _band_coverage() if _in_window(row[1])]
    if not coverage:
        return False, "no archived chain falls inside Setup A's expiry window"

    both = [symbol for symbol, _, long_n, short_n in coverage if long_n and short_n]
    missing = [symbol for symbol, _, long_n, short_n in coverage if not (long_n and short_n)]
    return len(both) == len(coverage), (
        f"{len(both)} of {len(coverage)} in-window chains offer BOTH bands "
        f"({LIMITS.long_leg_delta.minimum}-{LIMITS.long_leg_delta.maximum} long, "
        f"{LIMITS.short_leg_delta.minimum}-{LIMITS.short_leg_delta.maximum} short); "
        f"no structure buildable for {sorted(missing)}"
    )


def _the_structure_carries_no_quantity() -> tuple[bool, str]:
    """Sizing is the risk gate's job in 2.7, and nothing here holds a lot count."""
    spread = build_debit_vertical(
        _fixture_chain(), Direction.BULLISH, LIMITS.long_leg_delta, LIMITS.short_leg_delta
    )
    banned = ("quantity", "size", "lots", "lot_count")
    offenders = [
        name for name in spread.__dataclass_fields__ if any(term in name.lower() for term in banned)
    ]
    leg_offenders = [
        name
        for name in spread.long_leg.__dataclass_fields__
        if any(term in name.lower() for term in banned)
    ]
    return not offenders and not leg_offenders, (
        "the spread and its legs carry economics, never a quantity"
    )


def run() -> list[CheckResult]:
    plan_ok, plan_detail = _the_fixture_produces_the_documented_leg_plan()
    econ_ok, econ_detail = _the_economics_are_hand_checkable()
    width_ok, width_detail = _max_loss_plus_max_gain_equals_the_width()
    thin_ok, thin_detail = _a_thin_chain_is_rejected()
    real_ok, real_detail = _it_works_on_a_real_captured_chain()
    coverage_ok, coverage_detail = _the_delta_bands_are_satisfiable_across_the_universe()
    size_ok, size_detail = _the_structure_carries_no_quantity()

    return [
        CheckResult(
            name="the_fixture_chain_produces_the_documented_leg_plan",
            passed=plan_ok,
            measured=plan_detail,
            requirement="need the long leg at delta 0.50-0.55 and the short at 0.25-0.30",
        ),
        CheckResult(
            name="the_economics_are_hand_checkable",
            passed=econ_ok,
            measured=econ_detail,
            requirement="need debit 135, max gain 265, breakeven 24135 exactly",
        ),
        CheckResult(
            name="max_loss_plus_max_gain_equals_the_width",
            passed=width_ok,
            measured=width_detail,
            requirement="need the defining property of a vertical to hold",
        ),
        CheckResult(
            name="a_thin_chain_is_rejected_not_approximated",
            passed=thin_ok,
            measured=thin_detail,
            requirement="need a StructureError, never a guessed structure",
        ),
        CheckResult(
            name="it_builds_from_a_real_captured_chain",
            passed=real_ok,
            measured=real_detail,
            requirement="need a consistent spread from live archived data, not only fixtures",
        ),
        CheckResult(
            name="the_delta_bands_are_satisfiable_across_the_universe",
            passed=coverage_ok,
            measured=coverage_detail,
            requirement="need every in-window chain to offer both bands, else Setup A "
            "cannot build for most of the universe",
        ),
        CheckResult(
            name="the_structure_carries_no_quantity",
            passed=size_ok,
            measured=size_detail,
            requirement="need no size field - sizing is the risk gate's job (rule 1)",
        ),
    ]
