"""Refreshes the cached expiry list and audits the holiday calendar against it.

Two outputs from one fetch, and the second is the interesting one.

The obvious job is caching: read each instrument's expiry list from the venue so
gates can ask "days to expiry" without a network call.

The second job falls out for free. A weekly expiry that is not on its usual
Tuesday means the exchange was shut that Tuesday, so the expiry list is *primary
evidence about the holiday calendar* - better than any published calendar,
because it is the venue's own behaviour. Comparing it against `holidays.yaml`
turns a stale holiday file into a visible failure. It is how 2026-11-24 was found
missing, after sitting unnoticed through four verification sweeps.

Nothing here computes an expiry (ISS-004). It only reads published ones and
reasons about which weekday they landed on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from trading_desk.application.calendars import TradingCalendar
from trading_desk.domain.expiries import DisplacedExpiry, displaced_expiries
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.ports.clock import Clock
from trading_desk.ports.expiry_store import ExpiryStorePort
from trading_desk.ports.notifier import AlertLevel, NotifierPort
from trading_desk.ports.option_chain import OptionChainPort


@dataclass(frozen=True)
class SymbolExpiries:
    """One instrument's refreshed list, and what it implies."""

    symbol: str
    expiries: tuple[date, ...]
    displaced: tuple[DisplacedExpiry, ...]
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


@dataclass
class ExpiryRefreshReport:
    """What was refreshed, and any holiday the calendar is missing."""

    results: list[SymbolExpiries] = field(default_factory=list)
    missing_holidays: list[date] = field(default_factory=list)

    @property
    def failed(self) -> list[SymbolExpiries]:
        return [item for item in self.results if not item.succeeded]

    @property
    def expiries_cached(self) -> int:
        return sum(len(item.expiries) for item in self.results)

    def summary(self) -> str:
        return (
            f"{len(self.results) - len(self.failed)}/{len(self.results)} instruments, "
            f"{self.expiries_cached} expiries cached, "
            f"{len(self.missing_holidays)} holiday(s) missing from config"
        )


class ExpiryRefresh:
    """Refreshes cached expiries and audits the holiday calendar."""

    def __init__(
        self,
        chain_source: OptionChainPort,
        store: ExpiryStorePort,
        notifier: NotifierPort,
        clock: Clock,
    ) -> None:
        self._chains = chain_source
        self._store = store
        self._notifier = notifier
        self._clock = clock

    def run(
        self, instruments: list[InstrumentRef], calendar: TradingCalendar
    ) -> ExpiryRefreshReport:
        report = ExpiryRefreshReport()
        fetched_at = self._clock.utc_now()

        for instrument in instruments:
            report.results.append(self._refresh_one(instrument, fetched_at))

        implied = {item.expected for result in report.results for item in result.displaced}
        report.missing_holidays = calendar.missing_holidays(implied)

        self._report(report)
        return report

    def _refresh_one(self, instrument: InstrumentRef, fetched_at: date | object) -> SymbolExpiries:
        """Fetch and cache one list, recording a failure rather than raising."""
        try:
            expiries = self._chains.available_expiries(instrument)
        except Exception as error:  # one bad symbol must not end the refresh
            return SymbolExpiries(
                symbol=instrument.symbol,
                expiries=(),
                displaced=(),
                error=f"{type(error).__name__}: {str(error)[:120]}",
            )

        displaced = tuple(displaced_expiries(expiries))
        self._store.save_expiries(
            symbol=instrument.symbol,
            expiries=expiries,
            fetched_at=fetched_at,  # type: ignore[arg-type]
            displaced=[item.actual for item in displaced],
        )
        return SymbolExpiries(
            symbol=instrument.symbol,
            expiries=tuple(expiries),
            displaced=displaced,
        )

    def _report(self, report: ExpiryRefreshReport) -> None:
        if report.failed:
            self._notifier.send_alert(
                AlertLevel.CRITICAL,
                "Expiry refresh incomplete: " + ", ".join(item.symbol for item in report.failed),
            )
            return

        if report.missing_holidays:
            # Loud, because the holiday file drives whether captures run at all.
            self._notifier.send_alert(
                AlertLevel.CRITICAL,
                "The exchange moved expiries off these Tuesdays but config/holidays.yaml "
                "does not list them: "
                + ", ".join(day.isoformat() for day in report.missing_holidays),
            )
            return

        self._notifier.send_alert(AlertLevel.INFO, f"Expiry refresh OK: {report.summary()}")
