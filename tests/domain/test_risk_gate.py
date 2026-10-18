"""The risk gate and sizing. Property tests, then the four fail-closed cases.

`docs/10` task 3 asks for properties, not examples: size is never negative,
never exceeds the per-trade cap for any input, never rounds up through a lot
boundary, and never approves with stale data. Examples prove a case; these
prove the rule.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, time
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import trading_desk.domain.risk.gate as gate_module
from trading_desk.domain.enums import Verdict
from trading_desk.domain.gates import GateResult
from trading_desk.domain.risk.gate import (
    CHECKS,
    MAX_CONSECUTIVE_REJECTS,
    RiskInputs,
    data_freshness,
    evaluate,
)
from trading_desk.domain.risk.sizing import DEFAULT_RISK_FRACTION, position_size
from trading_desk.domain.values import Money

EXPECTED_CHECKS = 13
SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)
NOW = datetime(2026, 10, 19, 11, 0, tzinfo=UTC)


def _healthy(**overrides: object) -> RiskInputs:
    base = {
        "instrument_symbol": "RELIANCE",
        "now": NOW,
        "session_open": SESSION_OPEN,
        "session_close": SESSION_CLOSE,
        "tick_age_seconds": 0.5,
        "chain_age_seconds": 5.0,
        "feed_connected": True,
        "in_approved_universe": True,
        "max_loss_per_lot": Money.from_rupees(Decimal("10")),
        "is_defined_risk": True,
        "has_naked_short": False,
        "open_interest_lots": 1200,
        "spread_fraction": 0.02,
        "size": position_size(
            Money.from_rupees(Decimal("2000000")), Money.from_rupees(Decimal("10")), 250
        ),
        "equity": Money.from_rupees(Decimal("2000000")),
        "max_symbol_exposure": Money.from_rupees(Decimal("100000")),
        "symbol_exposure": Money.from_rupees(Decimal("10000")),
        "max_portfolio_heat": Money.from_rupees(Decimal("50000")),
        "portfolio_heat": Money.from_rupees(Decimal("5000")),
        "max_net_delta": 100.0,
        "max_short_vega": 500.0,
        "daily_loss_limit": Money.from_rupees(Decimal("20000")),
        "max_trades_per_day": 5,
        "available_margin": Money.from_rupees(Decimal("500000")),
        "required_margin": Money.from_rupees(Decimal("50000")),
        "min_minutes_between_entries": 30.0,
    }
    base.update(overrides)
    return RiskInputs(**base)  # type: ignore[arg-type]


class TestSizingProperties:
    @given(
        equity=st.integers(min_value=0, max_value=10**11),
        loss=st.integers(min_value=1, max_value=10**8),
        lot=st.integers(min_value=1, max_value=10_000),
    )
    @settings(max_examples=300)
    def test_lots_are_never_negative(self, equity: int, loss: int, lot: int) -> None:
        size = position_size(Money(paise=equity), Money(paise=loss), lot)
        assert size.lots >= 0

    @given(
        equity=st.integers(min_value=0, max_value=10**11),
        loss=st.integers(min_value=1, max_value=10**8),
        lot=st.integers(min_value=1, max_value=10_000),
    )
    @settings(max_examples=500)
    def test_total_risk_never_exceeds_the_budget(self, equity: int, loss: int, lot: int) -> None:
        """The property that matters. For *any* equity, loss and lot size, the
        risk taken must fit inside 0.25% of equity."""
        size = position_size(Money(paise=equity), Money(paise=loss), lot)
        assert size.total_risk.paise <= size.risk_budget.paise

    @given(
        equity=st.integers(min_value=0, max_value=10**11),
        loss=st.integers(min_value=1, max_value=10**8),
        lot=st.integers(min_value=1, max_value=10_000),
    )
    @settings(max_examples=500)
    def test_one_more_lot_would_always_breach(self, equity: int, loss: int, lot: int) -> None:
        """Rounding is down, never nearest. If one more lot would still fit,
        the sizing left risk budget unused and the division is wrong."""
        size = position_size(Money(paise=equity), Money(paise=loss), lot)
        one_more = (size.lots + 1) * size.max_loss_per_lot.paise
        assert one_more > size.risk_budget.paise

    @given(equity=st.integers(min_value=0, max_value=10**11))
    @settings(max_examples=200)
    def test_the_budget_is_never_more_than_the_fraction_of_equity(self, equity: int) -> None:
        size = position_size(Money(paise=equity), Money(paise=1000), 1)
        assert Decimal(size.risk_budget.paise) <= Decimal(equity) * DEFAULT_RISK_FRACTION

    def test_a_lot_too_expensive_for_the_budget_returns_zero_with_a_reason(self) -> None:
        size = position_size(
            Money.from_rupees(Decimal("10000")), Money.from_rupees(Decimal("500")), 250
        )

        assert size.lots == 0
        assert not size.is_tradable
        assert "above the" in size.reason

    @pytest.mark.parametrize("bad", [0, -1])
    def test_a_non_positive_lot_size_raises(self, bad: int) -> None:
        with pytest.raises(ValueError, match="lot_size must be positive"):
            position_size(Money.from_rupees(Decimal("100000")), Money(paise=100), bad)


class TestNoConfidenceInput:
    def test_sizing_has_no_confidence_parameter(self) -> None:
        """FR-6.3 and FR-7.8 enforced by shape. Confidence-based sizing is
        blocked until calibration beats baseline over 50 resolved ideas, so
        the parameter must not exist to be passed."""
        parameters = set(inspect.signature(position_size).parameters)
        banned = {"confidence", "conviction", "score", "probability", "edge"}

        assert not (parameters & banned), f"sizing accepts {parameters & banned}"

    def test_the_risk_gate_takes_no_agent_output(self) -> None:
        """FR-6.7: no LLM may grant a risk exception. The gate cannot be
        argued with because it is never handed an argument."""
        fields = set(RiskInputs.__dataclass_fields__)
        banned = {"thesis", "rationale", "confidence", "agent_note", "override", "llm_reason"}

        assert not (fields & banned), f"RiskInputs accepts {fields & banned}"


class TestFailClosed:
    """FR-9.4's four conditions, each with its own test."""

    def test_stale_data_vetoes(self) -> None:
        decision = evaluate(_healthy(tick_age_seconds=30.0))

        assert not decision.approved
        assert "data_freshness" in decision.summary()

    def test_a_disconnected_feed_vetoes(self) -> None:
        decision = evaluate(_healthy(feed_connected=False))

        assert not decision.approved
        assert "data_freshness" in decision.summary()

    def test_an_unhandled_exception_vetoes_rather_than_escaping(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An error inside a check must not become an approval by escaping to
        a caller that reads "no exception" as "fine"."""

        def explode(_inputs: RiskInputs) -> GateResult:
            raise RuntimeError("simulated sensor failure")

        monkeypatch.setattr(gate_module, "CHECKS", (*CHECKS, explode))
        decision = evaluate(_healthy())

        assert not decision.approved
        assert decision.error is not None
        assert "RuntimeError" in decision.error

    def test_unknown_data_age_vetoes_just_like_stale_data(self) -> None:
        """Rule 6: "we do not know" is not "it is fine"."""
        result = data_freshness(_healthy(tick_age_seconds=None))

        assert result.verdict is Verdict.NO_DATA
        assert not evaluate(_healthy(chain_age_seconds=None)).approved


class TestTheThirteenChecks:
    def test_there_are_thirteen(self) -> None:
        assert len(CHECKS) == EXPECTED_CHECKS

    def test_a_healthy_proposal_is_approved_with_a_size(self) -> None:
        decision = evaluate(_healthy())

        assert decision.approved, decision.summary()
        assert decision.size is not None
        assert decision.size.lots > 0

    def test_every_check_reports_a_value_and_a_threshold(self) -> None:
        for result in evaluate(_healthy()).results:
            assert result.measured, f"{result.name} reported no measurement"
            assert result.threshold, f"{result.name} reported no threshold"

    @pytest.mark.parametrize(
        ("override", "expected_veto"),
        [
            ({"in_approved_universe": False}, "instrument_allowlist"),
            ({"has_naked_short": True}, "options_specific"),
            ({"is_defined_risk": False}, "options_specific"),
            ({"halted": True}, "circuit_breakers"),
            ({"reconciliation_mismatch": True}, "circuit_breakers"),
            ({"consecutive_rejects": MAX_CONSECUTIVE_REJECTS}, "circuit_breakers"),
            ({"proposal_already_seen": True}, "duplicate_or_churn"),
            ({"minutes_since_last_entry": 1.0}, "duplicate_or_churn"),
            ({"required_margin": Money.from_rupees(Decimal("900000"))}, "margin"),
            ({"trades_today": 5}, "daily_limits"),
            ({"realised_loss_today": Money.from_rupees(Decimal("20000"))}, "daily_limits"),
            ({"symbol_exposure": Money.from_rupees(Decimal("200000"))}, "concentration"),
            ({"portfolio_heat": Money.from_rupees(Decimal("90000"))}, "portfolio_risk"),
            ({"net_delta": 500.0}, "portfolio_risk"),
            ({"short_vega": 9000.0}, "portfolio_risk"),
            ({"now": datetime(2026, 10, 19, 3, 0, tzinfo=UTC)}, "market_state"),
        ],
    )
    def test_each_breach_is_vetoed_by_the_expected_check(
        self, override: dict[str, object], expected_veto: str
    ) -> None:
        """Every limit has a test that drives it to breach (FR-6.5)."""
        decision = evaluate(_healthy(**override))
        vetoed = [result.name for result in decision.vetoes()]

        assert not decision.approved
        assert expected_veto in vetoed, f"expected {expected_veto}, got {vetoed}"

    def test_the_gate_runs_advisory_and_reports_the_size_it_would_take(self) -> None:
        """FR-6.2. Nothing here places an order; the size is for the briefing."""
        decision = evaluate(_healthy())

        assert decision.size is not None
        assert "lot(s)" in decision.summary()


class TestMinimumViableCapital:
    """What flat 0.25% risk actually implies for account size.

    Discovered by writing a fixture that could not trade: a NIFTY debit spread
    with a 140-rupee max loss per unit and a 75 lot risks 10,500 rupees per
    lot, so 0.25% risk needs a 42 lakh account to take a single lot. Below
    that, the correct answer is zero lots - and the gate says so rather than
    rounding up into a breach.
    """

    def test_a_small_account_cannot_take_a_full_lot_and_says_so(self) -> None:
        size = position_size(
            Money.from_rupees(Decimal("1000000")),  # 10 lakh
            Money.from_rupees(Decimal("140")),  # per unit
            75,  # NIFTY lot
        )

        assert size.lots == 0
        assert size.risk_budget == Money.from_rupees(Decimal("2500"))
        assert size.max_loss_per_lot == Money.from_rupees(Decimal("10500"))
        assert "above the" in size.reason

    def test_the_equity_needed_for_one_lot_is_computable(self) -> None:
        """42 lakh for that spread. Not a limitation of the code - a fact
        about 0.25% risk and NSE lot sizes that the capital figure in
        docs/07 has to reckon with."""
        per_lot = Money.from_rupees(Decimal("10500"))
        needed = Money(paise=int(Decimal(per_lot.paise) / DEFAULT_RISK_FRACTION))

        size = position_size(needed, Money.from_rupees(Decimal("140")), 75)
        assert size.lots == 1
        assert needed.rupees() == Decimal("4200000")
