"""Fixed vocabularies used across the domain.

Enums rather than strings so a typo is a failure at the point it is written, not a
silent mismatch three layers later. StrEnum so values serialise to readable text in
the database and in briefings.
"""

from __future__ import annotations

from enum import StrEnum


class Exchange(StrEnum):
    NSE = "NSE"
    BSE = "BSE"


class Segment(StrEnum):
    """Dhan's exchange segments, kept as our own names so a vendor change is a mapping."""

    EQUITY = "EQUITY"
    FNO = "FNO"
    CURRENCY = "CURRENCY"
    COMMODITY = "COMMODITY"


class OptionRight(StrEnum):
    CALL = "CALL"
    PUT = "PUT"


class Direction(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class Horizon(StrEnum):
    """How long an idea is expected to take. Bounds the expiry choice in step 2.5."""

    INTRADAY = "INTRADAY"
    SWING_3D = "SWING_3D"
    SWING_2W = "SWING_2W"


class SourceTier(StrEnum):
    """How much a source is trusted. Drives the credibility gate in Setup A.

    EXCHANGE is fact: it came from an NSE/BSE filing. Everything below is reporting.
    """

    EXCHANGE = "EXCHANGE"
    TIER_1 = "TIER_1"
    TIER_2 = "TIER_2"
    UNVERIFIED = "UNVERIFIED"


class SetupId(StrEnum):
    """The two v1 setups. A third needs 100 resolved ideas first - see docs/05."""

    CATALYST_CONTINUATION = "SETUP_A"
    INDEX_VOL_REGIME = "SETUP_B"


class Verdict(StrEnum):
    """A gate's conclusion. NO_DATA is distinct from FAIL on purpose.

    "The data was missing" and "the test was failed" lead to different fixes, and
    collapsing them would hide a broken feed as a quiet stream of rejections.
    """

    PASS = "PASS"
    FAIL = "FAIL"
    NO_DATA = "NO_DATA"
