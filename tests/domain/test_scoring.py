"""Exit rules and P&L, with the arithmetic checked by hand.

The test this module exists for is
`test_a_credit_spread_scored_by_the_debit_rules_would_be_wrong`: the two
rulebooks are opposite signs of the same idea, and a single formula with a flag
is how one quietly gets applied to the other.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trading_desk.domain.costs import CostRates
from trading_desk.domain.proposals import LegAction
from trading_desk.domain.scoring import (
    CreditExitRules,
    DebitExitRules,
    Resolution,
    ResolutionBasis,
    ScoredLeg,
    credit_exit,
    debit_exit,
    score,
    spread_state,
)
from trading_desk.domain.values import Money, Price

# A 1400/1440 bull call spread: Rs 40 wide, bought for Rs 20.
LONG_STRIKE = Price.from_rupees(Decimal("1400"))
SHORT_STRIKE = Price.from_rupees(Decimal("1440"))
STRIKES = (LONG_STRIKE, SHORT_STRIKE)

DEBIT_RULES = DebitExitRules(
    target_fraction_of_width=Decimal("0.60"),
    stop_fraction_of_debit_lost=Decimal("0.50"),
    time_stop_days_to_expiry=5,
)
CREDIT_RULES = CreditExitRules(
    target_fraction_of_credit_captured=Decimal("0.50"),
    stop_multiple_of_credit=Decimal("2.0"),
)

# Zero-rate costs, so a P&L test measures the P&L and not the cost model.
# `test_costs_are_charged_on_the_exit_premium` uses real-shaped rates.
FREE = CostRates(
    brokerage_per_order=Money.zero(),
    stt_sell_fraction=Decimal(0),
    exchange_txn_fraction=Decimal(0),
    sebi_fraction=Decimal(0),
    stamp_duty_buy_fraction=Decimal(0),
    gst_fraction=Decimal(0),
    slippage_fraction=Decimal(0),
)


def debit_legs(long_now: str, short_now: str) -> tuple[ScoredLeg, ...]:
    """Long 1400 bought at Rs 50, short 1440 sold at Rs 30. Net debit Rs 20."""
    return (
        ScoredLeg(
            LegAction.BUY,
            Money.from_rupees(Decimal("50")),
            Money.from_rupees(Decimal(long_now)),
        ),
        ScoredLeg(
            LegAction.SELL,
            Money.from_rupees(Decimal("30")),
            Money.from_rupees(Decimal(short_now)),
        ),
    )


def credit_legs(short_now: str, long_now: str) -> tuple[ScoredLeg, ...]:
    """Short 1400 sold at Rs 50, long 1440 bought at Rs 30. Net credit Rs 20."""
    return (
        ScoredLeg(
            LegAction.SELL,
            Money.from_rupees(Decimal("50")),
            Money.from_rupees(Decimal(short_now)),
        ),
        ScoredLeg(
            LegAction.BUY,
            Money.from_rupees(Decimal("30")),
            Money.from_rupees(Decimal(long_now)),
        ),
    )


class TestTheSpreadState:
    def test_a_bought_spread_reads_as_a_debit(self) -> None:
        state = spread_state(debit_legs("50", "30"), STRIKES)
        assert state.is_debit
        assert state.net_entry == Money.from_rupees(Decimal("20"))
        assert state.width == Money.from_rupees(Decimal("40"))

    def test_a_sold_spread_reads_as_a_credit(self) -> None:
        state = spread_state(credit_legs("50", "30"), STRIKES)
        assert not state.is_debit
        assert state.net_entry.paise == -Money.from_rupees(Decimal("20")).paise

    def test_one_strike_has_no_width_and_is_refused(self) -> None:
        """A fraction-of-width target against a width of zero fires instantly."""
        with pytest.raises(ValueError, match="strikes to have a width"):
            spread_state(debit_legs("50", "30"), (LONG_STRIKE,))


class TestTheDebitRulebook:
    def test_the_target_is_a_fraction_of_width_not_of_the_debit(self) -> None:
        """60% of the Rs 40 width is Rs 24 gained. The spread is now worth
        Rs 44, so Rs 24 is exactly on the line."""
        state = spread_state(debit_legs("70", "26"), STRIKES)  # net 44, gained 24
        test = debit_exit(state, DEBIT_RULES, days_to_expiry=20)
        assert test.resolution is Resolution.TARGET
        assert "Rs 24.00 gained" in test.measured
        assert "Rs 24.00" in test.threshold

    def test_the_stop_is_half_the_debit_paid(self) -> None:
        state = spread_state(debit_legs("40", "30"), STRIKES)  # net 10, lost 10 of 20
        test = debit_exit(state, DEBIT_RULES, days_to_expiry=20)
        assert test.resolution is Resolution.STOP
        assert "Rs 10.00 of a Rs 20.00 debit lost" in test.measured

    def test_the_time_stop_counts_days_to_expiry_not_days_held(self) -> None:
        state = spread_state(debit_legs("52", "30"), STRIKES)
        assert debit_exit(state, DEBIT_RULES, days_to_expiry=6).resolution is None
        assert debit_exit(state, DEBIT_RULES, days_to_expiry=5).resolution is Resolution.TIME_STOP

    def test_a_spread_at_its_target_on_its_time_stop_day_resolves_as_the_target(self) -> None:
        """Order matters. Calling this a time stop would under-report the
        setup's hit rate for ever."""
        state = spread_state(debit_legs("70", "26"), STRIKES)
        assert debit_exit(state, DEBIT_RULES, days_to_expiry=1).resolution is Resolution.TARGET

    def test_an_open_spread_still_reports_both_thresholds(self) -> None:
        state = spread_state(debit_legs("52", "30"), STRIKES)
        test = debit_exit(state, DEBIT_RULES, days_to_expiry=20)
        assert test.resolution is None
        assert "target Rs 24.00" in test.threshold
        assert "stop Rs 10.00" in test.threshold

    def test_a_credit_structure_is_refused_by_the_debit_rulebook(self) -> None:
        state = spread_state(credit_legs("50", "30"), STRIKES)
        with pytest.raises(ValueError, match="must have cost something"):
            debit_exit(state, DEBIT_RULES, days_to_expiry=20)


