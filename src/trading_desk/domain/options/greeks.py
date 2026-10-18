"""Black-76 pricing, greeks and an implied-volatility solver. Pure maths.

**Why our own implementation rather than calling a library from `domain/`.**
The dependency rule keeps `domain/` free of heavy third-party packages, and
`vollib` pulls in scipy and numpy. The arithmetic here is a page of standard
formulae; what matters is that it is *checked*, so `vollib` is a development
dependency used in the tests as the reference implementation. Our numbers are
asserted against it rather than against themselves.

**Black-76, on a forward.** Indian index options are European and cash-settled,
which is what Black-76 assumes. We capture spot rather than the future, so the
forward is derived as `F = S * exp(r * T)` with no dividend yield - an
approximation that is stated here rather than hidden, and whose effect is
largest on long-dated single stocks going ex-dividend.

**Divergence from the broker's greeks is expected and is the point.** Dhan
publishes its own greeks without publishing its model, its risk-free rate, its
dividend assumption or its day-count convention. Any of those differing moves
delta by a little and theta by a lot. `compare_greeks` therefore reports the
gap rather than deciding who is right - docs/10 says disagreements are
"investigated rather than averaged".
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from trading_desk.domain.enums import OptionRight

# A year, for converting days to the T in the formulae. Calendar days, not
# trading days: the risk-free rate accrues on weekends and so does decay.
DAYS_PER_YEAR = 365.0

# The solver's bounds. Below 0.1% is numerically meaningless; above 500% the
# quote is broken rather than volatile (see domain.vol on the 419% reading).
MIN_SOLVER_VOL = 0.001
MAX_SOLVER_VOL = 5.0
SOLVER_TOLERANCE = 1e-6
SOLVER_MAX_ITERATIONS = 100

# Below this a broker greek is indistinguishable from zero, and a ratio
# against it would report an infinite divergence that means nothing.
NEGLIGIBLE = 1e-9


@dataclass(frozen=True)
class Greeks:
    """Model outputs. Floats, unlike prices - there is no exact decimal here.

    Conventions, which are where cross-checks usually go wrong:
    - `vega` is per **one percentage point** of volatility, not per 1.0.
    - `theta` is per **calendar day**, not per year.
    Both match what brokers publish, so a comparison is like for like.
    """

    price: float
    delta: float
    gamma: float
    theta: float
    vega: float


def _normal_cdf(value: float) -> float:
    """Standard normal CDF via erf - no scipy needed for one function."""
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _normal_pdf(value: float) -> float:
    return math.exp(-0.5 * value * value) / math.sqrt(2.0 * math.pi)


def forward_price(spot: float, rate: float, years: float) -> float:
    """F = S e^(rT), no dividend yield. See the module docstring."""
    return spot * math.exp(rate * years)


def _d1_d2(forward: float, strike: float, years: float, vol: float) -> tuple[float, float]:
    variance = vol * math.sqrt(years)
    d1 = (math.log(forward / strike) + 0.5 * vol * vol * years) / variance
    return d1, d1 - variance


def _is_degenerate(forward: float, strike: float, years: float, vol: float) -> bool:
    return forward <= 0 or strike <= 0 or years <= 0 or vol <= 0


def black76_price(  # noqa: PLR0917 - (F, K, T, r, sigma, right) is the textbook order
    forward: float, strike: float, years: float, rate: float, vol: float, right: OptionRight
) -> float:
    """The option's value. At or past expiry this is intrinsic, not an error."""
    if years <= 0 or vol <= 0:
        intrinsic = (
            max(forward - strike, 0.0) if right is OptionRight.CALL else max(strike - forward, 0.0)
        )
        return intrinsic * math.exp(-rate * max(years, 0.0))
    if forward <= 0 or strike <= 0:
        return 0.0

    d1, d2 = _d1_d2(forward, strike, years, vol)
    discount = math.exp(-rate * years)
    if right is OptionRight.CALL:
        return discount * (forward * _normal_cdf(d1) - strike * _normal_cdf(d2))
    return discount * (strike * _normal_cdf(-d2) - forward * _normal_cdf(-d1))


