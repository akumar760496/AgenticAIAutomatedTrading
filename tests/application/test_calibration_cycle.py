"""The calibration cycle: read the scored outcomes, measure, record, report."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from tests.fakes import FakeJournal, FakeNotifier, FrozenClock
from trading_desk.application.calibration import ALL_SCOPE, CalibrationCycle, deliver
from trading_desk.domain.enums import Direction
from trading_desk.domain.values import Money
from trading_desk.ports.journal import RecordedIdea, RecordedOutcome

NOW = datetime(2026, 10, 19, 10, 30, tzinfo=UTC)
BRIEFED_AT = datetime(2026, 10, 16, 2, 45, tzinfo=UTC)


def record(
    journal: FakeJournal,
    *,
    confidence: float,
    net_rupees: str,
    setup_id: str = "SETUP_A",
    used_proxy: bool = False,
) -> None:
    """One briefed idea with one resolved outcome."""
    idea = RecordedIdea(
        idea_id=uuid4(),
        cycle_id=uuid4(),
        symbol="RELIANCE",
        setup_id=setup_id,
        direction=Direction.BULLISH,
        rank=1,
        confidence=confidence,
        gate_values={},
        hypothetical_risk=Money.from_rupees("2000"),
        created_at=BRIEFED_AT,
        used_iv_proxy=used_proxy,
    )
    journal.record_idea(idea)
    journal.record_outcome(
        RecordedOutcome(
            idea_id=idea.idea_id,
            resolution="TARGET" if Money.from_rupees(net_rupees).paise > 0 else "STOP",
            hypothetical_pnl=Money.from_rupees(net_rupees),
            actual_pnl=None,
            resolved_at=NOW,
        )
    )


def cycle(journal: FakeJournal) -> CalibrationCycle:
    return CalibrationCycle(journal=journal, clock=FrozenClock(NOW))


class TestWithNothingResolved:
    def test_it_reports_the_absence_rather_than_a_perfect_score(self) -> None:
        """A Brier score of 0.0 is a flawless forecaster. No data must never
        come out looking like that (rule 6)."""
        report = cycle(FakeJournal()).run()

        assert report.scopes == ()
        assert report.overall is None
        assert "nothing to calibrate" in report.text()

    def test_the_sizing_lock_stays_shut_and_says_which_reason(self) -> None:
        report = cycle(FakeJournal()).run()
        assert not report.lock.is_open
        assert "not yet measurable" in report.lock.describe()

    def test_nothing_is_recorded(self) -> None:
        journal = FakeJournal()
        cycle(journal).run()
        assert journal.calibrations == []


class TestWithResolvedOutcomes:
    def test_success_is_read_from_net_money_not_the_resolution_label(self) -> None:
        """A TARGET that netted a loss after costs is not a success. The two
        genuinely disagree: costs are ~13% of a vertical's best case (ISS-023).
        """
        journal = FakeJournal()
        idea = RecordedIdea(
            idea_id=uuid4(),
            cycle_id=uuid4(),
            symbol="RELIANCE",
            setup_id="SETUP_A",
            direction=Direction.BULLISH,
            rank=1,
            confidence=0.9,
            gate_values={},
            hypothetical_risk=Money.from_rupees("2000"),
            created_at=BRIEFED_AT,
        )
        journal.record_idea(idea)
        journal.record_outcome(
            RecordedOutcome(
                idea_id=idea.idea_id,
                resolution="TARGET",  # the label says it worked
                hypothetical_pnl=Money.from_rupees("-50"),  # the money says otherwise
                actual_pnl=None,
                resolved_at=NOW,
            )
        )
        report = cycle(journal).run()

        overall = report.overall
        assert overall is not None
        assert overall.base_rate == 0.0  # scored as a failure

    def test_a_snapshot_is_recorded_for_all_and_for_each_setup(self) -> None:
        """A combined Brier score can hide one setup being useless while the
        other carries it."""
        journal = FakeJournal()
        record(journal, confidence=0.7, net_rupees="100", setup_id="SETUP_A")
        record(journal, confidence=0.7, net_rupees="-100", setup_id="SETUP_B")
        report = cycle(journal).run()

        scopes = {scoped.scope for scoped in report.scopes}
        assert scopes == {ALL_SCOPE, "SETUP_A", "SETUP_B"}
        assert {snapshot.scope for snapshot in journal.calibrations} == scopes

    def test_the_recorded_snapshot_carries_the_baseline_and_the_curve(self) -> None:
        journal = FakeJournal()
        for _ in range(7):
            record(journal, confidence=0.7, net_rupees="100")
        for _ in range(3):
            record(journal, confidence=0.7, net_rupees="-100")
        cycle(journal).run()

        snapshot = next(s for s in journal.calibrations if s.scope == ALL_SCOPE)
        assert snapshot.sample_count == 10
        assert snapshot.baseline_brier_score > 0
        assert snapshot.reliability_bins  # the curve, one row per populated band

    def test_the_lock_reads_the_combined_scope(self) -> None:
        """One setup beating the baseline over fifty of its own ideas is a
        different and later argument."""
        journal = FakeJournal()
        for index in range(30):
            record(journal, confidence=0.9, net_rupees="100" if index < 27 else "-100")
        for index in range(30):
            record(journal, confidence=0.3, net_rupees="100" if index < 9 else "-100")
        report = cycle(journal).run()

        assert report.lock.resolved_count == 60
        assert report.lock.is_open


class TestTheProxyWarning:
    def test_proxy_derived_ideas_are_counted_and_called_out(self) -> None:
        """Until ~six months of chain history accrues, most ideas' IV rank is
        proxy-derived. Mixing them in measures two systems and reports one."""
        journal = FakeJournal()
        record(journal, confidence=0.7, net_rupees="100", used_proxy=True)
        record(journal, confidence=0.7, net_rupees="100", used_proxy=False)
        report = cycle(journal).run()

        assert report.proxy_count == 1
        assert report.real_iv_count == 1
        text = report.text()
        assert "1 of 2 resolved" in text
        assert "ISS-002" in text
        assert "provisional" in text

    def test_no_warning_when_nothing_used_the_proxy(self) -> None:
        journal = FakeJournal()
        record(journal, confidence=0.7, net_rupees="100", used_proxy=False)
        assert "ISS-002" not in cycle(journal).run().text()


class TestTheReport:
    def test_it_names_the_sizing_lock_state(self) -> None:
        journal = FakeJournal()
        record(journal, confidence=0.7, net_rupees="100")
        assert "Sizing lock:" in cycle(journal).run().text()

    def test_it_prints_the_reliability_curve_for_populated_bands_only(self) -> None:
        journal = FakeJournal()
        for _ in range(6):
            record(journal, confidence=0.75, net_rupees="100")
        text = cycle(journal).run().text()

        assert "Reliability curve" in text
        assert "70%-80%" in text
        assert "empty" not in text

    def test_it_is_delivered_as_a_briefing_not_an_alert(self) -> None:
        journal = FakeJournal()
        record(journal, confidence=0.7, net_rupees="100")
        notifier = FakeNotifier()
        deliver(cycle(journal).run(), notifier)

        assert len(notifier.briefings) == 1
        assert notifier.alerts == []
        assert "CALIBRATION" in notifier.briefings[0]
