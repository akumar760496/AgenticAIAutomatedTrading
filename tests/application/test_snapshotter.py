"""The snapshotter captures what it can and reports honestly on the rest."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from tests.fakes import FakeChainArchive, FakeNotifier, FakeOptionChain, FrozenClock
from trading_desk.application.snapshotter import ChainSnapshotter
from trading_desk.domain.enums import Exchange, OptionRight, Segment
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.market import OptionChainSnapshot, OptionQuote, OptionStrike
from trading_desk.domain.values import Price
from trading_desk.ports.notifier import AlertLevel

NOW = datetime(2026, 10, 16, 4, 0, tzinfo=UTC)
EXPIRY = date(2026, 10, 19)  # a Monday: the Tuesday was Dussehra (ISS-004)


def _index(symbol: str, security_id: str) -> InstrumentRef:
    return InstrumentRef(
        symbol=symbol,
        exchange=Exchange.NSE,
        segment=Segment.FNO,
        security_id=security_id,
        lot_size=65,
    )


def _chain(instrument: InstrumentRef, strike_count: int = 3) -> OptionChainSnapshot:
    def quote(right: OptionRight) -> OptionQuote:
        return OptionQuote(
            right=right,
            last=Price.from_rupees(Decimal("100")),
            bid=Price.from_rupees(Decimal("99")),
            ask=Price.from_rupees(Decimal("101")),
            volume=1000,
            open_interest=5000,
            implied_volatility=13.5,
            delta=0.5,
        )

    strikes = tuple(
        OptionStrike(
            strike=Price.from_rupees(Decimal(25000 + 100 * offset)),
            call=quote(OptionRight.CALL),
            put=quote(OptionRight.PUT),
        )
        for offset in range(strike_count)
    )
    return OptionChainSnapshot(
        underlying=instrument,
        expiry_date=EXPIRY.isoformat(),
        strikes=strikes,
        spot=Price.from_rupees(Decimal("25050")),
        exchange_time=NOW,
        received_at=NOW,
    )


def _snapshotter() -> tuple[ChainSnapshotter, FakeOptionChain, FakeChainArchive, FakeNotifier]:
    chains, archive, notifier = FakeOptionChain(), FakeChainArchive(), FakeNotifier()
    return (
        ChainSnapshotter(chains, archive, notifier, FrozenClock(NOW)),
        chains,
        archive,
        notifier,
    )


class TestSuccessfulCapture:
    def test_every_instrument_is_captured(self) -> None:
        snapshotter, chains, archive, _ = _snapshotter()
        nifty, banknifty = _index("NIFTY", "13"), _index("BANKNIFTY", "25")
        chains.add_chain(_chain(nifty), EXPIRY)
        chains.add_chain(_chain(banknifty), EXPIRY)

        health = snapshotter.run([nifty, banknifty])

        assert health.succeeded == 2
        assert health.is_complete is True
        assert len(archive.written) == 2

    def test_rows_written_are_counted(self) -> None:
        """Rows, not just files. A file with zero rows is a failure that looks like success."""
        snapshotter, chains, _, _ = _snapshotter()
        nifty = _index("NIFTY", "13")
        chains.add_chain(_chain(nifty, strike_count=5), EXPIRY)

        health = snapshotter.run([nifty])

        assert health.rows_written == 10  # 5 strikes x call and put

    def test_a_complete_run_reports_info(self) -> None:
        snapshotter, chains, _, notifier = _snapshotter()
        nifty = _index("NIFTY", "13")
        chains.add_chain(_chain(nifty), EXPIRY)

        snapshotter.run([nifty])

        assert notifier.alerts[0][0] is AlertLevel.INFO


class TestPartialFailure:
    def test_one_failure_does_not_stop_the_run(self) -> None:
        """Partial capture beats no capture - the other instruments still matter."""
        snapshotter, chains, archive, _ = _snapshotter()
        nifty, banknifty = _index("NIFTY", "13"), _index("BANKNIFTY", "25")
        chains.add_chain(_chain(nifty), EXPIRY)
        # BANKNIFTY has no chain registered, so it raises LookupError.

        health = snapshotter.run([nifty, banknifty])

        assert health.succeeded == 1
        assert len(health.failed) == 1
        assert len(archive.written) == 1

    def test_a_partial_run_raises_a_warning_naming_the_instrument(self) -> None:
        snapshotter, chains, _, notifier = _snapshotter()
        nifty, banknifty = _index("NIFTY", "13"), _index("BANKNIFTY", "25")
        chains.add_chain(_chain(nifty), EXPIRY)

        snapshotter.run([nifty, banknifty])

        level, message = notifier.alerts[0]
        assert level is AlertLevel.WARNING
        assert "BANKNIFTY" in message

    def test_total_failure_is_critical(self) -> None:
        snapshotter, _, _, notifier = _snapshotter()

        snapshotter.run([_index("NIFTY", "13")])

        assert notifier.alerts[0][0] is AlertLevel.CRITICAL

    def test_a_fetch_error_is_recorded_not_raised(self) -> None:
        snapshotter, chains, _, _ = _snapshotter()
        nifty = _index("NIFTY", "13")
        chains.add_chain(_chain(nifty), EXPIRY)
        chains.fail_next_fetch = True

        health = snapshotter.run([nifty])

        assert health.failed[0].error is not None
        assert "ConnectionError" in health.failed[0].error

    def test_a_write_failure_is_recorded(self) -> None:
        """Disk full at 15:15 must be loud, not a quiet gap in the archive."""
        snapshotter, chains, archive, notifier = _snapshotter()
        nifty = _index("NIFTY", "13")
        chains.add_chain(_chain(nifty), EXPIRY)
        archive.fail_next_write = True

        health = snapshotter.run([nifty])

        assert health.is_complete is False
        assert notifier.alerts[0][0] is AlertLevel.CRITICAL


class TestExpirySelection:
    def test_the_nearest_expiry_is_used(self) -> None:
        """Never a computed date: ISS-004 showed expiries move off Tuesday for holidays."""
        snapshotter, chains, archive, _ = _snapshotter()
        nifty = _index("NIFTY", "13")
        later = date(2026, 10, 27)
        chains.add_chain(_chain(nifty), EXPIRY)
        chains.add_chain(_chain(nifty), later)

        snapshotter.run([nifty])

        assert archive.written[0].expiry_date == EXPIRY.isoformat()