def black76_greeks(  # noqa: PLR0917 - (F, K, T, r, sigma, right) is the textbook order
    forward: float, strike: float, years: float, rate: float, vol: float, right: OptionRight
) -> Greeks:
    """Price and the four greeks the briefing uses."""
    price = black76_price(forward, strike, years, rate, vol, right)
    if _is_degenerate(forward, strike, years, vol):
        # An expired or unpriced option has no sensitivities worth quoting.
        return Greeks(price=price, delta=0.0, gamma=0.0, theta=0.0, vega=0.0)

    d1, d2 = _d1_d2(forward, strike, years, vol)
    discount = math.exp(-rate * years)
    root_t = math.sqrt(years)
    pdf = _normal_pdf(d1)

    if right is OptionRight.CALL:
        delta = discount * _normal_cdf(d1)
        theta_annual = (
            -forward * discount * pdf * vol / (2 * root_t)
            - rate * strike * discount * _normal_cdf(d2)
            + rate * forward * discount * _normal_cdf(d1)
        )
    else:
        delta = -discount * _normal_cdf(-d1)
        theta_annual = (
            -forward * discount * pdf * vol / (2 * root_t)
            + rate * strike * discount * _normal_cdf(-d2)
            - rate * forward * discount * _normal_cdf(-d1)
        )

    return Greeks(
        price=price,
        delta=delta,
        gamma=discount * pdf / (forward * vol * root_t),
        # Per calendar day and per percentage point, matching broker convention.
        theta=theta_annual / DAYS_PER_YEAR,
        vega=forward * discount * pdf * root_t / 100.0,
    )


def implied_volatility(  # noqa: PLR0917 - mirrors the pricing signature
    price: float,
    forward: float,
    strike: float,
    years: float,
    rate: float,
    right: OptionRight,
) -> float | None:
    """Solve for the volatility that reproduces `price`, or None.

    Bisection rather than Newton-Raphson: it is slower and cannot diverge.
    Vega collapses for deep in- or out-of-the-money options, which is exactly
    where Newton overshoots into a negative volatility and returns something
    confident and wrong. This runs over a few hundred strikes a day, so the
    extra iterations cost nothing worth measuring.

    None means "no volatility reproduces this price", which happens when the
    quote is below intrinsic or above the forward - a broken quote, not a
    volatile one.
    """
    if price <= 0 or years <= 0 or forward <= 0 or strike <= 0:
        return None

    low_price = black76_price(forward, strike, years, rate, MIN_SOLVER_VOL, right)
    high_price = black76_price(forward, strike, years, rate, MAX_SOLVER_VOL, right)
    if not low_price <= price <= high_price:
        return None

    low, high = MIN_SOLVER_VOL, MAX_SOLVER_VOL
    for _ in range(SOLVER_MAX_ITERATIONS):
        middle = (low + high) / 2
        value = black76_price(forward, strike, years, rate, middle, right)
        if abs(value - price) < SOLVER_TOLERANCE:
            return middle
        if value < price:
            low = middle
        else:
            high = middle
    return (low + high) / 2


@dataclass(frozen=True)
class GreekDivergence:
    """How far our number is from the broker's, for one greek."""

    name: str
    ours: float
    theirs: float

    @property
    def absolute(self) -> float:
        return abs(self.ours - self.theirs)

    @property
    def relative(self) -> float | None:
        """None when theirs is ~0 - a ratio against zero says nothing."""
        if abs(self.theirs) < NEGLIGIBLE:
            return None
        return self.absolute / abs(self.theirs)


def compare_greeks(
    ours: Greeks, theirs: Greeks, tolerance: float = 0.10
) -> tuple[GreekDivergence, ...]:
    """Greeks differing by more than `tolerance` in relative terms.

    Reports, never averages. docs/10: disagreements are "investigated rather
    than averaged" - splitting the difference between two models produces a
    number neither model would stand behind.
    """
    pairs = (
        ("delta", ours.delta, theirs.delta),
        ("gamma", ours.gamma, theirs.gamma),
        ("theta", ours.theta, theirs.theta),
        ("vega", ours.vega, theirs.vega),
    )
    diverged = []
    for name, mine, broker in pairs:
        divergence = GreekDivergence(name=name, ours=mine, theirs=broker)
        relative = divergence.relative
        if relative is not None and relative > tolerance:
            diverged.append(divergence)
    return tuple(diverged)
