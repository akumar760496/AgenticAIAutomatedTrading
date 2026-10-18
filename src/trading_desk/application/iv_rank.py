"""IV rank per instrument, with its provenance attached.

**The proxy is the normal case right now, not the exception.** Real IV rank
needs our own option-chain history, the archive began on 2026-10-16, and
`MIN_SAMPLES` is thirty trading days. Until roughly six months have accrued
(FR-5.6) almost every value will be realised-vol derived, and the whole point
of this module is that nothing downstream can forget that.

The fallback order, and it never silently skips a step:

1. Enough real ATM IV history -> `IvSource.ACTUAL`.
2. Otherwise enough daily bars -> realised-vol percentile, `IvSource.PROXY`.
3. Otherwise **no number at all**, with the reason recorded.

Step 3 is the one that matters. A percentile over eleven observations is a lie
with a decimal point, and a gate that receives one cannot tell it from a real
reading. Returning `value=None` with a reason lets the briefing say "IV rank
unavailable: 11 samples" instead of quietly acting on noise.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from trading_desk.domain.vol import (
    DEFAULT_WINDOW,
    MIN_SAMPLES,
    IvRank,
    IvSource,
    rank_within,
    realised_volatility,
)
from trading_desk.ports.bar_store import BarStorePort
from trading_desk.ports.chain_archive import ChainArchivePort
from trading_desk.ports.notifier import AlertLevel, NotifierPort

# The lookback each realised-vol observation is computed over. Twenty trading
# days is about a month - long enough to be stable, short enough to move when
# the regime does. It is not the same as the ranking window.
REALISED_VOL_WINDOW = 20


@dataclass
class UniverseIvReport:
    """Ranks for a whole universe, and how many of each kind."""

    ranks: dict[str, IvRank] = field(default_factory=dict)

    @property
    def actual_count(self) -> int:
        return sum(
            1
            for rank in self.ranks.values()
            if rank.is_available and rank.source is IvSource.ACTUAL
        )

    @property
    def proxy_count(self) -> int:
        return sum(
            1 for rank in self.ranks.values() if rank.is_available and rank.source is IvSource.PROXY
        )

    @property
    def unavailable_count(self) -> int:
        return sum(1 for rank in self.ranks.values() if not rank.is_available)

    def summary(self) -> str:
        return (
            f"{len(self.ranks)} instruments: {self.actual_count} from real IV history, "
            f"{self.proxy_count} from the realised-vol proxy, "
            f"{self.unavailable_count} with insufficient history"
        )


class IvRankService:
    """Computes an IV rank per instrument, actual where possible, proxy otherwise."""

    def __init__(
        self,
        archive: ChainArchivePort,
        bars: BarStorePort,
        notifier: NotifierPort,
        window: int = DEFAULT_WINDOW,
        min_samples: int = MIN_SAMPLES,
    ) -> None:
        self._archive = archive
        self._bars = bars
        self._notifier = notifier
        self._window = window
        self._min_samples = min_samples

    def rank_for(self, symbol: str) -> IvRank:
        actual = self._from_chain_history(symbol)
        if actual.is_available:
            return actual

        proxy = self._from_realised_volatility(symbol)
        if proxy.is_available:
            return proxy

        # Neither worked. Report the *chain* shortfall, because that is the one
        # that resolves with time rather than with a backfill.
        return actual

    def run(self, symbols: list[str]) -> UniverseIvReport:
        report = UniverseIvReport(ranks={symbol: self.rank_for(symbol) for symbol in symbols})

        if report.proxy_count:
            self._notifier.send_alert(
                AlertLevel.WARNING,
                f"IV rank: {report.proxy_count} of {len(report.ranks)} values are "
                "realised-vol proxies, not real implied volatility",
            )
        self._notifier.send_alert(AlertLevel.INFO, f"IV rank: {report.summary()}")
        return report

    def _from_chain_history(self, symbol: str) -> IvRank:
        """Rank today's ATM IV against our own captured history."""
        series = self._archive.atm_iv_series(symbol)
        if not series:
            return IvRank(
                value=None,
                source=IvSource.ACTUAL,
                sample_count=0,
                window=self._window,
                reason="no chain history captured yet",
            )

        values = [value for _, value in series]
        return rank_within(
            current=values[-1],
            # Prior days only - see rank_within on why today is excluded.
            history=values[:-1],
            source=IvSource.ACTUAL,
            window=self._window,
            min_samples=self._min_samples,
        )

    def _from_realised_volatility(self, symbol: str) -> IvRank:
        """The documented stand-in: where today's realised vol sits historically.

        Note what this is and is not. It is not implied volatility and does not
        approximate it - realised vol is backward-looking and sits structurally
        below implied. What transfers is the *percentile*: a stock in the top
        decile of its own realised-vol history is usually also rich on implied.
        That is a weaker claim than an IV rank, and PROXY says so.
        """
        closes = [close for _, close in self._bars.daily_closes(symbol)]
        if len(closes) <= REALISED_VOL_WINDOW:
            return IvRank(
                value=None,
                source=IvSource.PROXY,
                sample_count=len(closes),
                window=self._window,
                reason=f"only {len(closes)} daily bars, need more than {REALISED_VOL_WINDOW}",
            )

        observations = []
        for end in range(REALISED_VOL_WINDOW, len(closes)):
            value = realised_volatility(closes[end - REALISED_VOL_WINDOW : end + 1])
            if value is not None:
                observations.append(value)

        if not observations:
            return IvRank(
                value=None,
                source=IvSource.PROXY,
                sample_count=0,
                window=self._window,
                reason="no usable realised-vol observations",
            )

        return rank_within(
            current=observations[-1],
            history=observations[:-1],
            source=IvSource.PROXY,
            window=self._window,
            min_samples=self._min_samples,
        )
