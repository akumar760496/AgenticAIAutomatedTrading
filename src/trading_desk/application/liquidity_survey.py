"""Surveys every F&O underlying's option chain once, to measure liquidity.

This is deliberately not the snapshotter. The snapshotter captures a small
universe three times a day and its output is history that cannot be rebuilt. The
survey captures the *whole* candidate list once, and its output is a ranking that
can be recomputed any time. Different jobs, different cadence, different cost:
with ~218 underlyings and two rate-limited calls each at 4.5s, a full survey takes
roughly half an hour.

Why survey at all, rather than rank from the Parquet archive: the archive only
holds the names already being captured, so ranking from it would re-rank the
hand-written starter list and never discover anything outside it. The candidate
list has to come from the exchange's instrument master.

**The survey must run on a trading day,** and the caller is responsible for
checking that before starting one. Two live findings forced that split of
responsibility:

1. A weekend run does not return zeroes. Volume is session-cumulative and Dhan
   keeps serving the previous session's figures, so a Saturday survey looks
   perfectly healthy while describing Friday.
2. The chain's `exchange_time` cannot detect that, because Dhan stamps it with the
   time it answered the request. A Saturday-midnight survey reports a
   Saturday-midnight timestamp over Friday's volume.

So nothing in the response tells you which session it describes. The trading
calendar does, and it lives in the caller, which is why `LiquiditySurvey` takes no
calendar and makes no claim about freshness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from trading_desk.domain.enums import Exchange, Segment
from trading_desk.domain.instruments import InstrumentCatalogue, InstrumentRef
from trading_desk.domain.liquidity import LiquidityMeasure, measure_chain
from trading_desk.ports.clock import Clock
from trading_desk.ports.notifier import AlertLevel, NotifierPort
from trading_desk.ports.option_chain import OptionChainPort

# If this share of surveyed names reports zero premium turnover, the feed is dead
# rather than quiet. Half is far beyond anything a real session produces: illiquid
# names have thin turnover, not none. This catches a broken feed, and deliberately
# does not try to catch an out-of-hours run - see the module docstring.
MAX_ZERO_TURNOVER_FRACTION = 0.5

# A survey of two names proves nothing about a 218-name distribution, and ranking
# from it would produce a confident-looking universe from noise.
MIN_USABLE_MEASURES = 20


@dataclass(frozen=True)
class SurveyFailure:
    """One underlying that could not be measured, and why."""

    symbol: str
    error: str


@dataclass
class SurveyResult:
    """Everything the survey measured, everything it could not, and whether it counts."""

    measures: list[LiquidityMeasure] = field(default_factory=list)
    failures: list[SurveyFailure] = field(default_factory=list)
    surveyed_at: datetime | None = None

    @property
    def attempted(self) -> int:
        return len(self.measures) + len(self.failures)

    @property
    def zero_turnover_count(self) -> int:
        return sum(1 for measure in self.measures if measure.premium_turnover.is_zero())

    @property
    def unusable_reason(self) -> str | None:
        """Why this survey must not be turned into a universe, or None if it may be.

        Fail closed (CLAUDE.md rule 6): a survey that looks like a weekend run
        produces no universe at all rather than an empty one.
        """
        if len(self.measures) < MIN_USABLE_MEASURES:
            return (
                f"only {len(self.measures)} of {self.attempted} underlyings measured, "
                f"need at least {MIN_USABLE_MEASURES}"
            )

        zero_fraction = self.zero_turnover_count / len(self.measures)
        if zero_fraction > MAX_ZERO_TURNOVER_FRACTION:
            return (
                f"{self.zero_turnover_count} of {len(self.measures)} underlyings show zero "
                f"premium turnover ({zero_fraction:.0%}) - the feed is not returning "
                f"session volume"
            )
        return None

    def summary(self) -> str:
        return (
            f"Surveyed {len(self.measures)}/{self.attempted} underlyings, "
            f"{len(self.failures)} failed, {self.zero_turnover_count} with zero turnover"
        )


class LiquiditySurvey:
    """Measures one nearest-expiry chain per candidate underlying."""

    def __init__(
        self,
        chain_source: OptionChainPort,
        notifier: NotifierPort,
        clock: Clock,
    ) -> None:
        self._chains = chain_source
        self._notifier = notifier
        self._clock = clock

    def run(self, catalogue: InstrumentCatalogue) -> SurveyResult:
        """Survey every underlying in the catalogue, in symbol order.

        Symbol order rather than arbitrary dict order so that a survey interrupted
        half way is reproducible, and two runs can be compared.
        """
        result = SurveyResult(surveyed_at=self._clock.utc_now())

        for symbol in sorted(catalogue.underlyings):
            info = catalogue.underlyings[symbol]
            instrument = InstrumentRef(
                symbol=info.symbol,
                exchange=Exchange.NSE,
                segment=Segment.FNO,
                security_id=info.security_id,
                lot_size=info.lot_size,
            )
            measure = self._measure_one(instrument, is_index=info.is_index, result=result)
            if measure is not None:
                result.measures.append(measure)

        self._report(result)
        return result

    def _measure_one(
        self, instrument: InstrumentRef, is_index: bool, result: SurveyResult
    ) -> LiquidityMeasure | None:
        """Measure one underlying, recording a failure instead of raising.

        Catches broadly on purpose, as the snapshotter does: one unparseable chain
        out of 218 must not end a half-hour survey. The exception type and message
        are kept so a pattern across failures is visible afterwards.
        """
        try:
            expiries = self._chains.available_expiries(instrument)
            if not expiries:
                result.failures.append(SurveyFailure(instrument.symbol, "no listed expiries"))
                return None

            chain = self._chains.fetch_chain(instrument, expiries[0])
            return measure_chain(chain, is_index=is_index)
        except Exception as error:  # one bad chain must not end a half-hour survey
            result.failures.append(
                SurveyFailure(instrument.symbol, f"{type(error).__name__}: {error}")
            )
            return None

    def _report(self, result: SurveyResult) -> None:
        unusable = result.unusable_reason
        if unusable is not None:
            self._notifier.send_alert(AlertLevel.CRITICAL, f"Liquidity survey unusable: {unusable}")
            return
        if result.failures:
            self._notifier.send_alert(AlertLevel.WARNING, result.summary())
