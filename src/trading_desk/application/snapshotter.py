"""Captures option chains and reports honestly on what it captured.

The most important job in v1, and the least glamorous. Every missed snapshot is a
day of IV history that cannot be bought back or derived from price data.

Two rules shape the design:

1. **One failure must not stop the run.** If RELIANCE times out, the other 26
   instruments still get captured. Partial capture beats no capture.
2. **A silent failure is worse than a loud one.** A snapshotter that fails quietly
   leaves a gap that is later trusted as "the market was quiet". So every run
   returns a health report, and anything short of complete raises an alert.
3. **It will not write on a non-trading day.** The calendar check lives here rather
   than in the CLI because three separate entry points reach this class, and two of
   them had no check at all - a verify script and an on-demand capture script would
   both happily have written a Saturday partition full of Friday's quotes. Partitions
   are keyed by IST date, so that phantom session would be indistinguishable from a
   real one and would sit in the IV-rank history permanently. A guard that can be
   bypassed by calling a different entry point is not a guard (ISS-006).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from trading_desk.application.calendars import TradingCalendar
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.timezones import to_ist
from trading_desk.ports.chain_archive import ChainArchivePort
from trading_desk.ports.clock import Clock
from trading_desk.ports.notifier import AlertLevel, NotifierPort
from trading_desk.ports.option_chain import OptionChainPort


@dataclass(frozen=True)
class InstrumentCapture:
    """What happened for one instrument. `error` is None on success."""

    symbol: str
    expiry: date | None
    rows_written: int
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


@dataclass
class CaptureHealth:
    """The result of one snapshot run, in the shape the health log records."""

    captures: list[InstrumentCapture] = field(default_factory=list)

    @property
    def attempted(self) -> int:
        return len(self.captures)

    @property
    def succeeded(self) -> int:
        return sum(1 for capture in self.captures if capture.succeeded)

    @property
    def failed(self) -> list[InstrumentCapture]:
        return [capture for capture in self.captures if not capture.succeeded]

    @property
    def rows_written(self) -> int:
        return sum(capture.rows_written for capture in self.captures)

    @property
    def is_complete(self) -> bool:
        return self.attempted > 0 and not self.failed

    def summary(self) -> str:
        return f"captured {self.succeeded}/{self.attempted} instruments, {self.rows_written} rows"


class MarketClosedError(RuntimeError):
    """Raised when a capture is attempted on a non-trading day.

    An exception rather than a quiet return, because every caller that reaches
    this point believes it is about to collect data. Silently collecting nothing
    looks identical to collecting a session with no trades.
    """


class ChainSnapshotter:
    """Fetches and archives the nearest-expiry chain for each instrument."""

    def __init__(
        self,
        chain_source: OptionChainPort,
        archive: ChainArchivePort,
        notifier: NotifierPort,
        clock: Clock,
        calendar: TradingCalendar,
    ) -> None:
        self._chains = chain_source
        self._archive = archive
        self._notifier = notifier
        self._clock = clock
        self._calendar = calendar

    def run(self, instruments: list[InstrumentRef], force: bool = False) -> CaptureHealth:
        """Capture every instrument, then report. Never raises for one failure.

        Raises MarketClosedError on a non-trading day. `force` exists for a
        deliberate out-of-hours test and is never set by the scheduler.
        """
        today = to_ist(self._clock.utc_now()).date()
        closed = self._calendar.reason_closed(today)
        if closed is not None and not force:
            raise MarketClosedError(
                f"{today}: market closed ({closed}). Capturing now would archive the "
                f"previous session's figures under today's date."
            )

        health = CaptureHealth()
        for instrument in instruments:
            health.captures.append(self._capture_one(instrument))

        self._report(health)
        return health

    def _capture_one(self, instrument: InstrumentRef) -> InstrumentCapture:
        """Capture one instrument's nearest expiry, turning any failure into a record.

        Catching broadly is deliberate here, and the one place it is right: the job
        is to keep going and report, and an unexpected exception type is exactly
        the case that must not take the whole run down.
        """
        try:
            expiries = self._chains.available_expiries(instrument)
            if not expiries:
                return InstrumentCapture(
                    symbol=instrument.symbol,
                    expiry=None,
                    rows_written=0,
                    error="exchange listed no expiries",
                )

            # Nearest expiry only in v1. The IV-rank series is built from the front
            # month, and each extra expiry costs another 3 seconds of rate limit.
            expiry = expiries[0]
            snapshot = self._chains.fetch_chain(instrument, expiry)
            rows = self._archive.write(snapshot)
            return InstrumentCapture(instrument.symbol, expiry, rows)

        except Exception as error:
            return InstrumentCapture(
                symbol=instrument.symbol,
                expiry=None,
                rows_written=0,
                error=f"{type(error).__name__}: {str(error)[:120]}",
            )

    def _report(self, health: CaptureHealth) -> None:
        """Alert on anything short of a complete capture.

        Loud by design. The failure mode worth preventing is a gap nobody noticed,
        because months later it is indistinguishable from a quiet market.
        """
        if health.is_complete:
            self._notifier.send_alert(AlertLevel.INFO, f"Chain snapshot OK: {health.summary()}")
            return

        failures = ", ".join(f"{c.symbol} ({c.error})" for c in health.failed)
        level = AlertLevel.CRITICAL if health.succeeded == 0 else AlertLevel.WARNING
        self._notifier.send_alert(
            level,
            f"Chain snapshot incomplete: {health.summary()}. Failed: {failures}",
        )