class TestTheCreditRulebook:
    def test_the_target_is_half_the_credit_captured(self) -> None:
        """Rs 20 received; closing now costs Rs 10, so Rs 10 is captured."""
        state = spread_state(credit_legs("35", "25"), STRIKES)  # owes 10
        test = credit_exit(state, CREDIT_RULES)
        assert test.resolution is Resolution.TARGET
        assert "Rs 10.00 of a Rs 20.00 credit captured" in test.measured

    def test_the_stop_is_twice_the_credit_received(self) -> None:
        state = spread_state(credit_legs("75", "35"), STRIKES)  # owes 40 = 2 x 20
        test = credit_exit(state, CREDIT_RULES)
        assert test.resolution is Resolution.STOP
        assert "Rs 40.00 to close" in test.measured

    def test_there_is_no_time_stop_on_a_credit_spread(self) -> None:
        """Inventing one would resolve trades the rulebook says are open."""
        state = spread_state(credit_legs("45", "30"), STRIKES)
        assert credit_exit(state, CREDIT_RULES).resolution is None

    def test_a_debit_structure_is_refused_by_the_credit_rulebook(self) -> None:
        state = spread_state(debit_legs("50", "30"), STRIKES)
        with pytest.raises(ValueError, match="must have paid you something"):
            credit_exit(state, CREDIT_RULES)


def test_a_credit_spread_scored_by_the_debit_rules_would_be_wrong() -> None:
    """The bug the two separate rulebooks exist to prevent.

    A credit spread that has captured half its credit is a *winner*. Run it
    through the debit arithmetic and the same position reads as having lost
    money, because a credit spread's net entry is negative and "the debit lost"
    is computed from it. The two functions must refuse each other's structures
    rather than return a number.
    """
    winning_credit = spread_state(credit_legs("35", "25"), STRIKES)

    assert credit_exit(winning_credit, CREDIT_RULES).resolution is Resolution.TARGET
    with pytest.raises(ValueError):
        debit_exit(winning_credit, DEBIT_RULES, days_to_expiry=20)


