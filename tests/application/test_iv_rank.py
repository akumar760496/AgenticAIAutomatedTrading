"""The actual-vs-proxy fallback, and the refusal at the end of it."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from tests.fakes import FakeBarStore, FakeChainArchive, FakeNotifier
from trading_desk.application.iv_rank import REALISED_VOL_WINDOW, IvRankService
from trading_desk.domain.enums import Exchange, OptionRight, Segment
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.market import Bar, OptionChainSnapshot, OptionQuote, OptionStrike
from trading_desk.domain.timezones import IST
from trading_desk.domain.values import Price
from trading_desk.domain.vol import IvSource
from trading_desk.ports.notifier import AlertLevel

START = datetime(2026, 1, 1, 15, 15, tzinfo=IST)

RELIANCE = InstrumentRef(
    symbol="RELIANCE", exchange=Exchange.NSE, segment=Segment.FNO, security_id="1", lot_size=250
)


def _quote(right: OptionRight, iv: float | None) -> OptionQuote:
    price = Price.from_rupees(Decimal("50"))
    return OptionQuote(
        right=right,
        last=price,
        bid=price,
        ask=price,
        volume=100,
        open_interest=1000,
        implied_volatility=iv,
    )


def _snapshot(day: int, iv: float) -> OptionChainSnapshot:
    spot = Price.from_rupees(Decimal("1400"))
    return OptionChainSnapshot(
        underlying=RELIANCE,
        expiry_date="2026-02-26",
        strikes=(
            OptionStrike(
                strike=spot,
                call=_quote(OptionRight.CALL, iv),
                put=_quote(OptionRight.PUT, iv),
            ),
        ),
        spot=spot,
        exchange_time=START + timedelta(days=day),
        received_at=START + timedelta(days=day),
    )


def _bar(day: int, close: str) -> Bar:
    price = Price.from_rupees(Decimal(close))
    return Bar(
        instrument=RELIANCE,
        start=datetime(2026, 1, 1, tzinfo=IST) + timedelta(days=day),
        open=price,
        high=price,
        low=price,
        close=price,
        volume=1000,
    )


def _service(
    archive: FakeChainArchive, bars: FakeBarStore, notifier: FakeNotifier
) -> IvRankService:
    return IvRankService(archive=archive, bars=bars, notifier=notifier, min_samples=30)


class TestRealIvHistoryWins:
    def test_enough_chain_history_gives_an_actual_rank(self) -> None:
        archive, bars, notifier = FakeChainArchive(), FakeBarStore(), FakeNotifier()
        # 40 quiet days then a spike: today should rank at the top.
        for day in range(40):
            archive.write(_snapshot(day, 18.0 + day * 0.01))
        archive.write(_snapshot(40, 55.0))

        rank = _service(archive, bars, notifier).rank_for("RELIANCE")

        assert rank.source is IvSource.ACTUAL
        assert rank.value == 100.0
        assert rank.sample_count == 40

    def test_real_history_is_preferred_even_when_bars_exist(self) -> None:
        archive, bars, notifier = FakeChainArchive(), FakeBarStore(), FakeNotifier()
        for day in range(40):
            archive.write(_snapshot(day, 20.0 + day * 0.1))
        bars.write_daily_bars([_bar(day, str(100 + day)) for day in range(300)])

        assert _service(archive, bars, notifier).rank_for("RELIANCE").source is IvSource.ACTUAL


class TestProxyFallback:
    def test_too_little_chain_history_falls_back_to_the_proxy(self) -> None:
        """The normal case today: the archive started on 2026-10-16."""
        archive, bars, notifier = FakeChainArchive(), FakeBarStore(), FakeNotifier()
        archive.write(_snapshot(0, 20.0))
        closes = [str(100 + (day % 7) * 3) for day in range(120)]
        bars.write_daily_bars([_bar(day, close) for day, close in enumerate(closes)])

        rank = _service(archive, bars, notifier).rank_for("RELIANCE")

        assert rank.source is IvSource.PROXY
        assert rank.is_available

    def test_the_proxy_is_announced_not_buried(self) -> None:
        archive, bars, notifier = FakeChainArchive(), FakeBarStore(), FakeNotifier()
        closes = [str(100 + (day % 5) * 2) for day in range(120)]
        bars.write_daily_bars([_bar(day, close) for day, close in enumerate(closes)])

        report = _service(archive, bars, notifier).run(["RELIANCE"])

        assert report.proxy_count == 1
        assert any(
            level is AlertLevel.WARNING and "proxies" in message
            for level, message in notifier.alerts
        )


class TestRefusal:
    def test_neither_source_available_returns_no_number(self) -> None:
        archive, bars, notifier = FakeChainArchive(), FakeBarStore(), FakeNotifier()

        rank = _service(archive, bars, notifier).rank_for("RELIANCE")

        assert rank.value is None
        assert not rank.is_available
        assert "no chain history" in rank.reason

    def test_too_few_bars_for_even_the_proxy(self) -> None:
        archive, bars, notifier = FakeChainArchive(), FakeBarStore(), FakeNotifier()
        bars.write_daily_bars([_bar(day, "100") for day in range(REALISED_VOL_WINDOW)])

        assert not _service(archive, bars, notifier).rank_for("RELIANCE").is_available

    def test_the_report_counts_the_unavailable_ones(self) -> None:
        archive, bars, notifier = FakeChainArchive(), FakeBarStore(), FakeNotifier()

        report = _service(archive, bars, notifier).run(["RELIANCE", "INFY"])

        assert report.unavailable_count == 2
        assert "2 with insufficient history" in report.summary()
