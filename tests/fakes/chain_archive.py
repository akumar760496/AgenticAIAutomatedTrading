"""An in-memory chain archive."""

from __future__ import annotations

from datetime import date

from trading_desk.domain.enums import OptionRight
from trading_desk.domain.market import OptionChainSnapshot
from trading_desk.domain.timezones import to_ist
from trading_desk.domain.values import Money, Price
from trading_desk.domain.vol import atm_implied_volatility


class FakeChainArchive:
    def __init__(self) -> None:
        self.written: list[OptionChainSnapshot] = []
        self.fail_next_write = False

    def write(self, snapshot: OptionChainSnapshot) -> int:
        if self.fail_next_write:
            self.fail_next_write = False
            raise OSError("Simulated disk failure")
        self.written.append(snapshot)
        return sum(1 for strike in snapshot.strikes for side in (strike.call, strike.put) if side)

    def snapshot_count(self, on_date: str, underlying: str) -> int:
        return sum(
            1
            for snapshot in self.written
            if snapshot.underlying.symbol == underlying
            and snapshot.received_at.date().isoformat() == on_date
        )

    def atm_iv_series(self, underlying: str) -> list[tuple[date, float]]:
        """Last snapshot of each day wins, matching the Parquet archive."""
        by_day: dict[date, float] = {}
        for snapshot in sorted(self.written, key=lambda item: item.received_at):
            if snapshot.underlying.symbol != underlying:
                continue
            value = atm_implied_volatility(snapshot)
            if value is None:
                continue
            by_day[to_ist(snapshot.received_at).date()] = value
        return sorted(by_day.items())

    def premium_on(
        self,
        *,
        underlying: str,
        on_date: date,
        expiry: date,
        strike: Price,
        right: OptionRight,
    ) -> Money | None:
        """The mid from the last snapshot written for that day, or None.

        Computed from what was written rather than from a separate dictionary a
        test can set independently. A fake with its own answer store drifts away
        from the real adapter, and then the test proves the fake works.
        """
        same_day = [
            snapshot
            for snapshot in sorted(self.written, key=lambda item: item.received_at)
            if snapshot.underlying.symbol == underlying
            and to_ist(snapshot.received_at).date() == on_date
            and snapshot.expiry_date == expiry.isoformat()
        ]
        if not same_day:
            return None

        for candidate in same_day[-1].strikes:
            if candidate.strike != strike:
                continue
            quote = candidate.call if right is OptionRight.CALL else candidate.put
            if quote is None or quote.bid.paise <= 0 or quote.ask.paise <= 0:
                return None
            return Money(paise=(quote.bid.paise + quote.ask.paise) // 2)
        return None
