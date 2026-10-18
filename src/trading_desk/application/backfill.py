"""Loads historical daily bars and reports on what it loaded.

Two jobs, and the second is the one that matters. Pulling bars is easy; knowing
whether the result is trustworthy is not, and a backfill nobody validated is a
backtest nobody should believe.

**Nothing is adjusted.** A 20% overnight move is flagged, never corrected. It is
usually a split or a bonus, and Dhan's history may or may not already be adjusted
for it - silently applying a second adjustment would turn a visible anomaly into
an invisible one. The report names the day and leaves the decision to a human.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from itertools import pairwise

from trading_desk.domain.indicators import (
    DEFAULT_ATR_PERIOD,
    average_true_range,
    percent_change,
    price_range,
)
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.market import Bar
from trading_desk.domain.timezones import to_ist
from trading_desk.domain.values import Money
from trading_desk.ports.bar_store import BarStorePort
from trading_desk.ports.history import HistoricalBarsPort
from trading_desk.ports.notifier import AlertLevel, NotifierPort

# A close-to-close move beyond this is almost always a corporate action rather
# than a market move. Flagged for a human, never adjusted away.
UNEXPLAINED_MOVE_PERCENT = 20.0

# The 20-day range the setups use. Named rather than inlined so the window the
# gates read and the window the report checks cannot drift apart.
RANGE_WINDOW_DAYS = 20


@dataclass(frozen=True)
class SuspectMove:
    """A day whose move is large enough to need a human explanation."""

    symbol: str
    on_date: date
    percent: float

    def describe(self) -> str:
        return f"{self.symbol} {self.on_date}: {self.percent:+.1f}%"


@dataclass(frozen=True)
class InstrumentBackfill:
    """What was loaded for one instrument, and whether it is usable."""

    symbol: str
    bars_fetched: int
    bars_inserted: int
    first_date: date | None
    last_date: date | None
    atr: Money | None
    range_20d: Money | None
    suspect_moves: tuple[SuspectMove, ...]
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None

    @property
    def indicators_ready(self) -> bool:
        """Whether this instrument can feed the gates. The real done-when of 1.4."""
        return self.atr is not None and self.range_20d is not None


@dataclass
class BackfillReport:
    """Every instrument's outcome, in the shape the validation report prints."""

    results: list[InstrumentBackfill] = field(default_factory=list)

    @property
    def failed(self) -> list[InstrumentBackfill]:
        return [item for item in self.results if not item.succeeded]

    @property
    def not_ready(self) -> list[InstrumentBackfill]:
        return [item for item in self.results if item.succeeded and not item.indicators_ready]

    @property
    def suspect_moves(self) -> list[SuspectMove]:
        return [move for item in self.results for move in item.suspect_moves]

    @property
    def bars_inserted(self) -> int:
        return sum(item.bars_inserted for item in self.results)

    @property
    def is_clean(self) -> bool:
        """Clean means loadable and usable. Suspect moves do not make it dirty -
        they need explaining, which is a human step, not a failure."""
        return not self.failed and not self.not_ready

    def summary(self) -> str:
        return (
            f"{len(self.results) - len(self.failed)}/{len(self.results)} instruments, "
            f"{self.bars_inserted} bars inserted, "
            f"{len(self.not_ready)} without indicators, "
            f"{len(self.suspect_moves)} moves to explain"
        )


class BackfillService:
    """Pulls daily history for the universe and validates it."""

    def __init__(
        self,
        history: HistoricalBarsPort,
        store: BarStorePort,
        notifier: NotifierPort,
    ) -> None:
        self._history = history
        self._store = store
        self._notifier = notifier

    def run(self, instruments: list[InstrumentRef], start: date, end: date) -> BackfillReport:
        report = BackfillReport()
        for instrument in instruments:
            report.results.append(self._load_one(instrument, start, end))
        self._report(report)
        return report

    def _load_one(self, instrument: InstrumentRef, start: date, end: date) -> InstrumentBackfill:
        """Load one instrument, turning a failure into a record rather than raising.

        Same reasoning as the snapshotter: one unavailable symbol out of 26 must
        not abandon the other 25, and the failure has to be visible afterwards.
        """
        try:
            bars = self._history.daily_bars(instrument, start, end)
        except Exception as error:
            return _failure(instrument.symbol, f"{type(error).__name__}: {str(error)[:120]}")

        if not bars:
            return _failure(instrument.symbol, "no bars returned for the range")

        inserted = self._store.write_daily_bars(bars, source="backfill")

        return InstrumentBackfill(
            symbol=instrument.symbol,
            bars_fetched=len(bars),
            bars_inserted=inserted,
            first_date=to_ist(bars[0].start).date(),
            last_date=to_ist(bars[-1].start).date(),
            atr=average_true_range(bars, DEFAULT_ATR_PERIOD),
            range_20d=price_range(bars[-RANGE_WINDOW_DAYS:]),
            suspect_moves=_suspect_moves(instrument.symbol, bars),
        )

    def _report(self, report: BackfillReport) -> None:
        if report.failed:
            self._notifier.send_alert(
                AlertLevel.CRITICAL,
                f"Backfill incomplete: {', '.join(item.symbol for item in report.failed)}",
            )
            return
        if report.not_ready:
            self._notifier.send_alert(
                AlertLevel.WARNING,
                "Backfill loaded but indicators unavailable for: "
                + ", ".join(item.symbol for item in report.not_ready),
            )
            return
        self._notifier.send_alert(AlertLevel.INFO, f"Backfill OK: {report.summary()}")


def _failure(symbol: str, error: str) -> InstrumentBackfill:
    return InstrumentBackfill(
        symbol=symbol,
        bars_fetched=0,
        bars_inserted=0,
        first_date=None,
        last_date=None,
        atr=None,
        range_20d=None,
        suspect_moves=(),
        error=error,
    )


def _suspect_moves(symbol: str, bars: list[Bar]) -> tuple[SuspectMove, ...]:
    """Close-to-close moves large enough to be a corporate action."""
    moves = []
    for previous, current in pairwise(bars):
        change = percent_change(previous.close, current.close)
        if abs(change) >= UNEXPLAINED_MOVE_PERCENT:
            moves.append(SuspectMove(symbol, to_ist(current.start).date(), change))
    return tuple(moves)
