"""Config says what we want; the instrument master says what exists."""

from __future__ import annotations

from pathlib import Path

import pytest

from trading_desk.application.universe import (
    UniverseRequest,
    load_universe_request,
    resolve_universe,
)
from trading_desk.domain.instruments import InstrumentCatalogue, UnderlyingInfo

CONFIG = Path("config/universe_starter.yaml")


def _catalogue(*symbols: str) -> InstrumentCatalogue:
    return InstrumentCatalogue(
        underlyings={
            symbol: UnderlyingInfo(
                symbol=symbol,
                security_id=str(1000 + index),
                lot_size=50 + index,
                is_index=symbol in {"NIFTY", "BANKNIFTY"},
            )
            for index, symbol in enumerate(symbols)
        },
        unresolved=(),
    )


class TestConfigIsSymbolsOnly:
    def test_indices_and_stocks_are_listed(self) -> None:
        request = load_universe_request(CONFIG)

        assert request.index_symbols == ("NIFTY", "BANKNIFTY")
        assert "RELIANCE" in request.stock_symbols

    def test_tatamotors_was_removed_after_the_master_showed_no_options(self) -> None:
        """Regression: the placeholder list carried a symbol with no listed options."""
        request = load_universe_request(CONFIG)

        assert "TATAMOTORS" not in request.all_symbols

    def test_a_missing_config_file_fails_loudly(self) -> None:
        with pytest.raises(FileNotFoundError, match="Universe config"):
            load_universe_request(Path("config/does_not_exist.yaml"))


class TestResolution:
    def test_security_ids_and_lot_sizes_come_from_the_master(self) -> None:
        """Nothing hand-writes an id. NIFTY is 13 in one segment and 26000 in another."""
        request = UniverseRequest(("NIFTY",), ("RELIANCE",), version=2, source="test")

        universe = resolve_universe(request, _catalogue("NIFTY", "RELIANCE"))

        nifty = universe.instruments[0]
        assert nifty.security_id == "1000"
        assert nifty.lot_size == 50

    def test_symbols_without_listed_options_are_named_not_dropped(self) -> None:
        """A symbol that vanishes silently is a gap nobody notices until the IV history is needed."""
        request = UniverseRequest(("NIFTY",), ("TATAMOTORS",), version=2, source="test")

        universe = resolve_universe(request, _catalogue("NIFTY"))

        assert len(universe) == 1
        assert universe.unavailable == ("TATAMOTORS",)

    def test_the_whole_starter_list_resolves(self) -> None:
        request = load_universe_request(CONFIG)
        catalogue = _catalogue(*request.all_symbols)

        universe = resolve_universe(request, catalogue)

        assert universe.unavailable == ()
        assert len(universe) == len(request.all_symbols)
