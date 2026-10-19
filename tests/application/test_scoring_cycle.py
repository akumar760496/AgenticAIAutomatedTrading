"""The 16:00 scoring cycle.

The tests that earn their keep are the refusals. `outcomes` is append-only, so
a row written from a guessed premium is permanent and indistinguishable from a
measured one - which would poison the dataset the whole project exists to
build. Every refusal below must leave the journal untouched and say why.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from tests.fakes import FakeChainArchive, FakeJournal, FakeNotifier, FrozenClock
from trading_desk.application.scoring import ScoringCycle, deliver
from trading_desk.domain.costs import CostConfig, CostRates
from trading_desk.domain.enums import Direction, Exchange, OptionRight, Segment
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.market import OptionChainSnapshot, OptionQuote, OptionStrike
from trading_desk.domain.proposals import LegAction
from trading_desk.domain.scoring import CreditExitRules, DebitExitRules, Resolution
from trading_desk.domain.setups.thresholds import (
    DeltaBand,
    ExpiryWindow,
    LiquidityLimits,
    SetupAThresholds,
    SetupBThresholds,
    SetupThresholds,
)
from trading_desk.domain.values import Money, Price
from trading_desk.ports.journal import RecordedIdea, RecordedLeg, RecordedOutcome

# 16:00 IST on Monday 19 October 2026.
NOW = datetime(2026, 10, 19, 10, 30, tzinfo=UTC)
TODAY = date(2026, 10, 19)
BRIEFED_AT = datetime(2026, 10, 16, 2, 45, tzinfo=UTC)
EXPIRY = date(2026, 11, 26)

LONG_STRIKE = Price.from_rupees("1400")
SHORT_STRIKE = Price.from_rupees("1440")

RELIANCE = InstrumentRef(
    symbol="RELIANCE",
    security_id="2885",
    exchange=Exchange.NSE,
    segment=Segment.EQUITY,
)

FREE_RATES = CostRates(
    brokerage_per_order=Money.zero(),
    stt_sell_fraction=Decimal(0),
    exchange_txn_fraction=Decimal(0),
    sebi_fraction=Decimal(0),
    stamp_duty_buy_fraction=Decimal(0),
    gst_fraction=Decimal(0),
    slippage_fraction=Decimal(0),
)
COSTS = CostConfig(rates=FREE_RATES, verified=False, source="test")


def thresholds() -> SetupThresholds:
    """Only the exit rules matter here; the gate numbers are never read."""
    band = DeltaBand(0.5, 0.55)
    window = ExpiryWindow(10, 25)
    debit = DebitExitRules(Decimal("0.60"), Decimal("0.50"), 5)
    return SetupThresholds(
        setup_a=SetupAThresholds(
            max_sessions_since_catalyst=2,
            catalyst_types=frozenset({"EARNINGS_SURPRISE"}),
            min_credibility=0.7,
            max_move_in_atr=1.5,
            min_volume_ratio=1.5,
            max_iv_rank=60.0,
            liquidity=LiquidityLimits(500, 0.03),
            expiry=window,
            long_leg_delta=band,
            short_leg_delta=band,
            exits=debit,
        ),
        setup_b=SetupBThresholds(
            credit_regime_min_iv_rank=70.0,
            debit_regime_max_iv_rank=35.0,
            credit_delta=band,
            credit_expiry=window,
            debit_expiry=window,
            credit_exits=CreditExitRules(Decimal("0.50"), Decimal("2.0")),
            debit_exits=DebitExitRules(Decimal("0.60"), Decimal("0.50"), 1),
            flat_by_sessions_before_expiry=1,
            max_concurrent_index_positions=1,
            blocking_event_types=frozenset({"RBI_POLICY"}),
        ),
        allow_naked_shorts=False,
        verified=False,
        source="test",
    )


def debit_legs(expiry: date = EXPIRY) -> tuple[RecordedLeg, ...]:
    """Long 1400 at Rs 50, short 1440 at Rs 30. A Rs 20 debit on a Rs 40 width.

    `expiry` is a parameter because the legs and the idea must agree: in
    production `RecordedIdea.expiry` is `min(leg.expiry)`, so a fixture where
    they differ is testing a state the system cannot produce.
    """
    return (
        RecordedLeg(
            right=OptionRight.CALL,
            action=LegAction.BUY,
            strike=LONG_STRIKE,
            expiry=expiry,
            entry_premium=Money.from_rupees("50"),
        ),
        RecordedLeg(
            right=OptionRight.CALL,
            action=LegAction.SELL,
            strike=SHORT_STRIKE,
            expiry=expiry,
            entry_premium=Money.from_rupees("30"),
        ),
    )


def credit_legs() -> tuple[RecordedLeg, ...]:
    """Short 1400 at Rs 50, long 1440 at Rs 30. A Rs 20 credit."""
    return (
        RecordedLeg(
            right=OptionRight.CALL,
            action=LegAction.SELL,
            strike=LONG_STRIKE,
            expiry=EXPIRY,
            entry_premium=Money.from_rupees("50"),
        ),
        RecordedLeg(
            right=OptionRight.CALL,
            action=LegAction.BUY,
            strike=SHORT_STRIKE,
            expiry=EXPIRY,
            entry_premium=Money.from_rupees("30"),
        ),
    )


def idea(
    *,
    legs: tuple[RecordedLeg, ...] | None = None,
    setup_id: str = "SETUP_A",
    expiry: date | None = EXPIRY,
) -> RecordedIdea:
    return RecordedIdea(
        idea_id=uuid4(),
        cycle_id=uuid4(),
        symbol="RELIANCE",
        setup_id=setup_id,
        direction=Direction.BULLISH,
        rank=1,
        confidence=0.65,
        gate_values={"iv_rank": "40.0 (proxy)"},
        hypothetical_risk=Money.from_rupees("2000"),
        created_at=BRIEFED_AT,
        stop_level=Price.from_rupees("1400"),
        target_level=Price.from_rupees("1440"),
        expiry=expiry,
        legs=debit_legs() if legs is None else legs,
    )


def quote(premium: str) -> OptionQuote:
    """A two-sided quote whose mid is `premium`."""
    mid = Money.from_rupees(premium).paise
    return OptionQuote(
        right=OptionRight.CALL,
        last=Price(paise=mid),
        bid=Price(paise=mid - 50),
        ask=Price(paise=mid + 50),
        volume=1000,
        open_interest=5000,
    )


def archive_with(*, long_now: str, short_now: str, on: date = TODAY) -> FakeChainArchive:
    archive = FakeChainArchive()
    archive.write(
        OptionChainSnapshot(
            underlying=RELIANCE,
            expiry_date=EXPIRY.isoformat(),
            strikes=(
                OptionStrike(strike=LONG_STRIKE, call=quote(long_now), put=None),
                OptionStrike(strike=SHORT_STRIKE, call=quote(short_now), put=None),
            ),
            spot=Price.from_rupees("1430"),
            exchange_time=datetime(on.year, on.month, on.day, 9, 45, tzinfo=UTC),
            received_at=datetime(on.year, on.month, on.day, 9, 45, tzinfo=UTC),
        )
    )
    return archive


def cycle(journal: FakeJournal, archive: FakeChainArchive) -> ScoringCycle:
    return ScoringCycle(
        journal=journal,
        chains=archive,
        clock=FrozenClock(NOW),
        thresholds=thresholds(),
        costs=COSTS,
    )


class TestScoringAnIdeaThatResolved:
    def test_a_debit_spread_at_its_target_is_scored_and_recorded(self) -> None:
        journal = FakeJournal()
        journal.record_idea(idea())
        # Long now Rs 70, short now Rs 26: net Rs 44, so Rs 24 gained on a
        # Rs 40 width - exactly the 60% target.
        report = cycle(journal, archive_with(long_now="70", short_now="26")).run()

        assert len(report.scored) == 1
        assert report.scored[0].result.resolution is Resolution.TARGET
        assert report.scored[0].result.gross == Money.from_rupees("24")
        assert len(journal.outcomes) == 1
        assert journal.outcomes[0].resolution == "TARGET"

    def test_the_recorded_row_keeps_gross_costs_and_the_threshold(self) -> None:
        """Rule 9 and FR-7.6: a net number alone cannot answer whether the
        strategy survives its own costs, or why it resolved."""
        journal = FakeJournal()
        journal.record_idea(idea())
        cycle(journal, archive_with(long_now="70", short_now="26")).run()

        recorded = journal.outcomes[0]
        assert recorded.gross_pnl is not None
        assert recorded.costs is not None
        assert "Rs 24.00 gained" in recorded.measured
        assert "of width" in recorded.threshold
        assert recorded.basis == "SNAPSHOT"

    def test_actual_pnl_is_none_rather_than_zero(self) -> None:
        """Zero would claim the user took the trade and broke even."""
        journal = FakeJournal()
        journal.record_idea(idea())
        cycle(journal, archive_with(long_now="70", short_now="26")).run()
        assert journal.outcomes[0].actual_pnl is None

    def test_a_credit_spread_is_scored_by_the_credit_rulebook(self) -> None:
        """The variant is read from the legs, not from a label, so a
        mislabelled idea is still scored by the arithmetic that applies.

        Rs 20 received, closing now costs Rs 10, so Rs 10 is captured - the 50%
        target. Under the debit rules this same position would raise.
        """
        journal = FakeJournal()
        journal.record_idea(idea(legs=credit_legs(), setup_id="SETUP_B"))
        report = cycle(journal, archive_with(long_now="25", short_now="35")).run()

        assert len(report.scored) == 1
        assert report.scored[0].result.resolution is Resolution.TARGET


class TestScoringAnIdeaThatHasNotResolved:
    def test_an_open_spread_is_counted_as_open_and_not_recorded(self) -> None:
        journal = FakeJournal()
        journal.record_idea(idea())
        report = cycle(journal, archive_with(long_now="52", short_now="30")).run()

        assert report.scored == ()
        assert report.still_open == 1
        assert journal.outcomes == []

    def test_an_already_resolved_idea_is_not_scored_twice(self) -> None:
        journal = FakeJournal()
        existing = idea()
        journal.record_idea(existing)
        journal.record_outcome(
            RecordedOutcome(
                idea_id=existing.idea_id,
                resolution="TARGET",
                hypothetical_pnl=Money.from_rupees("100"),
                actual_pnl=None,
                resolved_at=NOW,
            )
        )
        report = cycle(journal, archive_with(long_now="70", short_now="26")).run()

        assert report.scored == ()
        assert len(journal.outcomes) == 1


class TestTheRefusals:
    """Each of these must write nothing and state its reason."""

    def test_an_idea_with_no_legs_is_reported_unscoreable(self) -> None:
        """Every idea briefed before migration 0009."""
        journal = FakeJournal()
        journal.record_idea(idea(legs=()))
        report = cycle(journal, archive_with(long_now="70", short_now="26")).run()

        assert journal.outcomes == []
        assert len(report.unscoreable) == 1
        assert "no legs recorded" in report.unscoreable[0].reason
        assert "0009" in report.unscoreable[0].reason

    def test_a_missing_exit_premium_refuses_rather_than_guessing(self) -> None:
        """The archive has no snapshot for today. A guessed premium would write
        a permanent, wrong, append-only row (rule 6)."""
        journal = FakeJournal()
        journal.record_idea(idea())
        stale = archive_with(long_now="70", short_now="26", on=date(2026, 10, 16))
        report = cycle(journal, stale).run()

        assert journal.outcomes == []
        assert len(report.unscoreable) == 1
        assert "no archived premium" in report.unscoreable[0].reason

    def test_a_premium_for_only_one_leg_refuses_the_whole_idea(self) -> None:
        """A vertical priced on one leg is a different trade, wrong by the
        width of the spread rather than by a rounding error."""
        journal = FakeJournal()
        journal.record_idea(idea())
        archive = FakeChainArchive()
        archive.write(
            OptionChainSnapshot(
                underlying=RELIANCE,
                expiry_date=EXPIRY.isoformat(),
                strikes=(OptionStrike(strike=LONG_STRIKE, call=quote("70"), put=None),),
                spot=Price.from_rupees("1430"),
                exchange_time=NOW,
                received_at=NOW,
            )
        )
        report = cycle(journal, archive).run()

        assert journal.outcomes == []
        assert "no archived premium" in report.unscoreable[0].reason

    def test_an_idea_with_no_expiry_is_reported_unscoreable(self) -> None:
        journal = FakeJournal()
        journal.record_idea(idea(expiry=None))
        report = cycle(journal, archive_with(long_now="70", short_now="26")).run()

        assert journal.outcomes == []
        assert "no expiry recorded" in report.unscoreable[0].reason

    def test_an_unknown_setup_is_reported_rather_than_scored_by_a_default(self) -> None:
        journal = FakeJournal()
        journal.record_idea(idea(setup_id="SETUP_Z"))
        report = cycle(journal, archive_with(long_now="70", short_now="26")).run()

        assert journal.outcomes == []
        assert "no exit rules for setup SETUP_Z" in report.unscoreable[0].reason

    def test_a_single_leg_idea_is_refused_because_it_has_no_width(self) -> None:
        journal = FakeJournal()
        journal.record_idea(idea(legs=debit_legs()[:1]))
        report = cycle(journal, archive_with(long_now="70", short_now="26")).run()

        assert journal.outcomes == []
        assert "needs a width" in report.unscoreable[0].reason


class TestExpiry:
    def test_an_idea_past_its_expiry_resolves_on_the_expiry_basis(self) -> None:
        """Past expiry with no rule fired, the position is over regardless, and
        `EXPIRY` records that this - not a premium comparison - is why."""
        journal = FakeJournal()
        past = date(2026, 10, 15)
        journal.record_idea(idea(legs=debit_legs(past), expiry=past))
        archive = FakeChainArchive()
        archive.write(
            OptionChainSnapshot(
                underlying=RELIANCE,
                expiry_date=past.isoformat(),
                strikes=(
                    OptionStrike(strike=LONG_STRIKE, call=quote("52"), put=None),
                    OptionStrike(strike=SHORT_STRIKE, call=quote("30"), put=None),
                ),
                spot=Price.from_rupees("1430"),
                exchange_time=NOW,
                received_at=NOW,
            )
        )
        report = cycle(journal, archive).run()

        assert len(report.scored) == 1
        assert journal.outcomes[0].basis == "EXPIRY"
        assert "past the" in journal.outcomes[0].measured


class TestTheReport:
    def test_a_day_with_nothing_to_score_says_so_rather_than_being_silent(self) -> None:
        report = cycle(FakeJournal(), FakeChainArchive()).run()
        assert "Nothing resolved today" in report.text()

    def test_the_report_states_that_the_numbers_are_hypothetical_and_snapshot_based(
        self,
    ) -> None:
        """A reader must not take these for realised P&L at an intraday fill."""
        journal = FakeJournal()
        journal.record_idea(idea())
        report = cycle(journal, archive_with(long_now="70", short_now="26")).run()

        text = report.text()
        assert "Hypothetical" in text
        assert "not from an intraday fill" in text

    def test_refusals_appear_in_the_report_rather_than_only_in_a_log(self) -> None:
        journal = FakeJournal()
        journal.record_idea(idea(legs=()))
        text = cycle(journal, archive_with(long_now="70", short_now="26")).run().text()

        assert "Not scored:" in text
        assert "no legs recorded" in text

    def test_the_net_total_is_the_sum_of_what_was_scored(self) -> None:
        journal = FakeJournal()
        journal.record_idea(idea())
        journal.record_idea(idea())
        report = cycle(journal, archive_with(long_now="70", short_now="26")).run()

        assert len(report.scored) == 2
        assert report.net_total == Money.from_rupees("48")


class TestDelivery:
    def test_the_report_is_delivered(self) -> None:
        journal = FakeJournal()
        journal.record_idea(idea())
        notifier = FakeNotifier()
        report = cycle(journal, archive_with(long_now="70", short_now="26")).run()

        deliver(report, notifier)
        assert len(notifier.briefings) == 1
        assert "SCORING" in notifier.briefings[0]
