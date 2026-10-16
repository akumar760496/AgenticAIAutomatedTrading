"""Dhan's instrument master: the authoritative map from symbol to security id.

Why this exists rather than a hand-written list of ids:

- **Security ids are not guessable.** NIFTY is 13 in the index segment and 26000
  in the derivatives segment. Writing either by hand is a coin flip.
- **Lot sizes change by circular.** NIFTY moved 75 -> 65 with the Jan 2026 series.
  The master carries the current value; a config file carries whatever someone
  last typed.
- **Symbols come and go.** TATAMOTORS has no listed options at all, which a
  hand-written universe would never have revealed until a snapshot silently
  failed every day.

The file is ~34MB, so it is cached on disk and refreshed once a day.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import polars as pl

from trading_desk.domain.instruments import InstrumentCatalogue, UnderlyingInfo

SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
DEFAULT_CACHE_DIR = Path("data/reference")

DOWNLOAD_TIMEOUT_SECONDS = 120.0

# Dhan's own codes, used to pick the rows we care about.
EXCHANGE_NSE = "NSE"
SEGMENT_INDEX = "I"
INSTRUMENT_STOCK_OPTION = "OPTSTK"
INSTRUMENT_INDEX_OPTION = "OPTIDX"


class DhanInstrumentMaster:
    """Downloads, caches and queries Dhan's scrip master."""

    def __init__(
        self,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._cache_dir = cache_dir
        self._http = http_client

    def cache_path(self, on_date: date) -> Path:
        return self._cache_dir / f"scrip_master_{on_date.isoformat()}.csv"

    def ensure_downloaded(self, on_date: date) -> Path:
        """Download today's master unless it is already cached."""
        target = self.cache_path(on_date)
        if target.is_file() and target.stat().st_size > 0:
            return target

        target.parent.mkdir(parents=True, exist_ok=True)
        client = self._http or httpx.Client(timeout=DOWNLOAD_TIMEOUT_SECONDS)
        try:
            response = client.get(SCRIP_MASTER_URL)
            response.raise_for_status()
            target.write_bytes(response.content)
        finally:
            if self._http is None:
                client.close()
        return target

    def option_underlyings(self, on_date: date) -> InstrumentCatalogue:
        """Every NSE symbol that has listed options, keyed by symbol.

        Read as strings throughout: security ids are identifiers, and parsing one
        as a number is how a leading zero disappears.
        """
        path = self.ensure_downloaded(on_date)
        frame = pl.read_csv(path, infer_schema_length=0)

        options = frame.filter(
            (pl.col("EXCH_ID") == EXCHANGE_NSE)
            & pl.col("INSTRUMENT").is_in([INSTRUMENT_STOCK_OPTION, INSTRUMENT_INDEX_OPTION])
        )

        underlyings: dict[str, UnderlyingInfo] = {}
        unresolved: list[str] = []

        for row in options.iter_rows(named=True):
            symbol = row["UNDERLYING_SYMBOL"]
            if not symbol or symbol in underlyings or symbol in unresolved:
                continue

            is_index = row["INSTRUMENT"] == INSTRUMENT_INDEX_OPTION
            if is_index:
                security_id = self._index_security_id(frame, symbol)
                if security_id is None:
                    unresolved.append(symbol)
                    continue
            else:
                security_id = row["UNDERLYING_SECURITY_ID"]

            underlyings[symbol] = UnderlyingInfo(
                symbol=symbol,
                security_id=security_id,
                lot_size=int(float(row["LOT_SIZE"] or 0)),
                is_index=is_index,
            )

        return InstrumentCatalogue(underlyings=underlyings, unresolved=tuple(sorted(unresolved)))

    @staticmethod
    def _index_security_id(frame: pl.DataFrame, symbol: str) -> str | None:
        """Indices need the id from the index segment, not the derivatives one.

        The option chain API takes UnderlyingScrip with UnderlyingSeg=IDX_I, and
        in that space NIFTY is 13. In the derivatives rows the same index appears
        as 26000. Using the wrong one returns a chain for something else.
        """
        matches = frame.filter(
            (pl.col("SEGMENT") == SEGMENT_INDEX) & (pl.col("SYMBOL_NAME") == symbol)
        )
        if matches.is_empty():
            return None
        return str(matches["SECURITY_ID"][0])
