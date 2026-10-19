"""The Options Strategist, against fake chains and the real thresholds file.

The thresholds come from `config/setups.yaml` rather than from literals here,
because the point of several of these tests is what the *configured* numbers
imply - including the two places where the config has no answer and the agent
has to say so instead of inventing one.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, time
from decimal import Decimal

from tests.agents.fixtures import NIFTY, RELIANCE, chain, nifty_chain
from tests.fakes import FakeOptionChain
from trading_desk.agents.options.agent import SETUP_B_DEBIT_BANDS_MISSING, OptionsAgent
from trading_desk.domain.enums import Direction, SetupId
from trading_desk.domain.market import OptionChainSnapshot
from trading_desk.domain.options.selection import StructureKind
from trading_desk.domain.setups.thresholds import load_setup_thresholds
from trading_desk.domain.values import Money, Price
from trading_desk.domain.vol import IvRank, IvSource

AS_OF = date(2026, 10, 19)
STOCK_EXPIRY = date(2026, 11, 5)  # 17 days out: inside Setup A's 10-25 window
INDEX_EXPIRY = date(2026, 10, 26)  # 7 days out: inside Setup B's credit window

THRESHOLDS = load_setup_thresholds()
WING = Money.from_rupees(Decimal("200"))


def _rank(value: float | None, reason: str = "") -> IvRank:
    return IvRank(
        value=value,
        source=IvSource.PROXY,
        sample_count=130,
        window=252,
        reason=reason,
    )


def _agent(*chains: tuple[OptionChainSnapshot, date]) -> tuple[OptionsAgent, FakeOptionChain]:
    port = FakeOptionChain()
    for snapshot, expiry in chains:
        port.add_chain(snapshot, expiry)
    return OptionsAgent(port, THRESHOLDS, WING), port


class TestSetupABuysPremium:
    def test_a_low_iv_rank_produces_a_debit_vertical(self) -> None:
        agent, _ = _agent((chain(RELIANCE, STOCK_EXPIRY), STOCK_EXPIRY))

        view = agent.propose(
            instrument=RELIANCE,
            setup=SetupId.CATALYST_CONTINUATION,
            direction=Direction.BULLISH,
            iv_rank=_rank(40.0),
            as_of=AS_OF,
        )

        assert view.choice.kind is StructureKind.DEBIT_VERTICAL
        assert view.spread is not None
        assert view.days_to_expiry == 17

    def test_the_structure_is_defined_risk_with_two_legs(self) -> None:
        agent, _ = _agent((chain(RELIANCE, STOCK_EXPIRY), STOCK_EXPIRY))
        view = agent.propose(
            instrument=RELIANCE,
            setup=SetupId.CATALYST_CONTINUATION,
            direction=Direction.BULLISH,
            iv_rank=_rank(40.0),
            as_of=AS_OF,
        )

        assert view.spread is not None
        assert view.spread.max_loss.paise + view.spread.max_gain.paise == view.spread.width.paise
        assert len(view.legs) == 2

    def test_an_elevated_iv_rank_does_not_flip_setup_a_into_selling(self) -> None:
        """config/setups.yaml gives Setup A a ceiling, not a regime."""
        agent, _ = _agent((chain(RELIANCE, STOCK_EXPIRY), STOCK_EXPIRY))

        view = agent.propose(
            instrument=RELIANCE,
            setup=SetupId.CATALYST_CONTINUATION,
            direction=Direction.BULLISH,
            iv_rank=_rank(95.0),
            as_of=AS_OF,
        )

        assert view.choice.kind is StructureKind.NONE
        assert "does not sell" in view.finding.vetoes[0]

    def test_the_legs_carry_the_quotes_the_liquidity_gate_compares(self) -> None:
        agent, _ = _agent((chain(RELIANCE, STOCK_EXPIRY), STOCK_EXPIRY))
        view = agent.propose(
            instrument=RELIANCE,
            setup=SetupId.CATALYST_CONTINUATION,
            direction=Direction.BULLISH,
            iv_rank=_rank(40.0),
            as_of=AS_OF,
        )

        assert all(leg.open_interest_lots > 0 for leg in view.legs)
        assert "min_leg_open_interest" in view.finding.measurements


class TestSetupBSellsPremium:
    def test_a_high_iv_rank_produces_a_credit_vertical_with_a_wing(self) -> None:
        agent, _ = _agent((nifty_chain(INDEX_EXPIRY), INDEX_EXPIRY))

        view = agent.propose(
            instrument=NIFTY,
            setup=SetupId.INDEX_VOL_REGIME,
            direction=Direction.BULLISH,
            iv_rank=_rank(85.0),
            as_of=AS_OF,
            time_of_day=time(10, 0),
        )

        assert view.choice.kind is StructureKind.CREDIT_VERTICAL
        assert view.spread is not None
        assert view.spread.long_leg.strike.paise != view.spread.short_leg.strike.paise

    def test_the_debit_variant_refuses_because_config_has_no_delta_bands(self) -> None:
        """A measured config gap, not a bug: setups.yaml gives setup_b a credit
        delta band and two expiry windows, and no strikes for the debit side."""
        agent, _ = _agent((nifty_chain(date(2026, 11, 2)), date(2026, 11, 2)))

        view = agent.propose(
            instrument=NIFTY,
            setup=SetupId.INDEX_VOL_REGIME,
            direction=Direction.BULLISH,
            iv_rank=_rank(20.0),
            as_of=AS_OF,
        )

        assert view.spread is None
        assert view.finding.vetoes == (SETUP_B_DEBIT_BANDS_MISSING,)

    def test_no_credit_spread_on_expiry_afternoon(self) -> None:
        agent, _ = _agent((nifty_chain(INDEX_EXPIRY), INDEX_EXPIRY))

        view = agent.propose(
            instrument=NIFTY,
            setup=SetupId.INDEX_VOL_REGIME,
            direction=Direction.BULLISH,
            iv_rank=_rank(85.0),
            as_of=AS_OF,
            is_expiry_day=True,
            time_of_day=time(15, 0),
        )

        assert view.spread is None
        assert "short gamma" in view.finding.vetoes[0]


class TestRefusals:
    def test_an_unavailable_iv_rank_builds_nothing_and_says_why(self) -> None:
        agent, _port = _agent((chain(RELIANCE, STOCK_EXPIRY), STOCK_EXPIRY))

        view = agent.propose(
            instrument=RELIANCE,
            setup=SetupId.CATALYST_CONTINUATION,
            direction=Direction.BULLISH,
            iv_rank=_rank(None, reason="11 samples, 30 needed"),
            as_of=AS_OF,
        )

        assert view.spread is None
        assert "no IV rank" in view.finding.vetoes[0]
        assert any("11 samples" in item for item in view.finding.unavailable)

    def test_no_expiry_in_the_window_refuses_before_fetching_a_chain(self) -> None:
        """Setup A wants 10-25 days; this chain expires tomorrow."""
        tomorrow = date(2026, 10, 20)
        agent, port = _agent((chain(RELIANCE, tomorrow), tomorrow))

        view = agent.propose(
            instrument=RELIANCE,
            setup=SetupId.CATALYST_CONTINUATION,
            direction=Direction.BULLISH,
            iv_rank=_rank(40.0),
            as_of=AS_OF,
        )

        assert view.spread is None
        assert "no expiry between 10 and 25 days" in view.finding.vetoes[0]
        assert port.fetch_calls == 0

    def test_a_chain_with_no_wing_refuses_rather_than_going_naked_short(self) -> None:
        """FR-5.9, induced rather than assumed.

        The short leg must be *available* and the wing must not be, or the test
        proves something else. Here 23600 sits in the credit band and is
        priced; everything below it has been removed, so there is nothing to
        buy as protection. The first version of this test used a chain whose
        short leg was itself unpriceable, and passed for the wrong reason.
        """
        full = nifty_chain(INDEX_EXPIRY)
        no_lower_strikes = replace(
            full,
            strikes=tuple(
                row for row in full.strikes if row.strike >= Price.from_rupees(Decimal("23600"))
            ),
        )
        agent, _ = _agent((no_lower_strikes, INDEX_EXPIRY))

        view = agent.propose(
            instrument=NIFTY,
            setup=SetupId.INDEX_VOL_REGIME,
            direction=Direction.BULLISH,
            iv_rank=_rank(85.0),
            as_of=AS_OF,
            time_of_day=time(10, 0),
        )

        assert view.spread is None
        assert "naked short" in view.finding.vetoes[0]


def test_the_view_carries_no_quantity_anywhere() -> None:
    """CLAUDE.md rule 1. The strategist describes a structure; it never sizes
    one, and there is no field here for a lot count to arrive in later."""
    agent, _ = _agent((chain(RELIANCE, STOCK_EXPIRY), STOCK_EXPIRY))
    view = agent.propose(
        instrument=RELIANCE,
        setup=SetupId.CATALYST_CONTINUATION,
        direction=Direction.BULLISH,
        iv_rank=_rank(40.0),
        as_of=AS_OF,
    )

    banned = {"quantity", "lots", "lot_count", "size", "order_price"}
    assert not banned & set(view.__dataclass_fields__)
    assert view.spread is not None
    assert not banned & set(view.spread.__dataclass_fields__)
