"""Two health numbers about the system's own behaviour, not the market's.

**The no-trade rate** (FR-7.9). `docs/06` expects the setups to produce no idea
on 40-60% of sessions, and both sides of that band are failures:

- **Below 40%** the gates are too loose. This is the dangerous direction,
  because a system that briefs something every single day looks productive and
  is simply finding a way to approve whatever it sees. `docs/12` §6 puts it as
  "gates fire on almost every candidate - a gate reads a field that's always
  null, so it always passes".
- **Above 60%** something is broken upstream and the briefing has gone quiet.
  A dead news feed looks exactly like a slow news month, which is a mistake
  this project has already made once (ISS-016).

**Briefing engagement** (FR-7.10). Whether the briefings are being read and
answered. It feeds the Gate 2 criterion in `docs/06` - ">70% ignored" means stop
- and it is deliberately a measurement rather than a judgement: the number is
reported, and what to do about it is the user's call.

Both are pure functions over counts. The alerting lives in `application/`.
"""

from __future__ import annotations

from dataclasses import dataclass

# The band from docs/06. Config would be the right home for these if they were
# exchange or rulebook numbers, but they are a claim about this system's own
# behaviour, which is what this module is about.
NO_TRADE_BAND_LOW = 0.40
NO_TRADE_BAND_HIGH = 0.60

# Below this many sessions the rate is noise. Three quiet days out of four is
# 75% and means nothing.
MIN_SESSIONS_FOR_RATE = 10

# docs/06's Gate 2 criterion, inverted into a rate.
MAX_IGNORED_FRACTION = 0.70


@dataclass(frozen=True)
class NoTradeRate:
    """How often the system said nothing, against the band it should sit in."""

    sessions: int
    sessions_with_no_idea: int

    def __post_init__(self) -> None:
        if self.sessions < 0 or self.sessions_with_no_idea < 0:
            raise ValueError("counts cannot be negative")
        if self.sessions_with_no_idea > self.sessions:
            raise ValueError(
                f"{self.sessions_with_no_idea} quiet sessions out of {self.sessions} "
                f"is not possible"
            )

    @property
    def rate(self) -> float | None:
        """None, not zero, when there is nothing to measure (rule 6)."""
        if self.sessions == 0:
            return None
        return self.sessions_with_no_idea / self.sessions

    @property
    def is_measurable(self) -> bool:
        return self.sessions >= MIN_SESSIONS_FOR_RATE

    @property
    def is_too_loose(self) -> bool:
        rate = self.rate
        return self.is_measurable and rate is not None and rate < NO_TRADE_BAND_LOW

    @property
    def is_too_quiet(self) -> bool:
        rate = self.rate
        return self.is_measurable and rate is not None and rate > NO_TRADE_BAND_HIGH

    @property
    def needs_alert(self) -> bool:
        return self.is_too_loose or self.is_too_quiet

    def describe(self) -> str:
        rate = self.rate
        if rate is None:
            return "no-trade rate: no sessions recorded yet"
        body = (
            f"no-trade rate {rate:.1%} ({self.sessions_with_no_idea} quiet of "
            f"{self.sessions} sessions), band "
            f"{NO_TRADE_BAND_LOW:.0%}-{NO_TRADE_BAND_HIGH:.0%}"
        )
        if not self.is_measurable:
            return f"{body} - under {MIN_SESSIONS_FOR_RATE} sessions, not yet meaningful"
        if self.is_too_loose:
            return f"{body} - TOO LOOSE, the gates are approving almost everything"
        if self.is_too_quiet:
            return f"{body} - TOO QUIET, check the feeds before the thresholds"
        return f"{body} - inside the band"


@dataclass(frozen=True)
class Engagement:
    """Whether the briefings are being answered.

    `acted_on` counts decisions of any kind, TOOK and SKIPPED alike. A recorded
    SKIPPED is engagement - it is the user reading the idea and rejecting it,
    which is the system working. Counting only TOOK would measure appetite.
    """

    briefings_sent: int
    briefings_answered: int

    def __post_init__(self) -> None:
        if self.briefings_answered > self.briefings_sent:
            raise ValueError(
                f"{self.briefings_answered} answered of {self.briefings_sent} sent is not possible"
            )

    @property
    def answered_fraction(self) -> float | None:
        if self.briefings_sent == 0:
            return None
        return self.briefings_answered / self.briefings_sent

    @property
    def ignored_fraction(self) -> float | None:
        answered = self.answered_fraction
        return None if answered is None else 1.0 - answered

    @property
    def fails_gate_two(self) -> bool:
        """More than 70% ignored - docs/06's stop criterion."""
        ignored = self.ignored_fraction
        return ignored is not None and ignored > MAX_IGNORED_FRACTION

    def describe(self) -> str:
        answered = self.answered_fraction
        if answered is None:
            return "engagement: no briefings sent yet"
        body = (
            f"engagement {answered:.0%} ({self.briefings_answered} of "
            f"{self.briefings_sent} briefings answered)"
        )
        if self.fails_gate_two:
            return f"{body} - over {MAX_IGNORED_FRACTION:.0%} ignored, docs/06 Gate 2"
        return body
