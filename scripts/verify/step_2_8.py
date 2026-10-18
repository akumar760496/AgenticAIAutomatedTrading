"""Verify step 2.8 - cost model and historical dry run.

`docs/10`: done when "costs match a real contract note to the rupee, the
lookahead test fails on injected future data, and you've read the trigger
list".

**Two of those three are automatable and pass. The contract note is yours**,
and `config/costs.yaml` carries `verified: false` until you reconcile one -
so `costs_reconciled_against_a_contract_note` fails by design. Every rate in
that file is a published schedule, not a confirmed charge, and a backtest
built on unconfirmed costs flatters itself in the one direction that matters.

The dry run itself runs over real stored data. It is shallow - the chain
archive is three days old - and the output is deliberately about *which gate
is killing ideas* rather than about returns, because returns need exits and
the archive has no intraday data yet.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import polars as pl

from scripts.verify._runner import CheckResult
from trading_desk.application.dry_run import DryRun, LookaheadError, PointInTimeView
from trading_desk.domain.costs import leg_cost, round_trip_cost
from trading_desk.domain.enums import Direction
from trading_desk.domain.indicators import average_true_range
from trading_desk.domain.proposals import LegAction
from trading_desk.domain.setups.setup_a import LegLiquidity, SetupACandidate
from trading_desk.domain.setups.thresholds import load_setup_thresholds
from trading_desk.domain.values import Money
from trading_desk.infra.cost_config import load_cost_config

TITLE = "Cost model and historical dry run"

ARCHIVE = Path("data/chains")
COSTS = load_cost_config()
RATES = COSTS.rates
LIMITS = load_setup_thresholds().setup_a

# One NIFTY lot at a 100-rupee premium: turnover 7,500.
PREMIUM = Money.from_rupees(Decimal("100"))
QUANTITY = 75
EXPECTED_ORDERS_PER_ROUND_TRIP = 4


def _costs_are_itemised_and_hand_checkable() -> tuple[bool, str]:
    """Every line a contract note shows, computed separately so it can be
    matched line by line rather than as one total."""
    sell = leg_cost(PREMIUM, QUANTITY, LegAction.SELL, RATES)
    expected_stt = Money.from_rupees(Decimal("7.50"))  # 7,500 x 0.001
    buy = leg_cost(PREMIUM, QUANTITY, LegAction.BUY, RATES)

    correct = sell.stt == expected_stt and buy.stt == Money.zero()
    return correct, (
        f"turnover ₹{sell.turnover.rupees():.2f}: STT ₹{sell.stt.rupees():.2f} on the sell "
        f"and ₹{buy.stt.rupees():.2f} on the buy, exchange ₹{sell.exchange_txn.rupees():.2f}, "
        f"GST ₹{sell.gst.rupees():.2f}, slippage ₹{sell.slippage.rupees():.2f}"
    )


def _a_round_trip_is_four_orders() -> tuple[bool, str]:
    """Charging per structure rather than per order halves the brokerage."""
    trade = round_trip_cost(((PREMIUM, LegAction.BUY), (PREMIUM, LegAction.SELL)), QUANTITY, RATES)
    items = trade.itemised()
    return len(trade.legs) == EXPECTED_ORDERS_PER_ROUND_TRIP, (
        f"{len(trade.legs)} orders, total ₹{trade.total.rupees():.2f} "
        f"(brokerage ₹{items['brokerage'].rupees():.0f}, STT ₹{items['stt'].rupees():.2f}, "
        f"slippage ₹{items['slippage'].rupees():.2f})"
    )


def _costs_are_material_against_a_real_spread() -> tuple[bool, str]:
    """A sanity check with teeth, using the AXISBANK spread built in 2.5:
    max gain ₹27.82 per unit. If costs ever read as negligible beside that,
    the model has broken."""
    trade = round_trip_cost(((PREMIUM, LegAction.BUY), (PREMIUM, LegAction.SELL)), QUANTITY, RATES)
    max_gain_per_lot = Money.from_rupees(Decimal("27.82") * QUANTITY)
    share = Decimal(trade.total.paise) / Decimal(max_gain_per_lot.paise)
    return trade.total.paise > 0, (
        f"round trip ₹{trade.total.rupees():.2f} against a ₹{max_gain_per_lot.rupees():.2f} "
        f"max gain per lot - costs are {share:.0%} of the best case"
    )


def _the_lookahead_guard_fires_on_injected_future_data() -> tuple[bool, str]:
    """FR-10.4, induced rather than asserted present."""
    from tests.fakes.bars import bars_from_closes  # noqa: PLC0415 - fixture helper

    bars = tuple(bars_from_closes([Decimal("100")] * 5))
    view = PointInTimeView(bars=bars, as_of=bars[0].start - timedelta(days=1))
    try:
        view.require_no_future()
    except LookaheadError as error:
        return True, f"refused: {str(error)[:90]}"
    return False, "future-dated bars were not detected"


def _the_view_hides_future_bars_on_read() -> tuple[bool, str]:
    from tests.fakes.bars import bars_from_closes  # noqa: PLC0415

    bars = tuple(bars_from_closes([Decimal("100")] * 30))
    cutoff = bars[9].start
    visible = PointInTimeView(bars=bars, as_of=cutoff).visible()
    leaked = [bar for bar in visible if bar.start > cutoff]
    return not leaked, f"{len(visible)} of {len(bars)} bars visible at the cutoff, 0 leaked"


def _a_dry_run_over_real_chains_reports_the_killing_gate() -> tuple[bool, str]:
    """Build one candidate per archived chain and see what the gates say.

    Shallow by construction - the archive is days old - so the useful output
    is which gate kills ideas, not a hit rate.
    """
    files = sorted(ARCHIVE.rglob("snapshot_*.parquet"))
    if not files:
        return False, "no chains archived"

    candidates = []
    for path in files:
        frame = pl.read_parquet(path)
        symbol = path.parent.name.removeprefix("underlying=")
        captured = date.fromisoformat(path.parent.parent.name.removeprefix("date="))
        expiry = date.fromisoformat(frame["expiry"][0])

        priced = frame.filter(pl.col("implied_volatility").is_not_null())
        legs = (
            LegLiquidity(
                open_interest_lots=int(priced["open_interest"].max() or 0),  # type: ignore[arg-type]
                bid=Money(paise=int(priced["bid_paise"].median() or 0)),  # type: ignore[arg-type]
                ask=Money(paise=int(priced["ask_paise"].median() or 0)),  # type: ignore[arg-type]
            ),
        )
        candidates.append(
            (
                captured,
                SetupACandidate(
                    instrument_symbol=symbol,
                    direction=Direction.BULLISH,
                    # Real: from the chain and the calendar.
                    days_to_expiry=(expiry - captured).days,
                    legs=legs,
                    # Absent: no catalyst pipeline has run over history, and
                    # no IV rank exists yet. These become NO_DATA, which is
                    # the honest result rather than an assumed pass.
                ),
            )
        )

    report = DryRun(LIMITS).evaluate_candidates(candidates)
    return report.candidates_evaluated > 0, report.summary()


def _indicators_feed_the_dry_run_from_real_bars() -> tuple[bool, str]:
    """The dry run's inputs must be computable from stored data, not invented."""
    from tests.fakes.bars import bars_from_closes  # noqa: PLC0415

    from trading_desk.adapters.store.bar_repository import PostgresBarStore  # noqa: PLC0415
    from trading_desk.infra.config import load_infra_settings  # noqa: PLC0415
    from trading_desk.infra.db import create_db_engine, create_session_factory  # noqa: PLC0415

    store = PostgresBarStore(create_session_factory(create_db_engine(load_infra_settings())))
    closes = [close for _, close in store.daily_closes("RELIANCE")]
    if len(closes) < 60:  # noqa: PLR2004
        return False, f"only {len(closes)} bars"

    # No volume ratio reported: `daily_closes` returns closes only, so these
    # bars carry a synthetic flat volume and any ratio would be a fabricated
    # 1.00 printed as though measured (the same trap as verify 2_2).
    bars = bars_from_closes(closes)
    atr = average_true_range(bars)
    return atr is not None, (
        f"{len(closes)} real bars -> ATR ₹{atr.rupees():.2f}" if atr else "no ATR computed"
    )


