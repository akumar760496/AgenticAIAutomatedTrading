"""The Portfolio Context agent: what you already hold, before anything new.

`docs/02`: "Prevents the classic agentic failure of proposing the seventh
correlated long Nifty-bank bet."

It reads positions from the broker and open ideas from the journal, and it
reports exposure, open index positions and open theses. No model call: every
number is a sum over rows.

**Net delta is computed only when every option position can be priced**, and is
`None` otherwise. That rule is the whole design of this module. A broker
position carries no greeks, so the delta has to be read from the option chain
for each held strike; if one of five positions has no quote, the sum of the
other four is a number that looks complete and is not. `docs/02` puts the net
delta in this agent's output, and `domain/risk/gate.py` has a `max_net_delta`
limit, so a quietly-wrong figure would flow straight into a limit check
(CLAUDE.md rule 6).

**Sector concentration is not computed at all.** `docs/02` asks for it, and
there is no sector mapping anywhere in `config/` - not in the universe file,
not in the instrument master. Writing one from memory would be exactly the kind
of plausible-looking invented data these rules exist to keep out, so the agent
reports it as missing on every call (ISS-027).

The scalar net delta is crude by nature: it sums share-equivalents across
different underlyings, which is only meaningful because v1 holds at most a
handful of positions and the risk gate already models the limit as one number.
It is reported as a measurement with that caveat attached, never as a view.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from trading_desk.domain.enums import OptionRight
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.specialists import SpecialistFinding, SpecialistName
from trading_desk.domain.values import Money
from trading_desk.ports.broker import BrokerPosition, BrokerReadPort
from trading_desk.ports.journal import JournalPort, RecordedIdea
from trading_desk.ports.option_chain import OptionChainPort

SECTOR_MAP_MISSING = (
    "sector concentration: no sector mapping exists in config/ for any symbol, "
    "so correlated names cannot be detected (ISS-027)"
)

NET_DELTA_CAVEAT = (
    "net delta sums share-equivalents across different underlyings - usable as a "
    "limit check on a handful of positions, not as a hedge ratio"
)


@dataclass(frozen=True)
class PortfolioView:
    """What is already on, and what is already believed."""

    finding: SpecialistFinding
    positions: int
    exposure_by_symbol: dict[str, Money]
    open_index_positions: int
    open_theses: tuple[RecordedIdea, ...]
    net_delta: float | None
    available_funds: Money | None

    def exposure_for(self, symbol: str) -> Money:
        return self.exposure_by_symbol.get(symbol, Money.zero())

    def already_has_a_thesis_on(self, symbol: str) -> bool:
        return any(idea.symbol == symbol for idea in self.open_theses)


class PortfolioAgent:
    def __init__(
        self,
        broker: BrokerReadPort,
        journal: JournalPort,
        chains: OptionChainPort,
        index_symbols: frozenset[str],
    ) -> None:
        self._broker = broker
        self._journal = journal
        self._chains = chains
        # Which symbols are indices is a fact about the exchange, not something
        # to infer from a position: NIFTY and BANKNIFTY are in the universe
        # config, and the caller passes them in rather than this module keeping
        # a second copy that can drift (CLAUDE.md rule 7).
        self._index_symbols = index_symbols

    def assess(self, symbol: str, as_of: datetime) -> PortfolioView:
        positions = self._broker.fetch_positions()
        open_ideas = tuple(self._journal.ideas_awaiting_decision(as_of))
        funds = self._broker.fetch_funds()

        exposure = _exposure_by_symbol(positions)
        net_delta, unpriced = self._net_delta(positions)

        unavailable = [SECTOR_MAP_MISSING]
        if unpriced:
            unavailable.append(
                f"net delta: {len(unpriced)} of {len(positions)} positions have no "
                f"chain quote ({', '.join(unpriced)}) - a partial sum is not reported"
            )

        measurements = {
            "open_positions": str(len(positions)),
            "open_index_positions": str(self._index_positions(positions)),
            "open_theses": str(len(open_ideas)),
            "exposure_in_this_symbol": f"₹{exposure.get(symbol, Money.zero()).rupees():,.2f}",
            "total_exposure": f"₹{sum(value.paise for value in exposure.values()) / 100:,.2f}",
            "net_delta": "unavailable" if net_delta is None else f"{net_delta:,.1f}",
            "available_funds": f"₹{funds.rupees():,.2f}",
        }

        notes = [NET_DELTA_CAVEAT]
        if any(idea.symbol == symbol for idea in open_ideas):
            notes.append(f"a thesis on {symbol} is already open and undecided")

        return PortfolioView(
            finding=SpecialistFinding(
                specialist=SpecialistName.PORTFOLIO,
                symbol=symbol,
                measurements=measurements,
                vetoes=(),
                unavailable=tuple(unavailable),
                notes=tuple(notes),
            ),
            positions=len(positions),
            exposure_by_symbol=exposure,
            open_index_positions=self._index_positions(positions),
            open_theses=open_ideas,
            net_delta=net_delta,
            available_funds=funds,
        )

    def _index_positions(self, positions: list[BrokerPosition]) -> int:
        """Feeds Setup B's one-index-position-at-a-time gate."""
        return sum(1 for position in positions if position.instrument.symbol in self._index_symbols)

    def _net_delta(self, positions: list[BrokerPosition]) -> tuple[float | None, list[str]]:
        """Sum position deltas, or return None naming what could not be priced.

        An equity position has a delta of 1 per share by definition. An option
        position's delta has to be read from the chain, because the broker does
        not send one.
        """
        total = 0.0
        unpriced: list[str] = []
        for position in positions:
            if not position.instrument.is_option:
                total += position.quantity.units
                continue

            delta = self._delta_from_chain(position.instrument)
            if delta is None:
                unpriced.append(str(position.instrument))
                continue
            total += delta * position.quantity.units

        if unpriced:
            return None, unpriced
        return total, []

    def _delta_from_chain(self, instrument: InstrumentRef) -> float | None:
        expiry, strike, right = instrument.expiry, instrument.strike, instrument.right
        if expiry is None or strike is None or right is None:
            return None

        underlying = InstrumentRef(
            symbol=instrument.symbol,
            exchange=instrument.exchange,
            segment=instrument.segment,
            security_id=instrument.security_id,
            lot_size=instrument.lot_size,
        )
        snapshot = self._chains.fetch_chain(underlying, expiry)
        for row in snapshot.strikes:
            if row.strike.paise != strike.paise:
                continue
            quote = row.call if right is OptionRight.CALL else row.put
            return None if quote is None else quote.delta
        return None


def _exposure_by_symbol(positions: list[BrokerPosition]) -> dict[str, Money]:
    """Exposure at the price paid, per underlying symbol.

    At cost rather than at market: this is a concentration measure, and
    revaluing it would make yesterday's limit depend on today's move.
    """
    exposure: dict[str, Money] = {}
    for position in positions:
        symbol = position.instrument.symbol
        value = Money(paise=position.average_price.paise * position.quantity.units)
        exposure[symbol] = exposure.get(symbol, Money.zero()) + value
    return exposure
