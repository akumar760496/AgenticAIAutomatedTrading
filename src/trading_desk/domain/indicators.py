"""Indicators computed from bars. Pure: bars in, numbers out, no clock.

Everything here returns `Money` rather than a float, because every value is a
price *difference* and differences get compared against thresholds that decide
whether a trade happens. A float ATR that is 0.005 paise light looks identical to
a correct one right up to the day it sits on the wrong side of a gate.

`Price` deliberately has no subtraction: a price is a quote, and the gap between
two quotes is an amount. The conversion is spelled out here rather than hidden on
the type.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

from trading_desk.domain.enums import TrendRegime
from trading_desk.domain.market import Bar
from trading_desk.domain.values import Money, Price

# Wilder's original period. Every published ATR table assumes it, so a different
# default would silently disagree with every external cross-check.
DEFAULT_ATR_PERIOD = 14

# Twenty sessions is about a calendar month of trading, the usual reference for
# "is today's volume unusual".
DEFAULT_VOLUME_PERIOD = 20

# 20 and 50 sessions: roughly a month against a quarter. Conventional on
# purpose - an unusual pair would need a backtest to justify, and there is not
# one yet (step 2.8).
DEFAULT_FAST_MA = 20
DEFAULT_SLOW_MA = 50

# The window a breakout is measured against. Matches the `range_20d` signal, and
# matches DEFAULT_VOLUME_PERIOD so the two halves of Setup A's technical_confirm
# gate describe the same stretch of history.
DEFAULT_RANGE_SESSIONS = 20


def _gap(first: Price, second: Price) -> Money:
    return Money(abs(first.paise - second.paise))


def true_range(bar: Bar, previous_close: Price | None) -> Money:
    """The greater of today's range and either gap against yesterday's close.

    On the first bar of a series there is no previous close, so the range is just
    high minus low. Substituting the bar's own open instead - a common shortcut -
    makes the first value wrong in a way that then propagates through every
    smoothed value after it.
    """
    intraday = _gap(bar.high, bar.low)
    if previous_close is None:
        return intraday
    return max(intraday, _gap(bar.high, previous_close), _gap(bar.low, previous_close))


def true_ranges(bars: Sequence[Bar]) -> list[Money]:
    """True range for each bar, oldest first."""
    ranges = []
    previous_close: Price | None = None
    for bar in bars:
        ranges.append(true_range(bar, previous_close))
        previous_close = bar.close
    return ranges


def average_true_range(bars: Sequence[Bar], period: int = DEFAULT_ATR_PERIOD) -> Money | None:
    """Wilder-smoothed ATR over `period` bars, or None without enough history.

    **Wilder's smoothing, not a simple moving average.** The first value is the
    plain mean of the first `period` true ranges; after that each value is
    `(previous * (period - 1) + current) / period`. A simple rolling mean is a
    different indicator that happens to be close, and it will not reconcile
    against any published figure.

    Returns None rather than a partial figure: an ATR computed from four bars is
    not a less precise ATR(14), it is a different number, and a gate comparing
    against it would be making a decision nobody authorised.

    The running value is a Decimal and is rounded to paise exactly once, at the
    end. Rounding at every step instead looks harmless and is not: integer
    division truncates, the recursion feeds each truncated value into the next,
    and the errors accumulate into a one-sided bias rather than cancelling.
    Measured over 694 bars it understated ATR by about 5 paise, every time.
    Since the breakout gate asks whether a move exceeds 1.5x ATR, an ATR that is
    reliably a little low is a gate that fires a little too often.
    """
    if period <= 0:
        raise ValueError(f"period must be positive, got {period}")

    ranges = true_ranges(bars)
    if len(ranges) < period:
        return None

    divisor = Decimal(period)
    running = Decimal(sum(item.paise for item in ranges[:period])) / divisor
    for item in ranges[period:]:
        running = (running * (period - 1) + item.paise) / divisor

    return Money(int(running.to_integral_value(ROUND_HALF_UP)))


def price_range(bars: Sequence[Bar]) -> Money | None:
    """Highest high minus lowest low across the bars. None if there are none.

    The 20-day range is this over the last 20 bars; the caller slices, so the
    same function serves any window without a period argument to get wrong.
    """
    if not bars:
        return None
    highest = max(bar.high.paise for bar in bars)
    lowest = min(bar.low.paise for bar in bars)
    return Money(highest - lowest)


def percent_change(earlier: Price, later: Price) -> float:
    """Signed move from `earlier` to `later`, in percent.

    A float is right here, unlike the amounts above: this is a ratio used for
    spotting outliers, never for sizing a position or computing a P&L.
    """
    if earlier.paise == 0:
        raise ValueError("cannot compute a percentage change from a zero price")
    return (later.paise - earlier.paise) / earlier.paise * 100


def simple_moving_average(bars: Sequence[Bar], period: int) -> Money | None:
    """Mean close over the last `period` bars, or None if there are too few.

    None rather than a mean of whatever is available: a "20-day average" over
    six days is a different number that a gate would act on without knowing.
    """
    if period <= 0:
        raise ValueError(f"period must be positive, got {period}")
    if len(bars) < period:
        return None

    total = sum((Decimal(bar.close.paise) for bar in bars[-period:]), Decimal(0))
    return Money(paise=int((total / period).to_integral_value(ROUND_HALF_UP)))


def volume_ratio(bars: Sequence[Bar], period: int = DEFAULT_VOLUME_PERIOD) -> float | None:
    """Latest bar's volume against the average of the `period` bars before it.

    The window deliberately **excludes** the latest bar. Including it damps the
    very spike the ratio exists to detect: on a 20-day window a 10x day reads
    as 6.9x instead of 10x, which is the difference between tripping a
    threshold and not.

    Returns None when the baseline is zero - a ratio against no volume is not
    "infinitely unusual", it is unmeasurable, and a gate must be told that
    rather than handed an infinity.
    """
    if period <= 0:
        raise ValueError(f"period must be positive, got {period}")
    if len(bars) < period + 1:
        return None

    baseline = bars[-(period + 1) : -1]
    total = sum(bar.volume for bar in baseline)
    if total <= 0:
        return None

    average = Decimal(total) / period
    return float(Decimal(bars[-1].volume) / average)


def closed_beyond_range(bars: Sequence[Bar], sessions: int = DEFAULT_RANGE_SESSIONS) -> bool | None:
    """Did the latest bar close outside the range of the ones before it?

    The prior bars exclude today's, which is the whole point: a close compared
    against a range that contains it can never be outside it, and the gate
    would quietly never fire.

    Lived in `agents/technical/agent.py` until 2026-10-20. It moved here so the
    backtest in `scripts/backtest_technical.py` measures the same function
    production uses - a backtest with its own copy of the rule is measuring a
    rule nobody trades.
    """
    if sessions <= 0:
        raise ValueError(f"sessions must be positive, got {sessions}")
    if len(bars) < sessions + 1:
        return None

    prior = bars[-(sessions + 1) : -1]
    highest = max(bar.high.paise for bar in prior)
    lowest = min(bar.low.paise for bar in prior)
    close = bars[-1].close.paise
    return close > highest or close < lowest


def trend_regime(
    bars: Sequence[Bar],
    fast_period: int = DEFAULT_FAST_MA,
    slow_period: int = DEFAULT_SLOW_MA,
) -> TrendRegime:
    """Classify the price structure from two moving averages.

    The rule is deliberately the boring one: price above a rising stack is an
    uptrend, below a falling stack is a downtrend, anything else is ranging.
    It will misclassify at turns. That is accepted - this feeds a *confirming*
    gate, not an entry trigger, and a cleverer classifier would be one more
    thing to be wrong about with no backtest to justify it.

    INSUFFICIENT_HISTORY is a distinct answer from RANGING. Collapsing them
    would let a newly listed stock read as "no trend" when the truth is "we
    cannot say".
    """
    fast = simple_moving_average(bars, fast_period)
    slow = simple_moving_average(bars, slow_period)
    if fast is None or slow is None:
        return TrendRegime.INSUFFICIENT_HISTORY

    close = bars[-1].close.paise
    if close > fast.paise > slow.paise:
        return TrendRegime.UPTREND
    if close < fast.paise < slow.paise:
        return TrendRegime.DOWNTREND
    return TrendRegime.RANGING
