"""The backfill loads what it can, never duplicates, and never adjusts."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from tests.fakes import FakeBarStore, FakeHistory, FakeNotifier
from trading_desk.application.backfill import (
    RANGE_WINDOW_DAYS,
    UNEXPLAINED_MOVE_PERCENT,
    BackfillService,
)
from trading_desk.domain.enums import Exchange, Segment
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.market import Bar
from trading_desk.domain.timezones import IST
from trading_desk.domain.values import Money, Price
from trading_desk.ports.notifier import AlertLevel

START = date(2026, 1, 1)
END = date(2026, 12, 31)


def _instrument(symbol: str) -> InstrumentRef:
    return InstrumentRef(
        symbol=symbol,
        exchange=Exchange.NSE,
        segment=Segment.EQUITY,
        security_id="1",
        lot_size=1,
    )


def _bar(instrument: InstrumentRef, day: int, close: str, high: str = "", low: str = "") -> Bar:
    price = Decimal(close)
    return Bar(
        instrument=instrument,
        # Midnight IST, as the real adapter produces.
        start=datetime(2026, 1, 1, tzinfo=IST) + timedelta(days=day),
        open=Price.from_rupees(price),
        high=Price.from_rupees(Decimal(high) if high else price + 5),
        low=Price.from_rupees(Decimal(low) if low else price - 5),
        close=Price.from_rupees(price),
        volume=1000,
    )


def _series(instrument: InstrumentRef, closes: list[str]) -> list[Bar]:
    return [_bar(instrument, index, close) for index, close in enumerate(closes)]


def _service(history: FakeHistory, store: FakeBarStore, notifier: FakeNotifier) -> BackfillService:
    return BackfillService(history=history, store=store, notifier=notifier)


class TestLoading:
    def test_loads_every_instrument(self) -> None:
        history, store, notifier = FakeHistory(), FakeBarStore(), FakeNotifier()
        reliance, tcs = _instrument("RELIANCE"), _instrument("TCS")
        history.add_bars("RELIANCE", _series(reliance, ["100"] * 30))
        history.add_bars("TCS", _series(tcs, ["200"] * 30))

        report = _service(history, store, notifier).run([reliance, tcs], START, END)

        assert report.bars_inserted == 60
        assert report.is_clean
        assert notifier.alerts[-1][0] is AlertLevel.INFO

    def test_a_second_run_inserts_nothing(self) -> None:
        """The single most common data bug: counts merely too large, never wrong."""
        history, store, notifier = FakeHistory(), FakeBarStore(), FakeNotifier()
        reliance = _instrument("RELIANCE")
        history.add_bars("RELIANCE", _series(reliance, ["100"] * 30))
        service = _service(history, store, notifier)

        first = service.run([reliance], START, END)
        second = service.run([reliance], START, END)

        assert first.bars_inserted == 30
        assert second.bars_inserted == 0
        assert store.count_daily_bars("RELIANCE") == 30

    def test_one_failure_does_not_abandon_the_rest(self) -> None:
        history, store, notifier = FakeHistory(), FakeBarStore(), FakeNotifier()
        reliance, tcs = _instrument("RELIANCE"), _instrument("TCS")
        history.add_bars("RELIANCE", _series(reliance, ["100"] * 30))
        history.add_bars("TCS", _series(tcs, ["200"] * 30))
        history.fail_next_fetch = True

        report = _service(history, store, notifier).run([reliance, tcs], START, END)

        assert len(report.failed) == 1
        assert "ConnectionError" in (report.failed[0].error or "")
        assert store.count_daily_bars("TCS") == 30
        assert notifier.alerts[-1][0] is AlertLevel.CRITICAL

    def test_an_instrument_with_no_history_is_recorded_not_skipped(self) -> None:
        history, store, notifier = FakeHistory(), FakeBarStore(), FakeNotifier()
        delisted = _instrument("DELISTED")

        report = _service(history, store, notifier).run([delisted], START, END)

        assert report.failed[0].error == "no bars returned for the range"


class TestIndicatorReadiness:
    def test_enough_history_makes_indicators_available(self) -> None:
        history, store, notifier = FakeHistory(), FakeBarStore(), FakeNotifier()
        reliance = _instrument("RELIANCE")
        history.add_bars("RELIANCE", _series(reliance, ["100"] * 30))

        report = _service(history, store, notifier).run([reliance], START, END)

        assert report.results[0].indicators_ready
        assert report.results[0].atr is not None
        assert report.results[0].range_20d is not None

    def test_too_little_history_is_flagged_rather_than_approximated(self) -> None:
        """A short series gets no ATR at all. The alternative - an ATR over five
        bars - is a different number that a gate would silently act on."""
        history, store, notifier = FakeHistory(), FakeBarStore(), FakeNotifier()
        thin = _instrument("NEWLISTING")
        history.add_bars("NEWLISTING", _series(thin, ["100"] * 5))

        report = _service(history, store, notifier).run([thin], START, END)

        assert report.results[0].atr is None
        assert not report.results[0].indicators_ready
        assert not report.is_clean
        assert notifier.alerts[-1][0] is AlertLevel.WARNING

    def test_the_range_window_excludes_anything_older_than_twenty_days(self) -> None:
        history, store, notifier = FakeHistory(), FakeBarStore(), FakeNotifier()
        reliance = _instrument("RELIANCE")
        # A spike on day 0, then 25 quiet days, so the spike falls outside the window.
        closes = ["500"] + ["100"] * 25
        history.add_bars("RELIANCE", _series(reliance, closes))

        report = _service(history, store, notifier).run([reliance], START, END)

        # Quiet bars run high 105 to low 95, so the 20-day range is exactly 10.
        # Including the day-0 spike would make it 410.
        assert report.results[0].range_20d == Money.from_rupees("10")
        assert len(closes) > RANGE_WINDOW_DAYS  # the fixture must actually overflow it


class TestValidation:
    def test_a_large_overnight_move_is_flagged(self) -> None:
        history, store, notifier = FakeHistory(), FakeBarStore(), FakeNotifier()
        reliance = _instrument("RELIANCE")
        # A 1-for-2 split: the close halves overnight.
        closes = ["1000"] * 20 + ["500"] + ["500"] * 9
        history.add_bars("RELIANCE", _series(reliance, closes))

        report = _service(history, store, notifier).run([reliance], START, END)

        assert len(report.suspect_moves) == 1
        assert report.suspect_moves[0].percent < -UNEXPLAINED_MOVE_PERCENT

    def test_a_flagged_move_does_not_change_the_stored_prices(self) -> None:
        """Flag, never adjust. Dhan's history may already be adjusted, and a
        second adjustment turns a visible anomaly into an invisible one."""
        history, store, notifier = FakeHistory(), FakeBarStore(), FakeNotifier()
        reliance = _instrument("RELIANCE")
        closes = ["1000"] * 20 + ["500"] * 10
        history.add_bars("RELIANCE", _series(reliance, closes))

        _service(history, store, notifier).run([reliance], START, END)

        stored = sorted(store.bars.items())
        assert stored[0][1].close == Price.from_rupees(Decimal("1000"))
        assert stored[-1][1].close == Price.from_rupees(Decimal("500"))

    def test_ordinary_volatility_is_not_flagged(self) -> None:
        history, store, notifier = FakeHistory(), FakeBarStore(), FakeNotifier()
        reliance = _instrument("RELIANCE")
        closes = ["100", "105", "98", "103", "96"] * 6
        history.add_bars("RELIANCE", _series(reliance, closes))

        report = _service(history, store, notifier).run([reliance], START, END)

        assert report.suspect_moves == []

    def test_suspect_moves_do_not_make_the_report_dirty(self) -> None:
        """They need explaining, which is a human step, not a load failure."""
        history, store, notifier = FakeHistory(), FakeBarStore(), FakeNotifier()
        reliance = _instrument("RELIANCE")
        history.add_bars("RELIANCE", _series(reliance, ["1000"] * 20 + ["500"] * 10))

        report = _service(history, store, notifier).run([reliance], START, END)

        assert report.suspect_moves
        assert report.is_clean
