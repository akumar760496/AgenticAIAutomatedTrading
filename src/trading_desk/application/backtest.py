"""Running every strategy and writing a result the dashboard can read.

This is the only layer that touches a database or a file. The strategies and
the engine are pure, which is why they can be tested without a market.

**Market direction comes from the index's own bars.** `trend_regime` over NIFTY
for each trading date, computed once and shared, so every trade can say what
the wider market was doing on the day it was entered. That is a real answer,
unlike the news column: the filings archive begins 2026-10-14, so for a trade
in 2024 there is nothing to check and the field says so.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean

from trading_desk.domain.backtest.engine import MarketContext, run_strategy
from trading_desk.domain.backtest.strategies import build_registry
from trading_desk.domain.backtest.strategy import Strategy
from trading_desk.domain.backtest.trade import FailureMode, SimulatedTrade
from trading_desk.domain.enums import TrendRegime
from trading_desk.domain.indicators import DEFAULT_SLOW_MA, trend_regime
from trading_desk.domain.market import Bar
from trading_desk.domain.setups.thresholds import load_setup_thresholds
from trading_desk.ports.bar_store import BarStorePort

log = logging.getLogger(__name__)

DEFAULT_RESULTS_DIR = Path("data/backtests")

# The index whose regime defines "the market" for the direction column.
MARKET_INDEX = "NIFTY"

# Daily bars to read per instrument. 940 exist; this reads all of them.
BARS_TO_READ = 2000

# Enough history for the slowest thing any strategy consults.
WARMUP = DEFAULT_SLOW_MA + 5


@dataclass(frozen=True)
class StrategyResult:
    """One strategy's trades and the summary the dashboard shows first."""

    strategy: str
    description: str
    exits: str
    trades_indices: bool
    breakeven_win_rate: float
    trades: list[SimulatedTrade]

    @property
    def win_rate(self) -> float | None:
        if not self.trades:
            return None
        return sum(1 for trade in self.trades if trade.won) / len(self.trades)

    @property
    def expectancy(self) -> float | None:
        if not self.trades:
            return None
        return mean(trade.net_return for trade in self.trades)

    @property
    def average_win(self) -> float:
        wins = [trade.net_return for trade in self.trades if trade.won]
        return mean(wins) if wins else 0.0

    @property
    def average_loss(self) -> float:
        """Mean losing return, as a positive magnitude."""
        losses = [-trade.net_return for trade in self.trades if not trade.won]
        return mean(losses) if losses else 0.0

    @property
    def realised_breakeven(self) -> float | None:
        """The win rate the *realised* win/loss sizes require.

        The exit policy's own `breakeven_win_rate` is theoretical: it assumes
        every win is the full target and every loss the full stop. Reality is
        neither - gaps overshoot the stop, time stops exit part-way, and costs
        apply to both. So a strategy can clear the theoretical bar and still
        lose money, which `index_trend_pullback` did on its first run: 33.8%
        against a 33.3% theoretical breakeven, and an expectancy of -0.35%.
        This is the bar that actually has to be cleared.
        """
        if self.average_win + self.average_loss <= 0:
            return None
        return self.average_loss / (self.average_win + self.average_loss)

    @property
    def profitable(self) -> bool:
        """The only verdict that means anything: did it make money.

        Deliberately not a win-rate comparison. A win rate is a statistic; this
        is the question.
        """
        expectancy = self.expectancy
        return expectancy is not None and expectancy > 0


def market_context(bars: list[Bar]) -> MarketContext:
    """The index's regime on each date, from a widening window of its own bars.

    Computed forward so each date sees only its own history - the same rule the
    strategies follow, for the same reason.
    """
    regimes: dict[str, TrendRegime] = {}
    for index in range(WARMUP, len(bars)):
        regimes[str(bars[index].start.date())] = trend_regime(bars[: index + 1])
    return (regimes and MarketContext(regime_by_date=regimes)) or MarketContext({})


