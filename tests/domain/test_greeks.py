"""Black-76 against `vollib`, the reference implementation.

Our numbers are asserted against an independent library rather than against
themselves. `vollib` is a dev dependency and deliberately not imported by
anything in `domain/` - see the module docstring in `domain/options/greeks.py`.
"""

from __future__ import annotations

import math

import pytest
from vollib.black import black
from vollib.black.greeks.analytical import delta, gamma, theta, vega

from trading_desk.domain.enums import OptionRight
from trading_desk.domain.options.greeks import (
    Greeks,
    black76_greeks,
    black76_price,
    compare_greeks,
    forward_price,
    implied_volatility,
)

TOLERANCE = 1e-6

# (forward, strike, years, rate, vol) across moneyness and tenor.
CASES = [
    (24000.0, 24000.0, 30 / 365, 0.065, 0.18),  # at the money
    (24000.0, 24200.0, 30 / 365, 0.065, 0.18),  # out of the money
    (24000.0, 23000.0, 30 / 365, 0.065, 0.18),  # deep in the money
    (24000.0, 24000.0, 3 / 365, 0.065, 0.25),  # near expiry
    (1400.0, 1500.0, 45 / 365, 0.065, 0.32),  # a single stock
]


@pytest.mark.parametrize(("forward", "strike", "years", "rate", "vol"), CASES)
@pytest.mark.parametrize(("right", "flag"), [(OptionRight.CALL, "c"), (OptionRight.PUT, "p")])
class TestAgainstVollib:
    def test_price_matches(  # noqa: PLR0917
        self,
        forward: float,
        strike: float,
        years: float,
        rate: float,
        vol: float,
        right: OptionRight,
        flag: str,
    ) -> None:
        ours = black76_price(forward, strike, years, rate, vol, right)
        assert ours == pytest.approx(black(flag, forward, strike, years, rate, vol), abs=TOLERANCE)

    def test_every_greek_matches(  # noqa: PLR0917
        self,
        forward: float,
        strike: float,
        years: float,
        rate: float,
        vol: float,
        right: OptionRight,
        flag: str,
    ) -> None:
        ours = black76_greeks(forward, strike, years, rate, vol, right)

        assert ours.delta == pytest.approx(
            delta(flag, forward, strike, years, rate, vol), abs=TOLERANCE
        )
        assert ours.gamma == pytest.approx(
            gamma(flag, forward, strike, years, rate, vol), abs=TOLERANCE
        )
        assert ours.vega == pytest.approx(
            vega(flag, forward, strike, years, rate, vol), abs=TOLERANCE
        )
        assert ours.theta == pytest.approx(
            theta(flag, forward, strike, years, rate, vol), abs=TOLERANCE
        )


class TestImpliedVolatilitySolver:
    @pytest.mark.parametrize(("forward", "strike", "years", "rate", "vol"), CASES)
    def test_it_recovers_the_volatility_it_was_priced_with(
        self, forward: float, strike: float, years: float, rate: float, vol: float
    ) -> None:
        """The round trip: price at a known vol, solve, get the vol back."""
        price = black76_price(forward, strike, years, rate, vol, OptionRight.CALL)
        solved = implied_volatility(price, forward, strike, years, rate, OptionRight.CALL)

        assert solved is not None
        assert solved == pytest.approx(vol, abs=1e-4)

    def test_a_price_below_intrinsic_has_no_solution(self) -> None:
        """None, not a guess. A broken quote is not a volatile one."""
        assert implied_volatility(1.0, 24000.0, 23000.0, 30 / 365, 0.065, OptionRight.CALL) is None

    def test_a_price_above_the_forward_has_no_solution(self) -> None:
        assert (
            implied_volatility(30000.0, 24000.0, 24000.0, 30 / 365, 0.065, OptionRight.CALL) is None
        )

    def test_a_zero_price_has_no_solution(self) -> None:
        assert implied_volatility(0.0, 24000.0, 24000.0, 30 / 365, 0.065, OptionRight.CALL) is None

    def test_the_solver_does_not_diverge_deep_out_of_the_money(self) -> None:
        """Where vega collapses, Newton-Raphson overshoots into a negative
        volatility and returns something confident and wrong. Bisection cannot."""
        price = black76_price(24000.0, 30000.0, 3 / 365, 0.065, 0.20, OptionRight.CALL)
        solved = implied_volatility(price, 24000.0, 30000.0, 3 / 365, 0.065, OptionRight.CALL)

        assert solved is None or solved > 0


class TestDegenerateInputs:
    def test_an_expired_option_is_worth_its_intrinsic(self) -> None:
        value = black76_price(24000.0, 23000.0, 0.0, 0.065, 0.18, OptionRight.CALL)
        assert value == pytest.approx(1000.0)

    def test_an_expired_option_has_no_sensitivities(self) -> None:
        greeks = black76_greeks(24000.0, 23000.0, 0.0, 0.065, 0.18, OptionRight.CALL)
        assert (greeks.delta, greeks.gamma, greeks.theta, greeks.vega) == (0.0, 0.0, 0.0, 0.0)

    def test_zero_volatility_does_not_divide_by_zero(self) -> None:
        greeks = black76_greeks(24000.0, 24000.0, 30 / 365, 0.065, 0.0, OptionRight.CALL)
        assert greeks.gamma == 0.0


class TestPutCallParity:
    def test_parity_holds(self) -> None:
        """C - P = e^-rT (F - K). An identity, so it catches a sign error that
        a comparison against one library's calls alone would not."""
        forward, strike, years, rate, vol = 24000.0, 24200.0, 30 / 365, 0.065, 0.18
        call = black76_price(forward, strike, years, rate, vol, OptionRight.CALL)
        put = black76_price(forward, strike, years, rate, vol, OptionRight.PUT)

        assert call - put == pytest.approx(math.exp(-rate * years) * (forward - strike))


class TestDivergenceReporting:
    def _greeks(self, delta_value: float) -> Greeks:
        return Greeks(price=100.0, delta=delta_value, gamma=0.001, theta=-5.0, vega=20.0)

    def test_agreement_inside_tolerance_reports_nothing(self) -> None:
        assert compare_greeks(self._greeks(0.50), self._greeks(0.52), tolerance=0.10) == ()

    def test_a_divergent_greek_is_named_with_both_numbers(self) -> None:
        diverged = compare_greeks(self._greeks(0.50), self._greeks(0.20), tolerance=0.10)

        assert [item.name for item in diverged] == ["delta"]
        assert diverged[0].ours == 0.50
        assert diverged[0].theirs == 0.20

    def test_a_zero_broker_value_is_not_an_infinite_divergence(self) -> None:
        """A ratio against zero says nothing, so it is not reported as a gap."""
        theirs = Greeks(price=100.0, delta=0.0, gamma=0.001, theta=-5.0, vega=20.0)
        assert compare_greeks(self._greeks(0.50), theirs) == ()


def test_the_forward_is_spot_grown_at_the_risk_free_rate() -> None:
    assert forward_price(24000.0, 0.065, 1.0) == pytest.approx(24000.0 * math.exp(0.065))
