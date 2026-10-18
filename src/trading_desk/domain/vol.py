"""Volatility maths. Pure: no clock, no I/O, no database.

**"IV rank" and "IV percentile" are different numbers and this module computes
the percentile.** The distinction matters enough to state up front, because the
two disagree badly and the thresholds in docs/06 (60 / 70 / 35) mean different
things under each:

- *IV rank*, as most retail platforms use it: (current - min) / (max - min).
  One outlier high in the window suppresses every later reading.
- *IV percentile*: the share of observations below the current one. Robust to
  an outlier, and what `docs/10` step 1.8 specifies - "percentile of today's
  ATM IV within the trailing window".

`IvRank.method` records which was used, so a future change cannot silently
re-scale every historical gate decision.

Two refusals are deliberate:

1. **Below `MIN_SAMPLES` no number is returned at all.** A percentile over
   eleven observations is a lie with a decimal point: each observation moves it
   by nine points, so it reads as precision that is not there.
2. **An implausible IV is dropped, not clamped.** Measured on live data in step
   1.1b, a deep-ITM strike with no volume carried a stale `last` that implied
   419% volatility. Clamping it to a ceiling would keep a fabricated number in
   the series; dropping it leaves an honest gap.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from math import log, sqrt
from statistics import fmean, stdev

from trading_desk.domain.market import Bar, OptionChainSnapshot

# Fewer observations than this and no rank is returned. Thirty trading days is
# roughly six weeks - short enough to be reachable, long enough that one day
# moves the percentile by about three points rather than nine.
MIN_SAMPLES = 30

# The trailing window a rank is measured over, in observations.
DEFAULT_WINDOW = 252

# An ATM implied volatility outside this band is not believed. Indian single
# stocks run roughly 15-60% and indices 10-25%; 200% happens on a stale quote,
# not in the market. Measured: 419.45% from a zero-volume deep-ITM strike.
MIN_PLAUSIBLE_IV = 1.0
MAX_PLAUSIBLE_IV = 200.0

TRADING_DAYS_PER_YEAR = 252


class IvSource(StrEnum):
    """Where the number came from. Carried everywhere the value appears."""

    ACTUAL = "ACTUAL"  # our own option-chain history
    PROXY = "PROXY"  # realised volatility, until enough IV history accrues


class RankMethod(StrEnum):
    PERCENTILE = "PERCENTILE"


@dataclass(frozen=True)
class IvRank:
    """A rank, or an honest refusal to produce one.

    `value is None` is not an error - it is the correct answer when there is not
    enough history, and it carries the reason so a briefing can say why rather
    than omitting the line (CLAUDE.md rule 9: gates report what they measured).
    """

    value: float | None
    source: IvSource
    sample_count: int
    window: int
    method: RankMethod = RankMethod.PERCENTILE
    reason: str = ""

    @property
    def is_available(self) -> bool:
        return self.value is not None

    def describe(self) -> str:
        if self.value is None:
            return f"no rank ({self.reason}; {self.sample_count} samples)"
        tag = " [proxy]" if self.source is IvSource.PROXY else ""
        return f"{self.value:.1f}th percentile of {self.sample_count} samples{tag}"


def atm_implied_volatility(snapshot: OptionChainSnapshot) -> float | None:
    """ATM IV for one chain snapshot, or None if neither side is believable.

    The call and put at the money should agree closely; averaging them is
    steadier than either alone and costs nothing. If only one side is usable
    that side is returned rather than nothing - a one-sided ATM reading is a
    real measurement, not a guess.
    """
    strike = snapshot.atm_strike()
    return combine_atm_iv(
        strike.call.implied_volatility if strike.call else None,
        strike.put.implied_volatility if strike.put else None,
    )


def combine_atm_iv(call_iv: float | None, put_iv: float | None) -> float | None:
    """The ATM reading from a call and a put IV, dropping implausible values.

    Separate from `atm_implied_volatility` so the archive reader can apply the
    identical rule to Parquet rows without rebuilding a whole snapshot object.
    The *rule* belongs here; the query belongs in the adapter.
    """
    usable = [value for value in (call_iv, put_iv) if _is_plausible(value)]
    if not usable:
        return None
    return fmean(value for value in usable if value is not None)


def _is_plausible(iv: float | None) -> bool:
    if iv is None:
        return False
    return MIN_PLAUSIBLE_IV <= iv <= MAX_PLAUSIBLE_IV


def percentile_of(value: float, history: Sequence[float]) -> float:
    """What share of `history` sits below `value`, as 0-100.

    Ties count as half. Without that, a flat series where today equals every
    past day reads as either 0 or 100 depending on the comparison operator,
    and both are wrong: the honest answer for "today is exactly typical" is 50.
    """
    if not history:
        raise ValueError("percentile_of needs at least one observation")

    below = sum(1 for item in history if item < value)
    equal = sum(1 for item in history if item == value)
    return 100.0 * (below + 0.5 * equal) / len(history)


def realised_volatility(
    closes: Sequence[Decimal], periods_per_year: int = TRADING_DAYS_PER_YEAR
) -> float | None:
    """Annualised close-to-close volatility, as a percentage.

    The documented proxy for IV rank until six months of real chain history
    accrues (FR-5.6). It is *not* implied volatility and never claims to be -
    realised vol is backward-looking and systematically below implied, which is
    why every value derived from it is tagged PROXY.

    Decimal in, float out: the log returns are float maths by nature, but the
    prices arriving must not have been floats on the way in (rule 4).
    """
    if len(closes) < 2:  # noqa: PLR2004 - two prices make one return
        return None

    returns = []
    for earlier, later in pairwise(closes):
        if earlier <= 0 or later <= 0:
            return None  # a non-positive price is bad data, not a 0% day
        returns.append(log(float(later) / float(earlier)))

    if len(returns) < 2:  # noqa: PLR2004 - stdev needs two
        return None
    return stdev(returns) * sqrt(periods_per_year) * 100.0


def realised_volatility_series(bars: Sequence[Bar], window: int) -> list[tuple[date, float]]:
    """A rolling realised-vol series, one value per day once the window fills.

    Returned as (date, value) pairs rather than a bare list so the caller can
    tell which day a reading belongs to - needed because a gap in the bars must
    not silently shift the series.
    """
    if len(bars) <= window:
        return []

    closes = [bar.close.rupees() for bar in bars]
    series = []
    for index in range(window, len(bars)):
        value = realised_volatility(closes[index - window : index + 1])
        if value is None:
            continue
        series.append((bars[index].start.date(), value))
    return series


def rank_within(
    current: float,
    history: Sequence[float],
    source: IvSource,
    window: int = DEFAULT_WINDOW,
    min_samples: int = MIN_SAMPLES,
) -> IvRank:
    """Rank `current` against the trailing window, or refuse and say why.

    **`history` is the *prior* observations and must not contain `current`.**
    Including today in its own history caps the result below 100: with a
    30-day window a new all-time high scores (29 + 0.5) / 30 = 98.3, and
    "98.3rd percentile" for a reading never seen before is simply wrong. The
    standard reading of IV percentile is "the share of *previous* days that
    were lower", so previous days are what this takes.
    """
    trailing = list(history)[-window:]

    if len(trailing) < min_samples:
        return IvRank(
            value=None,
            source=source,
            sample_count=len(trailing),
            window=window,
            reason=f"insufficient history: {len(trailing)} samples, need {min_samples}",
        )

    return IvRank(
        value=percentile_of(current, trailing),
        source=source,
        sample_count=len(trailing),
        window=window,
    )