class TestTheScore:
    def test_gross_is_the_move_times_the_quantity(self) -> None:
        legs = debit_legs("70", "26")  # Rs 20 paid, Rs 44 now: Rs 24 per contract
        result = score(
            legs=legs,
            strikes=STRIKES,
            resolution=Resolution.TARGET,
            basis=ResolutionBasis.SNAPSHOT,
            exit_test=debit_exit(spread_state(legs, STRIKES), DEBIT_RULES, days_to_expiry=20),
            quantity=50,
            rates=FREE,
        )
        assert result.gross == Money.from_rupees(Decimal("1200"))
        assert result.costs == Money.zero()
        assert result.net == result.gross

    def test_a_losing_trade_nets_negative(self) -> None:
        legs = debit_legs("40", "30")
        result = score(
            legs=legs,
            strikes=STRIKES,
            resolution=Resolution.STOP,
            basis=ResolutionBasis.SNAPSHOT,
            exit_test=debit_exit(spread_state(legs, STRIKES), DEBIT_RULES, days_to_expiry=20),
            quantity=1,
            rates=FREE,
        )
        assert result.net.paise < 0

    def test_costs_are_charged_on_the_exit_premium_not_the_entry(self) -> None:
        """A winning debit spread exits at a higher premium, so its turnover-
        linked charges are larger than the entry's. Reusing the entry premium
        understates the cost of exactly the trades that look best.

        Measured: the same structure scored with a large exit premium must cost
        more than one scored with a small exit premium, all else equal.
        """
        rates = CostRates(
            brokerage_per_order=Money.zero(),
            stt_sell_fraction=Decimal("0.001"),
            exchange_txn_fraction=Decimal("0.0005"),
            sebi_fraction=Decimal(0),
            stamp_duty_buy_fraction=Decimal(0),
            gst_fraction=Decimal(0),
            slippage_fraction=Decimal(0),
        )

        def costs_for(long_now: str, short_now: str) -> int:
            legs = debit_legs(long_now, short_now)
            return score(
                legs=legs,
                strikes=STRIKES,
                resolution=Resolution.TARGET,
                basis=ResolutionBasis.SNAPSHOT,
                exit_test=debit_exit(spread_state(legs, STRIKES), DEBIT_RULES, days_to_expiry=20),
                quantity=100,
                rates=rates,
            ).costs.paise

        assert costs_for("70", "26") > costs_for("40", "30")

    def test_the_line_shows_gross_costs_and_net_separately(self) -> None:
        """A net number alone cannot answer whether the strategy survives its
        own costs, which is the point of FR-7.6."""
        legs = debit_legs("70", "26")
        line = score(
            legs=legs,
            strikes=STRIKES,
            resolution=Resolution.TARGET,
            basis=ResolutionBasis.SNAPSHOT,
            exit_test=debit_exit(spread_state(legs, STRIKES), DEBIT_RULES, days_to_expiry=20),
            quantity=1,
            rates=FREE,
        ).as_line()
        assert "gross" in line and "costs" in line and "net" in line
        assert "snapshot" in line

    def test_a_zero_quantity_is_refused(self) -> None:
        with pytest.raises(ValueError, match="quantity must be positive"):
            score(
                legs=debit_legs("70", "26"),
                strikes=STRIKES,
                resolution=Resolution.TARGET,
                basis=ResolutionBasis.SNAPSHOT,
                exit_test=debit_exit(
                    spread_state(debit_legs("70", "26"), STRIKES),
                    DEBIT_RULES,
                    days_to_expiry=20,
                ),
                quantity=0,
                rates=FREE,
            )


class TestRuleShapes:
    def test_a_fraction_over_one_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at most 1"):
            DebitExitRules(Decimal("1.5"), Decimal("0.5"), 5)

    def test_a_negative_time_stop_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must not be negative"):
            DebitExitRules(Decimal("0.6"), Decimal("0.5"), -1)

    def test_a_negative_premium_is_refused(self) -> None:
        with pytest.raises(ValueError, match="cannot be negative"):
            ScoredLeg(LegAction.BUY, Money(paise=-1), Money(paise=100))
