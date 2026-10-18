"""Verify step 2.2 - indicator and signal library.

`docs/10` asks for pure functions, golden-file tests against hand-computed
values, and a registry so only pre-backtested signals can be named (FR-5.10).

The honest headline: **no signal is usable yet.** Step 2.8 runs the historical
dry run, and until it does nothing has out-of-sample evidence, so
`require_usable` raises for every entry. The registry therefore works as a
vocabulary today and becomes a gate at 2.8. A check here fails if any entry
claims a backtest before that happens, which is the mistake worth guarding
against - flipping a flag is easy and silent.

The indicator checks are cross-checked against **real backfilled bars**, not
only fixtures: a function can satisfy a hand-built series and still be wrong
about the shape of real data.
"""

from __future__ import annotations

from decimal import Decimal

from scripts.verify._runner import CheckResult
from trading_desk.adapters.store.bar_repository import PostgresBarStore
from trading_desk.domain.enums import TrendRegime
from trading_desk.domain.indicators import (
    simple_moving_average,
    trend_regime,
    volume_ratio,
)
from trading_desk.domain.signals.registry import (
    REGISTRY,
    SignalStatus,
    UnbackedSignalError,
    UnknownSignalError,
    backtested_signals,
    require_registered,
    require_usable,
)
from trading_desk.domain.values import Money
from trading_desk.infra.config import load_infra_settings
from trading_desk.infra.db import create_db_engine, create_session_factory

TITLE = "Indicator and signal library"

SYMBOL = "RELIANCE"

# Hand-computed: (100 + 102 + 104 + 106 + 108) / 5 = 104
SMA_CLOSES = [Decimal(value) for value in ("100", "102", "104", "106", "108")]
SMA_EXPECTED = Money.from_rupees(Decimal("104"))

# Baseline four days at 1,000 then a 10,000 day -> 10.0x, not 3.57x
SPIKE_VOLUMES = [1000, 1000, 1000, 1000, 10_000]
SPIKE_EXPECTED = 10.0

PLAUSIBLE_MIN_RATIO = 0.05
PLAUSIBLE_MAX_RATIO = 50.0


def _closes() -> list[Decimal]:
    engine = create_db_engine(load_infra_settings())
    store = PostgresBarStore(create_session_factory(engine))
    return [close for _, close in store.daily_closes(SYMBOL)]


def _a_hand_computed_average_matches() -> tuple[bool, str]:
    from tests.fakes.bars import bars_from_closes  # noqa: PLC0415 - test helper, verify only

    bars = bars_from_closes(SMA_CLOSES)
    measured = simple_moving_average(bars, 5)
    return measured == SMA_EXPECTED, (
        f"SMA(5) of {[str(value) for value in SMA_CLOSES]} = "
        f"{measured.rupees() if measured else None}, hand-computed 104"
    )


def _the_volume_window_excludes_the_latest_bar() -> tuple[bool, str]:
    """The bug worth a dedicated check: including today damps the spike."""
    from tests.fakes.bars import bars_from_closes  # noqa: PLC0415

    bars = bars_from_closes([Decimal("100")] * 5, volumes=SPIKE_VOLUMES)
    measured = volume_ratio(bars, period=4)
    return measured == SPIKE_EXPECTED, (
        f"a 10,000 day against four 1,000 days reads {measured}x "
        f"(3.57x if the latest bar were included in its own baseline)"
    )


def _indicators_behave_on_real_bars() -> tuple[bool, str]:
    """Fixtures prove the arithmetic; real data proves the shape."""
    from tests.fakes.bars import bars_from_closes  # noqa: PLC0415

    closes = _closes()
    if len(closes) < 60:  # noqa: PLR2004 - the slow MA needs 50 plus headroom
        return False, f"only {len(closes)} bars for {SYMBOL}"

    bars = bars_from_closes(closes)
    sma20 = simple_moving_average(bars, 20)
    regime = trend_regime(bars)
    # No volume ratio here on purpose: `daily_closes` returns closes only, so
    # the bars carry a synthetic flat volume and any ratio from them would be
    # a fabricated 1.0 printed as though it were measured.

    if sma20 is None:
        return False, "SMA(20) returned None on real bars"

    latest = closes[-1]
    # The average of the last 20 closes must sit inside their own range.
    window = closes[-20:]
    inside = min(window) <= sma20.rupees() <= max(window)
    return inside, (
        f"{len(closes)} real bars: last close ₹{latest}, SMA(20) ₹{sma20.rupees():.2f} "
        f"(window ₹{min(window)}-₹{max(window)}), regime {regime}"
    )


