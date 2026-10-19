"""The briefing, built from a real cycle and rendered.

The tests that earn their place are about the size line. FR-6.2 wants a
hypothetical position size in every briefing and `docs/07` has no capital
figure, so the interesting behaviour is what the briefing says when it cannot
answer - and that it keeps saying it every morning instead of going quiet.
"""

from __future__ import annotations

from decimal import Decimal

from tests.agents.strategist.harness import Harness
from tests.fakes import FakeNotifier
from trading_desk.application.briefing import BriefingBuilder, deliver
from trading_desk.domain.briefing import Briefing
from trading_desk.domain.values import Money
from trading_desk.infra.risk_config import RiskConfig

NO_CAPITAL = RiskConfig(
    equity=None,
    max_symbol_exposure_fraction=0.05,
    max_portfolio_heat_fraction=0.025,
    daily_loss_limit_fraction=0.01,
    max_trades_per_day=3,
    verified=False,
    source="test",
)

WITH_CAPITAL = RiskConfig(
    equity=Money.from_rupees(Decimal("4200000")),
    max_symbol_exposure_fraction=0.05,
    max_portfolio_heat_fraction=0.025,
    daily_loss_limit_fraction=0.01,
    max_trades_per_day=3,
    verified=True,
    source="test",
)


def _briefing(risk: RiskConfig = NO_CAPITAL) -> tuple[Briefing, Harness]:
    harness = Harness()
    harness.queue_a_full_cycle()
    output = harness.cycle.run(harness.request())
    return BriefingBuilder(risk, harness.journal).build(output), harness


class TestTheFiveSections:
    def test_the_headline_carries_the_decision(self) -> None:
        briefing, _ = _briefing()
        text = briefing.headline()

        assert "RELIANCE" in text
        assert "Max loss/lot" in text
        assert "Wrong if:" in text

    def test_every_gate_value_is_in_the_detail(self) -> None:
        """docs/12: can you find every gate value, not just verdicts?"""
        briefing, _ = _briefing()
        detail = briefing.detail()

        for name in briefing.ideas[0].gate_values:
            assert name in detail

    def test_the_evidence_carries_source_and_timestamp(self) -> None:
        """FR-4.5: no claim without a source."""
        briefing, _ = _briefing()
        detail = briefing.detail()

        assert "Wire A" in detail
        assert "IST" in detail
        assert "https://" in detail

    def test_the_cost_section_exists(self) -> None:
        """FR-7.12: cost appears in every briefing."""
        briefing, _ = _briefing()

        assert "COST" in briefing.detail()

    def test_the_rejected_section_names_the_killing_gate(self) -> None:
        harness = Harness()
        harness.queue_a_full_cycle()
        request = harness.request()
        output = harness.cycle.run(
            request.__class__(
                cycle_id=request.cycle_id,
                as_of=request.as_of,
                universe=request.universe,
                articles=request.articles,
                iv_ranks={},
            )
        )
        briefing = BriefingBuilder(NO_CAPITAL, harness.journal).build(output)

        assert "iv_rank" in briefing.detail()
        assert not briefing.has_ideas


class TestTheHypotheticalSize:
    def test_without_a_capital_figure_the_line_says_why(self) -> None:
        briefing, _ = _briefing(NO_CAPITAL)

        size = briefing.ideas[0].hypothetical_size
        assert "unavailable" in size
        assert "ISS-022" in size

    def test_the_absence_is_in_the_message_you_read_not_only_the_detail(self) -> None:
        """A missing number mentioned daily gets filled in; one that quietly
        disappears does not."""
        briefing, _ = _briefing(NO_CAPITAL)

        assert "unavailable" in briefing.headline()

    def test_with_a_capital_figure_it_shows_lots_and_the_budget(self) -> None:
        briefing, _ = _briefing(WITH_CAPITAL)

        size = briefing.ideas[0].hypothetical_size
        assert "lot(s)" in size
        assert "budget" in size

    def test_a_structure_too_large_for_the_budget_reports_zero_lots(self) -> None:
        """ISS-022's arithmetic, seen from the briefing: below the floor the
        answer is zero, and the briefing says so rather than rounding up."""
        tiny = RiskConfig(
            equity=Money.from_rupees(Decimal("100000")),
            max_symbol_exposure_fraction=0.05,
            max_portfolio_heat_fraction=0.025,
            daily_loss_limit_fraction=0.01,
            max_trades_per_day=3,
            verified=True,
            source="test",
        )
        briefing, _ = _briefing(tiny)

        assert "0 lots" in briefing.ideas[0].hypothetical_size


class TestNoIdeasDay:
    def test_a_quiet_day_states_the_reason_in_the_first_message(self) -> None:
        harness = Harness()
        harness.queue_a_full_cycle()
        request = harness.request()
        output = harness.cycle.run(
            request.__class__(
                cycle_id=request.cycle_id,
                as_of=request.as_of,
                universe=request.universe,
                articles=request.articles,
                iv_ranks={},
            )
        )
        briefing = BriefingBuilder(NO_CAPITAL, harness.journal).build(output)

        headline = briefing.headline()
        assert "No ideas today." in headline
        assert "Why:" in headline


class TestDelivery:
    def test_the_decision_goes_first_then_the_question_then_the_detail(self) -> None:
        briefing, _ = _briefing()
        notifier = FakeNotifier()

        deliver(briefing, notifier)

        assert len(notifier.briefings) == 2
        assert "BRIEFING" in notifier.briefings[0]
        assert "all gate values" in notifier.briefings[1]
        assert len(notifier.decisions_asked) == 1

    def test_each_idea_is_asked_about_by_its_own_id(self) -> None:
        briefing, _ = _briefing()
        notifier = FakeNotifier()

        deliver(briefing, notifier)

        asked_id, text = notifier.decisions_asked[0]
        assert asked_id == briefing.ideas[0].idea_id
        assert "RELIANCE" in text

    def test_a_failed_send_raises_rather_than_reporting_success(self) -> None:
        """Rule 6: 'could not reach Telegram' and 'nothing to say' must never
        look the same from the outside."""
        briefing, _ = _briefing()
        notifier = FakeNotifier()
        notifier.fail_next_send = True

        try:
            deliver(briefing, notifier)
        except ConnectionError:
            pass
        else:
            raise AssertionError("a failed delivery was reported as success")

    def test_the_missing_inputs_reach_the_reader(self) -> None:
        briefing, _ = _briefing()

        assert "INPUTS THIS CYCLE DID NOT HAVE" in briefing.detail()
        assert "ban list" in briefing.detail()
