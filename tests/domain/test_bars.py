"""Tick to bar aggregation.

docs/12 names the cases this must cover: session boundaries, a tick exactly at the
open, a tick at the last second, out-of-order ticks and duplicates. Each one below
is a way a bar series can look perfectly reasonable and be wrong.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from trading_desk.domain.bars import RejectReason, build_bars
from trading_desk.domain.enums import Exchange, Segment
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.market import Tick
from trading_desk.domain.sessions import SessionHours, session_for
from trading_desk.domain.timezones import IST
from trading_desk.domain.values import Price

HOURS = SessionHours(open_ist=time(9, 15), close_ist=time(15, 30))
SESSION = session_for(date(2026, 10, 19), HOURS)

RELIANCE = InstrumentRef(
    symbol="RELIANCE",
    exchange=Exchange.NSE,
    segment=Segment.EQUITY,
    security_id="2885",
    lot_size=1,
)


def _ist(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 10, 19, hour, minute, second, tzinfo=IST)


def _tick(
    hour: int,
    minute: int,
    price: str,
    *,
    quantity: int = 10,
    second: int = 0,
    sequence: int | None = None,
) -> Tick:
    return Tick(
        instrument=RELIANCE,
        price=Price.from_rupees(Decimal(price)),
        quantity=quantity,
        exchange_time=_ist(hour, minute, second),
        sequence=sequence,
    )


class TestAggregation:
    def test_empty_input_gives_empty_output(self) -> None:
        series = build_bars([], 5, SESSION)

        assert series.bars == ()
        assert series.rejected == ()

    def test_ohlc_comes_from_order_not_from_sorting(self) -> None:
        """Open is the first trade and close the last, even when neither is extreme."""
        series = build_bars(
            [
                _tick(9, 15, "100"),
                _tick(9, 16, "105"),
                _tick(9, 17, "95"),
                _tick(9, 18, "102"),
            ],
            5,
            SESSION,
        )

        assert len(series.bars) == 1
        bar = series.bars[0]
        assert bar.open == Price.from_rupees(Decimal("100"))
        assert bar.high == Price.from_rupees(Decimal("105"))
        assert bar.low == Price.from_rupees(Decimal("95"))
        assert bar.close == Price.from_rupees(Decimal("102"))
        assert bar.volume == 40

    def test_a_window_with_no_trades_produces_no_bar(self) -> None:
        """No interpolation. A missing bar is a fact; a flat one is a fabrication."""
        series = build_bars([_tick(9, 15, "100"), _tick(9, 35, "101")], 5, SESSION)

        assert [bar.start for bar in series.bars] == [_ist(9, 15), _ist(9, 35)]

    def test_every_tick_is_accounted_for(self) -> None:
        ticks = [_tick(9, 15, "100"), _tick(9, 30, "101"), _tick(16, 0, "102")]

        series = build_bars(ticks, 5, SESSION)

        reached_a_bar = sum(1 for _ in series.bars)
        assert reached_a_bar == 2
        assert len(series.rejected) == 1


class TestBoundaries:
    def test_a_tick_exactly_at_the_open_is_included(self) -> None:
        series = build_bars([_tick(9, 15, "100")], 5, SESSION)

        assert len(series.bars) == 1
        assert series.bars[0].start == _ist(9, 15)

    def test_a_tick_at_the_last_second_lands_in_the_final_bar(self) -> None:
        series = build_bars([_tick(15, 29, "100", second=59)], 5, SESSION)

        assert series.bars[0].start == _ist(15, 25)

    def test_a_tick_at_the_closing_instant_is_rejected(self) -> None:
        series = build_bars([_tick(15, 30, "100")], 5, SESSION)

        assert series.bars == ()
        assert series.rejected[0].reason is RejectReason.OUTSIDE_SESSION

    def test_a_pre_open_tick_is_rejected(self) -> None:
        series = build_bars([_tick(9, 14, "100", second=59)], 5, SESSION)

        assert series.rejected[0].reason is RejectReason.OUTSIDE_SESSION

    def test_bar_boundaries_do_not_drift_across_the_day(self) -> None:
        """The last 5m bar must start 15:25, not 15:24 or 15:26."""
        series = build_bars(
            [_tick(9, 15, "100"), _tick(12, 0, "101"), _tick(15, 29, "102", second=59)],
            5,
            SESSION,
        )

        assert [bar.start for bar in series.bars] == [_ist(9, 15), _ist(12, 0), _ist(15, 25)]


class TestOutOfOrder:
    def test_an_out_of_order_tick_inside_the_open_bar_is_folded_in(self) -> None:
        """Still the current window, so no information is lost by accepting it."""
        series = build_bars([_tick(9, 17, "100"), _tick(9, 16, "90")], 5, SESSION)

        assert len(series.bars) == 1
        assert series.bars[0].low == Price.from_rupees(Decimal("90"))
        assert series.rejected == ()

    def test_a_tick_for_an_already_closed_bar_is_rejected_as_late(self) -> None:
        """Accepting it would change a bar a consumer may already have read."""
        series = build_bars(
            [_tick(9, 15, "100"), _tick(9, 25, "101"), _tick(9, 16, "99")], 5, SESSION
        )

        assert len(series.bars) == 2
        assert series.bars[0].low == Price.from_rupees(Decimal("100"))  # unchanged
        late = series.rejected_for(RejectReason.LATE)
        assert len(late) == 1
        assert late[0].tick.exchange_time == _ist(9, 16)

    def test_a_late_tick_does_not_corrupt_the_running_volume(self) -> None:
        series = build_bars(
            [
                _tick(9, 15, "100", quantity=5),
                _tick(9, 25, "101", quantity=7),
                _tick(9, 16, "99", quantity=1000),
            ],
            5,
            SESSION,
        )

        assert series.volume == 12


class TestDuplicates:
    def test_a_repeated_sequence_number_is_discarded(self) -> None:
        series = build_bars(
            [_tick(9, 15, "100", sequence=1), _tick(9, 15, "100", sequence=1)], 5, SESSION
        )

        assert series.bars[0].volume == 10
        assert series.rejected_for(RejectReason.DUPLICATE)[0].tick.sequence == 1

    def test_identical_ticks_without_a_sequence_are_both_kept(self) -> None:
        """Two genuine trades can match exactly. Without a feed sequence number
        there is no way to tell a duplicate from a real second trade, and dropping
        one would understate volume."""
        series = build_bars([_tick(9, 15, "100"), _tick(9, 15, "100")], 5, SESSION)

        assert series.bars[0].volume == 20
        assert series.rejected == ()

    def test_different_sequences_at_the_same_instant_are_both_kept(self) -> None:
        series = build_bars(
            [_tick(9, 15, "100", sequence=1), _tick(9, 15, "100", sequence=2)], 5, SESSION
        )

        assert series.bars[0].volume == 20


class TestIntervals:
    def test_one_minute_bars(self) -> None:
        series = build_bars(
            [_tick(9, 15, "100"), _tick(9, 16, "101"), _tick(9, 17, "102")], 1, SESSION
        )

        assert [bar.start for bar in series.bars] == [_ist(9, 15), _ist(9, 16), _ist(9, 17)]

    def test_a_daily_bar_covers_the_whole_session(self) -> None:
        series = build_bars(
            [_tick(9, 15, "100"), _tick(12, 0, "110"), _tick(15, 29, "105", second=59)],
            375,
            SESSION,
        )

        assert len(series.bars) == 1
        assert series.bars[0].open == Price.from_rupees(Decimal("100"))
        assert series.bars[0].close == Price.from_rupees(Decimal("105"))
        assert series.bars[0].high == Price.from_rupees(Decimal("110"))
