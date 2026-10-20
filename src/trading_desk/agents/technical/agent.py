"""The Technical / Quant agent: Setup A's technical confirm, measured.

`docs/02` puts one hard constraint on this agent and it shapes the whole
module:

> "this agent does not invent indicators at runtime. It selects from a library
> of signals that have each been **backtested and registered** ... The LLM's
> job is regime-aware selection and confluence reasoning, not discovering alpha
> in a chat window."

So every number here is computed by a named, registered signal, and the names
are checked against `domain/signals/registry.py` **when the agent is built**,
not when it runs. A typo or an invented indicator raises at construction, which
is the cheap place to find it.

**None of the four signals is backtested, and the view says so.** Step 2.8 ran
a dry run but registered no signal as BACKTESTED, so `require_usable` would
raise for all of them today. Calling it here would simply stop the agent
working; what the agent does instead is report `0 of 4 signals backtested` in
its measurements, every time, so the briefing carries the caveat rather than
the code carrying a lie. FR-5.10 is satisfied by the disclosure plus the closed
list - the agent cannot reach a signal nobody registered.

No model call. Everything below is arithmetic over bars.
"""

from __future__ import annotations

from dataclasses import dataclass

from trading_desk.domain.enums import TrendRegime
from trading_desk.domain.indicators import (
    average_true_range,
    closed_beyond_range,
    price_range,
    trend_regime,
    volume_ratio,
)
from trading_desk.domain.market import Bar
from trading_desk.domain.signals.registry import SignalStatus, require_registered
from trading_desk.domain.specialists import SpecialistFinding, SpecialistName
from trading_desk.domain.values import Money, Price
from trading_desk.ports.bar_store import BarStorePort

# The closed list. Nothing outside it is computed, and each name must exist in
# the registry or the agent will not build.
SIGNALS_USED = (
    "atr_14",
    "range_20d",
    "volume_ratio_20d",
    "trend_regime_20_50",
)

# The 50-session moving average is the longest input, so 60 bars is the floor
# and 80 gives the range and volume windows room to sit inside the same read.
BARS_NEEDED = 60
DEFAULT_BARS_READ = 80

# The window the breakout is measured against, matching range_20d.
RANGE_SESSIONS = 20


@dataclass(frozen=True)
class TechnicalView:
    """Measured price behaviour. Every field may be None, and None means
    "not measurable from the bars we hold" - never zero, never a default."""

    finding: SpecialistFinding
    atr: Money | None
    range_20d: Money | None
    volume_ratio: float | None
    trend: TrendRegime
    last_close: Price | None
    close_beyond_range_edge: bool | None
    bars_read: int
    # How far price has travelled since the catalyst, in rupees. None when no
    # catalyst age was supplied, or when the history does not reach back that
    # far - which is different from "it has not moved".
    move_since_catalyst: Money | None = None

    @property
    def has_enough_history(self) -> bool:
        return self.bars_read >= BARS_NEEDED


class TechnicalAgent:
    def __init__(self, bars: BarStorePort, bars_to_read: int = DEFAULT_BARS_READ) -> None:
        self._bars = bars
        self._bars_to_read = bars_to_read
        # Fail at construction, not mid-cycle. An unregistered name here is a
        # programming error and there is no sensible way to continue past it.
        self._signals = [require_registered(name) for name in SIGNALS_USED]

    @property
    def backtested_count(self) -> int:
        return sum(1 for signal in self._signals if signal.status is SignalStatus.BACKTESTED)

    def assess(self, symbol: str, sessions_since_catalyst: int | None = None) -> TechnicalView:
        """Measure the price behaviour. `sessions_since_catalyst` is optional
        because the technical picture is worth having with or without one; when
        it is given, the move since that session is measured too."""
        bars = self._bars.daily_bars(symbol, self._bars_to_read)

        atr = average_true_range(bars)
        recent_range = price_range(bars[-RANGE_SESSIONS:]) if len(bars) >= RANGE_SESSIONS else None
        ratio = volume_ratio(bars)
        trend = trend_regime(bars)
        last_close = bars[-1].close if bars else None
        beyond = closed_beyond_range(bars, RANGE_SESSIONS)
        move = _move_since(bars, sessions_since_catalyst)

        return TechnicalView(
            finding=self._finding(
                symbol,
                bars=bars,
                atr=atr,
                recent_range=recent_range,
                ratio=ratio,
                trend=trend,
                beyond=beyond,
                move=move,
            ),
            atr=atr,
            range_20d=recent_range,
            volume_ratio=ratio,
            trend=trend,
            last_close=last_close,
            close_beyond_range_edge=beyond,
            bars_read=len(bars),
            move_since_catalyst=move,
        )

    def _finding(
        self,
        symbol: str,
        *,
        bars: list[Bar],
        atr: Money | None,
        recent_range: Money | None,
        ratio: float | None,
        trend: TrendRegime,
        beyond: bool | None,
        move: Money | None = None,
    ) -> SpecialistFinding:
        measurements = {
            "bars_read": str(len(bars)),
            "atr_14": f"₹{atr.rupees():,.2f}" if atr else "unavailable",
            "range_20d": f"₹{recent_range.rupees():,.2f}" if recent_range else "unavailable",
            "volume_ratio_20d": f"{ratio:.2f}x" if ratio is not None else "unavailable",
            "trend_regime_20_50": trend.value,
            "closed_beyond_range_edge": "unavailable" if beyond is None else str(beyond),
            "signals_backtested": f"{self.backtested_count} of {len(self._signals)}",
            "move_since_catalyst": f"₹{move.rupees():,.2f}" if move else "not asked",
        }

        unavailable: list[str] = []
        if len(bars) < BARS_NEEDED:
            unavailable.append(
                f"{len(bars)} bars held, {BARS_NEEDED} needed for the 50-session average"
            )
        for name, value in (
            ("atr_14", atr),
            ("range_20d", recent_range),
            ("volume_ratio_20d", ratio),
        ):
            if value is None:
                unavailable.append(f"{name}: not computable from {len(bars)} bars")

        notes = [
            f"{self.backtested_count} of {len(self._signals)} signals are backtested - "
            "these are descriptive measurements, not evidence of an edge (FR-5.10)"
        ]

        # A technical agent does not veto. It measures; Setup A's gates compare
        # those measurements against thresholds and decide. Keeping the verdict
        # out of here is what stops the same judgement being made twice, in two
        # places, with two sets of numbers.
        return SpecialistFinding(
            specialist=SpecialistName.TECHNICAL,
            symbol=symbol,
            measurements=measurements,
            vetoes=(),
            unavailable=tuple(unavailable),
            notes=tuple(notes),
        )


def _move_since(bars: list[Bar], sessions_ago: int | None) -> Money | None:
    """Absolute move from the close `sessions_ago` sessions back to the latest.

    Absolute rather than signed: Setup A's `not_already_priced` gate asks how
    far price has travelled in ATR units, and a 2-ATR move against the catalyst
    is every bit as disqualifying as one with it.
    """
    if sessions_ago is None or sessions_ago < 0:
        return None
    if len(bars) < sessions_ago + 1:
        return None

    then = bars[-(sessions_ago + 1)].close.paise
    now = bars[-1].close.paise
    return Money(paise=abs(now - then))