def _insufficient_history_is_not_ranging() -> tuple[bool, str]:
    """ "We cannot say" and "no trend" are different answers."""
    from tests.fakes.bars import bars_from_closes  # noqa: PLC0415

    short = trend_regime(bars_from_closes([Decimal("100")] * 30))
    return short is TrendRegime.INSUFFICIENT_HISTORY, f"30 bars classify as {short}"


def _an_unregistered_signal_is_refused() -> tuple[bool, str]:
    try:
        require_registered("9_day_momentum_crossover")
    except UnknownSignalError as error:
        names_alternatives = "atr_14" in str(error)
        return names_alternatives, f"refused, and listed the {len(REGISTRY)} valid names"
    return False, "an invented signal name was accepted"


def _nothing_claims_a_backtest_before_step_2_8() -> tuple[bool, str]:
    claiming = [name for name, item in REGISTRY.items() if item.status is SignalStatus.BACKTESTED]
    return not claiming, (
        f"{len(REGISTRY)} signals registered, {len(backtested_signals())} backtested "
        "(step 2.8 has not run)"
        if not claiming
        else f"claims evidence it does not have: {claiming}"
    )


def _using_an_unbacked_signal_raises() -> tuple[bool, str]:
    """FR-5.10 enforced, not merely documented."""
    try:
        require_usable("atr_14")
    except UnbackedSignalError as error:
        return "IMPLEMENTED" in str(error), "require_usable refuses an unbacktested signal"
    return False, "an unbacktested signal was accepted for agent use"


def _every_entry_states_its_evidence() -> tuple[bool, str]:
    incomplete = [
        name
        for name, item in REGISTRY.items()
        if not item.evidence.strip() or not item.computed_by.strip()
    ]
    return not incomplete, (
        f"{len(REGISTRY)} entries, each naming where it is computed and what is known"
        if not incomplete
        else f"incomplete: {incomplete}"
    )


def run() -> list[CheckResult]:
    sma_ok, sma_detail = _a_hand_computed_average_matches()
    window_ok, window_detail = _the_volume_window_excludes_the_latest_bar()
    real_ok, real_detail = _indicators_behave_on_real_bars()
    short_ok, short_detail = _insufficient_history_is_not_ranging()
    unknown_ok, unknown_detail = _an_unregistered_signal_is_refused()
    claim_ok, claim_detail = _nothing_claims_a_backtest_before_step_2_8()
    usable_ok, usable_detail = _using_an_unbacked_signal_raises()
    evidence_ok, evidence_detail = _every_entry_states_its_evidence()

    return [
        CheckResult(
            name="a_hand_computed_average_matches",
            passed=sma_ok,
            measured=sma_detail,
            requirement="need SMA(5) == 104 exactly, worked out by hand",
        ),
        CheckResult(
            name="the_volume_window_excludes_the_latest_bar",
            passed=window_ok,
            measured=window_detail,
            requirement="need 10.0x - including today would damp it to 3.57x",
        ),
        CheckResult(
            name="indicators_behave_on_real_backfilled_bars",
            passed=real_ok,
            measured=real_detail,
            requirement="need SMA(20) inside its own window on live data",
        ),
        CheckResult(
            name="insufficient_history_is_distinct_from_ranging",
            passed=short_ok,
            measured=short_detail,
            requirement="need INSUFFICIENT_HISTORY, not RANGING",
        ),
        CheckResult(
            name="an_invented_signal_name_is_refused",
            passed=unknown_ok,
            measured=unknown_detail,
            requirement="need a loud error naming the valid signals (FR-5.10)",
        ),
        CheckResult(
            name="no_signal_claims_a_backtest_before_2_8",
            passed=claim_ok,
            measured=claim_detail,
            requirement="need 0 BACKTESTED until the dry run produces evidence",
        ),
        CheckResult(
            name="an_unbacktested_signal_cannot_be_used_by_an_agent",
            passed=usable_ok,
            measured=usable_detail,
            requirement="need require_usable to raise - FR-5.10 enforced, not documented",
        ),
        CheckResult(
            name="every_registry_entry_states_its_evidence",
            passed=evidence_ok,
            measured=evidence_detail,
            requirement="need evidence and computed_by on every entry",
        ),
    ]
