"""Writes option chain snapshots to partitioned Parquet.

Layout:

    data/chains/date=2026-10-16/underlying=NIFTY/snapshot_0930.parquet

Partitioned by date then underlying so a year of history can be scanned by DuckDB
without reading files it does not need, and so a missing day is visible as a
missing directory rather than as absent rows inside a large file.

One row per strike per side. Wide-per-strike would be fewer rows but makes "all
call IVs for this underlying" a column-name problem instead of a filter.

Prices are stored as integer paise, matching the domain. Greeks are floats,
because they are model outputs with no exact decimal value.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from trading_desk.adapters.clock import to_ist
from trading_desk.domain.market import OptionChainSnapshot

DEFAULT_ARCHIVE_ROOT = Path("data/chains")

# Declared explicitly rather than inferred. Two reasons, both learned the hard way:
# inference reads only the first rows, so a file whose early strikes have no greeks
# infers Null and then fails when a real float arrives; and a multi-year archive
# must have identical column types in every file or a single scan across it breaks.
SCHEMA: dict[str, pl.DataType] = {
    "underlying": pl.String(),
    "expiry": pl.String(),
    "received_at": pl.Datetime(time_unit="us", time_zone="UTC"),
    "exchange_time": pl.Datetime(time_unit="us", time_zone="UTC"),
    "spot_paise": pl.Int64(),
    "strike_paise": pl.Int64(),
    "right": pl.String(),
    "last_paise": pl.Int64(),
    "bid_paise": pl.Int64(),
    "ask_paise": pl.Int64(),
    "volume": pl.Int64(),
    "open_interest": pl.Int64(),
    "implied_volatility": pl.Float64(),
    "delta": pl.Float64(),
    "gamma": pl.Float64(),
    "theta": pl.Float64(),
    "vega": pl.Float64(),
}


class ParquetChainArchive:
    """Implements ChainArchivePort against the local filesystem."""

    def __init__(self, root: Path = DEFAULT_ARCHIVE_ROOT) -> None:
        self._root = root

    def write(self, snapshot: OptionChainSnapshot) -> int:
        rows = self._to_rows(snapshot)
        if not rows:
            raise ValueError(
                f"{snapshot.underlying.symbol}: snapshot produced no rows - refusing to "
                "write an empty file that would later look like a successful capture"
            )

        target = self._path_for(snapshot)
        target.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(rows, schema=SCHEMA).write_parquet(target)
        return len(rows)

    def snapshot_count(self, on_date: str, underlying: str) -> int:
        """Snapshots captured for an IST trading date."""
        directory = self._root / f"date={on_date}" / f"underlying={underlying}"
        if not directory.is_dir():
            return 0
        return len(list(directory.glob("snapshot_*.parquet")))

    def _path_for(self, snapshot: OptionChainSnapshot) -> Path:
        # Partitioned by IST, because a trading day is an IST concept. Using UTC
        # would be correct during market hours and confusing everywhere else:
        # a 22:50 IST run would land under the previous UTC date.
        captured = to_ist(snapshot.received_at)
        return (
            self._root
            / f"date={captured.date().isoformat()}"
            / f"underlying={snapshot.underlying.symbol}"
            / f"snapshot_{captured.strftime('%H%M')}.parquet"
        )

    @staticmethod
    def _to_rows(snapshot: OptionChainSnapshot) -> list[dict[str, object]]:
        """Flatten to one row per strike per side. An explicit loop: when a field
        moves, the line that breaks should be obvious."""
        rows: list[dict[str, object]] = []
        for strike in snapshot.strikes:
            for side in (strike.call, strike.put):
                if side is None:
                    continue
                rows.append(
                    {
                        "underlying": snapshot.underlying.symbol,
                        "expiry": snapshot.expiry_date,
                        "received_at": snapshot.received_at,
                        "exchange_time": snapshot.exchange_time,
                        "spot_paise": snapshot.spot.paise,
                        "strike_paise": strike.strike.paise,
                        "right": str(side.right),
                        "last_paise": side.last.paise,
                        "bid_paise": side.bid.paise,
                        "ask_paise": side.ask.paise,
                        "volume": side.volume,
                        "open_interest": side.open_interest,
                        "implied_volatility": side.implied_volatility,
                        "delta": side.delta,
                        "gamma": side.gamma,
                        "theta": side.theta,
                        "vega": side.vega,
                    }
                )
        return rows
