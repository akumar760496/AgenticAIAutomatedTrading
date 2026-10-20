"""The strategies available to backtest.

`build_registry` is what the CLI and the dashboard enumerate. Adding a strategy
means writing the class and adding one line here - nothing else in the system
needs to know about it.

It is a function rather than a module-level dict because a strategy may need
thresholds from `config/`, and reading a file at import time would make the
whole domain package depend on a file being present.
"""

from trading_desk.domain.backtest.strategies.index_breakout import IndexBreakout
from trading_desk.domain.backtest.strategies.stock_breakout import StockBreakout
from trading_desk.domain.backtest.strategy import Strategy
from trading_desk.domain.setups.thresholds import SetupAThresholds


def build_registry(stock_thresholds: SetupAThresholds) -> dict[str, Strategy]:
    """Every strategy, keyed by its stable name."""
    strategies: tuple[Strategy, ...] = (
        StockBreakout(stock_thresholds),
        IndexBreakout(),
    )
    return {strategy.name: strategy for strategy in strategies}


__all__ = ["IndexBreakout", "StockBreakout", "Strategy", "build_registry"]