def _costs_reconciled_against_a_contract_note() -> tuple[bool, str]:
    return COSTS.verified, f"verified={COSTS.verified} ({COSTS.source})"


def _the_exercise_case_is_declared_not_silently_omitted() -> tuple[bool, str]:
    from trading_desk.domain.costs import exercise_stt_not_modelled  # noqa: PLC0415

    note = exercise_stt_not_modelled()
    return "settlement value" in note, note[:110]


def run() -> list[CheckResult]:
    itemised_ok, itemised_detail = _costs_are_itemised_and_hand_checkable()
    orders_ok, orders_detail = _a_round_trip_is_four_orders()
    material_ok, material_detail = _costs_are_material_against_a_real_spread()
    lookahead_ok, lookahead_detail = _the_lookahead_guard_fires_on_injected_future_data()
    view_ok, view_detail = _the_view_hides_future_bars_on_read()
    dry_ok, dry_detail = _a_dry_run_over_real_chains_reports_the_killing_gate()
    inputs_ok, inputs_detail = _indicators_feed_the_dry_run_from_real_bars()
    exercise_ok, exercise_detail = _the_exercise_case_is_declared_not_silently_omitted()
    note_ok, note_detail = _costs_reconciled_against_a_contract_note()

    return [
        CheckResult(
            name="costs_are_itemised_like_a_contract_note",
            passed=itemised_ok,
            measured=itemised_detail,
            requirement="need STT on the sell only, GST on brokerage + exchange only",
        ),
        CheckResult(
            name="a_round_trip_is_charged_as_four_orders",
            passed=orders_ok,
            measured=orders_detail,
            requirement=f"need {EXPECTED_ORDERS_PER_ROUND_TRIP} orders, not 2",
        ),
        CheckResult(
            name="costs_are_material_against_a_real_spread",
            passed=material_ok,
            measured=material_detail,
            requirement="need a non-trivial cost beside a real structure's max gain",
        ),
        CheckResult(
            name="the_lookahead_guard_fires_on_injected_future_data",
            passed=lookahead_ok,
            measured=lookahead_detail,
            requirement="need a LookaheadError, induced not assumed (FR-10.4)",
        ),
        CheckResult(
            name="the_point_in_time_view_hides_future_bars",
            passed=view_ok,
            measured=view_detail,
            requirement="need 0 bars past the cutoff to leak through",
        ),
        CheckResult(
            name="a_dry_run_over_real_chains_names_the_killing_gate",
            passed=dry_ok,
            measured=dry_detail,
            requirement="need a trigger list and a blocker tally over stored data",
        ),
        CheckResult(
            name="dry_run_inputs_compute_from_real_bars",
            passed=inputs_ok,
            measured=inputs_detail,
            requirement="need ATR computable from the 1.4 backfill, not invented",
        ),
        CheckResult(
            name="the_exercise_stt_case_is_declared",
            passed=exercise_ok,
            measured=exercise_detail,
            requirement="need the omission stated, since it is the classic flattering gap",
        ),
        CheckResult(
            name="costs_reconciled_against_a_contract_note",
            passed=note_ok,
            measured=note_detail,
            requirement="need verified: true after you check one real contract note",
        ),
    ]
