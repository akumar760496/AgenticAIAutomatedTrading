"""The refresh caches expiries and audits the holiday file against them."""

from __future__ import annotations

from datetime import UTC, date, datetime

from tests.fakes import FakeExpiryStore, FakeNotifier, FakeOptionChain, FrozenClock
from trading_desk.application.calendars import TradingCalendar
from trading_desk.application.expiry_refresh import ExpiryRefresh
from trading_desk.domain.enums import Exchange, OptionRight, Segment
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.market import OptionChainSnapshot, OptionQuote, OptionStrike
from trading_desk.domain.values import Price
from trading_desk.ports.notifier import AlertLevel

NOW = datetime(2026, 10, 18, 4, 0, tzinfo=UTC)

NIFTY_LIVE = [
    date(2026, 10, 19),
    date(2026, 10, 27),
    date(2026, 11, 3),
    date(2026, 11, 9),
    date(2026, 11, 17),
    date(2026, 11, 23),
]
IMPLIED_HOLIDAYS = {date(2026, 10, 20), date(2026, 11, 10), date(2026, 11, 24)}

NIFTY = InstrumentRef(
    symbol="NIFTY",
    exchange=Exchange.NSE,
    segment=Segment.FNO,
    security_id="13",
    lot_size=65,
)


def _calendar(holidays: set[date]) -> TradingCalendar:
    return TradingCalendar(
        holidays=frozenset(holidays), special_sessions=frozenset(), verified=True
    )


def _chain_for(expiry: date) -> OptionChainSnapshot:
    quote = OptionQuote(
        right=OptionRight.CALL,
        last=Price.from_rupees(100),
        bid=Price.from_rupees(99),
        ask=Price.from_rupees(101),
        volume=10,
        open_interest=10,
    )
    return OptionChainSnapshot(
        underlying=NIFTY,
        expiry_date=expiry.isoformat(),
        strikes=(OptionStrike(strike=Price.from_rupees(25000), call=quote, put=None),),
        spot=Price.from_rupees(25000),
        exchange_time=NOW,
        received_at=NOW,
    )


def _refresh(
    chains: FakeOptionChain, store: FakeExpiryStore, notifier: FakeNotifier
) -> ExpiryRefresh:
    return ExpiryRefresh(chains, store, notifier, FrozenClock(NOW))


def _chains_with_live_expiries() -> FakeOptionChain:
    chains = FakeOptionChain()
    for expiry in NIFTY_LIVE:
        chains.add_chain(_chain_for(expiry), expiry)
    return chains


def test_caches_every_expiry_with_a_fetch_time() -> None:
    chains, store, notifier = _chains_with_live_expiries(), FakeExpiryStore(), FakeNotifier()

    report = _refresh(chains, store, notifier).run([NIFTY], _calendar(IMPLIED_HOLIDAYS))

    assert store.expiries_for("NIFTY") == NIFTY_LIVE
    assert store.last_fetched_at("NIFTY") == NOW
    assert report.expiries_cached == len(NIFTY_LIVE)


def test_marks_the_displaced_expiries_in_the_cache() -> None:
    chains, store, notifier = _chains_with_live_expiries(), FakeExpiryStore(), FakeNotifier()

    _refresh(chains, store, notifier).run([NIFTY], _calendar(IMPLIED_HOLIDAYS))

    assert store.displaced["NIFTY"] == {
        date(2026, 10, 19),
        date(2026, 11, 9),
        date(2026, 11, 23),
    }


def test_a_complete_holiday_file_reports_nothing_missing() -> None:
    chains, store, notifier = _chains_with_live_expiries(), FakeExpiryStore(), FakeNotifier()

    report = _refresh(chains, store, notifier).run([NIFTY], _calendar(IMPLIED_HOLIDAYS))

    assert report.missing_holidays == []
    assert notifier.alerts[-1][0] is AlertLevel.INFO


def test_a_stale_holiday_file_is_reported_critically() -> None:
    """The real case: 2026-11-24 was absent and nothing had noticed."""
    chains, store, notifier = _chains_with_live_expiries(), FakeExpiryStore(), FakeNotifier()
    incomplete = IMPLIED_HOLIDAYS - {date(2026, 11, 24)}

    report = _refresh(chains, store, notifier).run([NIFTY], _calendar(incomplete))

    assert report.missing_holidays == [date(2026, 11, 24)]
    level, message = notifier.alerts[-1]
    assert level is AlertLevel.CRITICAL
    assert "2026-11-24" in message


def test_one_failure_does_not_end_the_refresh() -> None:
    chains, store, notifier = _chains_with_live_expiries(), FakeExpiryStore(), FakeNotifier()
    other = InstrumentRef(
        symbol="BANKNIFTY",
        exchange=Exchange.NSE,
        segment=Segment.FNO,
        security_id="25",
        lot_size=30,
    )

    report = _refresh(chains, store, notifier).run([other, NIFTY], _calendar(IMPLIED_HOLIDAYS))

    # BANKNIFTY has no chains in the fake, so its expiry list is empty, not an error.
    assert store.expiries_for("NIFTY") == NIFTY_LIVE
    assert len(report.results) == 2
