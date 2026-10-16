"""Snapshots land on disk in the layout DuckDB will scan, with prices intact."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

from trading_desk.adapters.store.parquet_chain import ParquetChainArchive
from trading_desk.domain.enums import Exchange, OptionRight, Segment
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.market import OptionChainSnapshot, OptionQuote, OptionStrike
from trading_desk.domain.values import Price

NOW = datetime(2026, 10, 16, 4, 0, tzinfo=UTC)  # 09:30 IST, the market open


def _nifty() -> InstrumentRef:
    return InstrumentRef(
        symbol="NIFTY",
        exchange=Exchange.NSE,
        segment=Segment.FNO,
        security_id="13",
        lot_size=65,
    )


def _snapshot(with_put: bool = True, iv: float | None = 13.5) -> OptionChainSnapshot:
    call = OptionQuote(
        right=OptionRight.CALL,
        last=Price.from_rupees(Decimal("210.55")),
        bid=Price.from_rupees(Decimal("210.00")),
        ask=Price.from_rupees(Decimal("211.00")),
        volume=125000,
        open_interest=48000,
        implied_volatility=iv,
        delta=0.55,
    )
    put = (
        OptionQuote(
            right=OptionRight.PUT,
            last=Price.from_rupees(Decimal("88.25")),
            bid=Price.from_rupees(Decimal("88.00")),
            ask=Price.from_rupees(Decimal("88.50")),
            volume=90000,
            open_interest=61000,
            implied_volatility=13.1,
            delta=-0.45,
        )
        if with_put
        else None
    )
    return OptionChainSnapshot(
        underlying=_nifty(),
        expiry_date="2026-10-19",
        strikes=(OptionStrike(strike=Price.from_rupees(Decimal("25000")), call=call, put=put),),
        spot=Price.from_rupees(Decimal("25123.45")),
        exchange_time=NOW,
        received_at=NOW,
    )


def test_file_lands_in_the_partitioned_path(tmp_path: Path) -> None:
    """Partitioned by IST trading date: 04:00 UTC is 09:30 IST."""
    ParquetChainArchive(tmp_path).write(_snapshot())

    expected = tmp_path / "date=2026-10-16" / "underlying=NIFTY" / "snapshot_0930.parquet"
    assert expected.is_file()


def test_one_row_per_strike_per_side(tmp_path: Path) -> None:
    rows = ParquetChainArchive(tmp_path).write(_snapshot())

    assert rows == 2


def test_a_missing_side_is_not_written_as_a_row(tmp_path: Path) -> None:
    rows = ParquetChainArchive(tmp_path).write(_snapshot(with_put=False))

    assert rows == 1


def test_prices_survive_the_round_trip_exactly(tmp_path: Path) -> None:
    """Paise in, paise out. A float column here would be a silent rounding bug."""
    archive = ParquetChainArchive(tmp_path)
    archive.write(_snapshot())

    frame = pl.read_parquet(
        tmp_path / "date=2026-10-16" / "underlying=NIFTY" / "snapshot_0930.parquet"
    )
    call_row = frame.filter(pl.col("right") == "CALL").row(0, named=True)

    assert call_row["last_paise"] == 21055
    assert call_row["strike_paise"] == 2500000
    assert call_row["spot_paise"] == 2512345


def test_missing_iv_is_stored_as_null_not_zero(tmp_path: Path) -> None:
    """A zero IV would corrupt the IV-rank series, and it cannot be rebuilt."""
    archive = ParquetChainArchive(tmp_path)
    archive.write(_snapshot(with_put=False, iv=None))

    frame = pl.read_parquet(
        tmp_path / "date=2026-10-16" / "underlying=NIFTY" / "snapshot_0930.parquet"
    )

    assert frame["implied_volatility"][0] is None


def test_snapshot_count_tracks_captures_per_day(tmp_path: Path) -> None:
    archive = ParquetChainArchive(tmp_path)
    archive.write(_snapshot())

    assert archive.snapshot_count("2026-10-16", "NIFTY") == 1
    assert archive.snapshot_count("2026-10-15", "NIFTY") == 0


def test_an_empty_snapshot_is_refused(tmp_path: Path) -> None:
    """Refuse rather than write a file that later reads as a successful capture."""
    empty = OptionChainSnapshot(
        underlying=_nifty(),
        expiry_date="2026-10-19",
        strikes=(OptionStrike(strike=Price.from_rupees(Decimal("25000")), call=None, put=None),),
        spot=Price.from_rupees(Decimal("25000")),
        exchange_time=NOW,
        received_at=NOW,
    )

    with pytest.raises(ValueError, match="no rows"):
        ParquetChainArchive(tmp_path).write(empty)