class BacktestRunner:
    def __init__(self, bars: BarStorePort, results_dir: Path = DEFAULT_RESULTS_DIR) -> None:
        self._bars = bars
        self._dir = results_dir

    def run(self, stocks: list[str], indices: list[str]) -> list[StrategyResult]:
        registry = build_registry(load_setup_thresholds().setup_a)
        market = market_context(self._read(MARKET_INDEX))

        results = []
        for strategy in registry.values():
            symbols = indices if strategy.trades_indices else stocks
            results.append(self._run_one(strategy, symbols, market))
        return results

    def _run_one(
        self, strategy: Strategy, symbols: list[str], market: MarketContext
    ) -> StrategyResult:
        trades: list[SimulatedTrade] = []
        for symbol in symbols:
            bars = self._read(symbol)
            if len(bars) <= WARMUP:
                log.info("%s: only %d bars, skipped", symbol, len(bars))
                continue
            trades.extend(run_strategy(strategy, bars, market, WARMUP))

        log.info("%s: %d trades over %d symbol(s)", strategy.name, len(trades), len(symbols))
        return StrategyResult(
            strategy=strategy.name,
            description=strategy.describe(),
            exits=strategy.exits.describe(),
            trades_indices=strategy.trades_indices,
            breakeven_win_rate=strategy.exits.breakeven_win_rate,
            trades=trades,
        )

    def _read(self, symbol: str) -> list[Bar]:
        return list(self._bars.daily_bars(symbol, BARS_TO_READ))

    def write(self, results: list[StrategyResult]) -> Path:
        """One JSON file the dashboard reads. Overwrites today's run."""
        self._dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).date().isoformat()
        path = self._dir / f"{stamp}.json"
        path.write_text(json.dumps(_as_json(results), indent=2))

        latest = self._dir / "latest.json"
        latest.write_text(json.dumps(_as_json(results), indent=2))
        return path


def _as_json(results: list[StrategyResult]) -> dict[str, object]:
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategies": [_strategy_json(result) for result in results],
    }


def _strategy_json(result: StrategyResult) -> dict[str, object]:
    return {
        "name": result.strategy,
        "description": result.description,
        "exits": result.exits,
        "trades_indices": result.trades_indices,
        "breakeven_win_rate": result.breakeven_win_rate,
        "realised_breakeven": result.realised_breakeven,
        "win_rate": result.win_rate,
        "expectancy": result.expectancy,
        "average_win": result.average_win,
        "average_loss": result.average_loss,
        "profitable": result.profitable,
        "trade_count": len(result.trades),
        "failure_modes": _failure_tally(result.trades),
        "trades": [_trade_json(trade) for trade in result.trades],
    }


def _failure_tally(trades: list[SimulatedTrade]) -> dict[str, int]:
    tally: dict[str, int] = {}
    for trade in trades:
        mode = trade.failure_mode
        if mode is FailureMode.NOT_A_FAILURE:
            continue
        tally[mode.value] = tally.get(mode.value, 0) + 1
    return dict(sorted(tally.items(), key=lambda item: -item[1]))


def _trade_json(trade: SimulatedTrade) -> dict[str, object]:
    mode = trade.failure_mode
    return {
        "symbol": trade.symbol,
        "is_index": trade.is_index,
        "direction": trade.direction.value,
        "entry_date": trade.entry_date,
        "entry_price": str(trade.entry_price.rupees()),
        "exit_date": trade.exit_date,
        "exit_price": str(trade.exit_price.rupees()),
        "sessions_held": trade.sessions_held,
        "exit_reason": trade.exit_reason.value,
        "stop_level": str(trade.stop_level.rupees()),
        "target_level": str(trade.target_level.rupees()),
        "net_return": trade.net_return,
        "won": trade.won,
        "market_direction": trade.market_direction.value,
        "against_market": trade.against_market,
        "against_news": trade.against_news,
        "reasons": [
            {
                "name": reason.name,
                "verdict": reason.verdict.value,
                "measured": reason.measured,
                "threshold": reason.threshold,
            }
            for reason in trade.reasons
        ],
        "failure_mode": mode.value,
        "failure_explanation": mode.explain(),
    }
