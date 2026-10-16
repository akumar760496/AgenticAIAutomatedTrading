"""Builds the tradeable universe from a symbol list plus Dhan's instrument master.

Two sources, each doing what it is good at:

- **Config says what we want to watch.** A list of symbols, under version control,
  changed deliberately.
- **The instrument master says what exists and what it is called.** Security ids,
  lot sizes, and whether a symbol has listed options at all.

Nothing here hand-writes a security id or a lot size. Those are facts that change
by exchange circular, and the first run of this code found two: TATAMOTORS has no
listed options, and NIFTY's id differs between the index and derivatives segments.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from trading_desk.domain.enums import Exchange, Segment
from trading_desk.domain.instruments import InstrumentCatalogue, InstrumentRef

DEFAULT_UNIVERSE_PATH = Path("config/universe_starter.yaml")


@dataclass(frozen=True)
class UniverseRequest:
    """What config asks for, before anything checks whether it exists."""

    index_symbols: tuple[str, ...]
    stock_symbols: tuple[str, ...]
    version: int
    source: str

    @property
    def all_symbols(self) -> tuple[str, ...]:
        return self.index_symbols + self.stock_symbols


@dataclass(frozen=True)
class Universe:
    """What we will actually capture, and what we asked for but cannot.

    `unavailable` is part of the result rather than a logged aside. A symbol that
    silently vanishes from the universe is a gap in the archive that nobody
    notices until the IV history is needed.
    """

    instruments: tuple[InstrumentRef, ...]
    unavailable: tuple[str, ...]
    version: int

    def __len__(self) -> int:
        return len(self.instruments)

    def all_instruments(self) -> tuple[InstrumentRef, ...]:
        return self.instruments


def load_universe_request(path: Path = DEFAULT_UNIVERSE_PATH) -> UniverseRequest:
    if not path.is_file():
        raise FileNotFoundError(f"Universe config not found at {path}")

    raw = yaml.safe_load(path.read_text())
    return UniverseRequest(
        index_symbols=tuple(raw.get("indices", [])),
        stock_symbols=tuple(raw.get("stocks", [])),
        version=int(raw["version"]),
        source=str(raw["source"]),
    )


def resolve_universe(request: UniverseRequest, catalogue: InstrumentCatalogue) -> Universe:
    """Turn requested symbols into instruments, naming what could not be resolved."""
    instruments = []
    unavailable = []

    for symbol in request.all_symbols:
        info = catalogue.underlyings.get(symbol)
        if info is None:
            unavailable.append(symbol)
            continue
        instruments.append(
            InstrumentRef(
                symbol=info.symbol,
                exchange=Exchange.NSE,
                segment=Segment.FNO,
                security_id=info.security_id,
                lot_size=info.lot_size,
            )
        )

    return Universe(
        instruments=tuple(instruments),
        unavailable=tuple(unavailable),
        version=request.version,
    )
