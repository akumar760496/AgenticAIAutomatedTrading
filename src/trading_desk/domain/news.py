"""News and exchange filings as the domain sees them.

The raw body is kept, never a summary. A summary discards the detail you need in
three months when working out why an idea failed - and the credibility scorer in
step 3.2 reads wording the summary would have smoothed away.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from trading_desk.domain.enums import SourceTier


@dataclass(frozen=True)
class FeedFailure:
    """A source that could not be read on a given poll, and why.

    Carried back from the adapter so the ingest can alert. A feed that 403s and
    a feed with no news both return zero articles; only this tells them apart.
    """

    name: str
    reason: str


@dataclass(frozen=True)
class NewsArticle:
    """One article from one publisher.

    Several articles about the same event are linked by event_key, so five outlets
    carrying one announcement count as one event with five sources rather than five
    pieces of evidence.
    """

    article_id: str
    headline: str
    body: str
    url: str
    publisher: str
    source_tier: SourceTier
    published_at: datetime
    ingested_at: datetime
    instrument_symbols: tuple[str, ...] = ()
    event_key: str | None = None

    def __post_init__(self) -> None:
        if not self.headline:
            raise ValueError(f"{self.article_id}: headline must not be empty")
        if self.published_at.tzinfo is None:
            raise ValueError(f"{self.article_id}: published_at must be tz-aware")

    def lag_seconds(self) -> float:
        """How late we were to this story. Feeds the staleness check in step 1.5."""
        return (self.ingested_at - self.published_at).total_seconds()


@dataclass(frozen=True)
class Filing:
    """An exchange announcement. SourceTier.EXCHANGE by definition - this is fact."""

    filing_id: str
    symbol: str
    subject: str
    body: str
    url: str
    filed_at: datetime
    ingested_at: datetime
    attachments: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.filed_at.tzinfo is None:
            raise ValueError(f"{self.filing_id}: filed_at must be tz-aware")

    @property
    def source_tier(self) -> SourceTier:
        return SourceTier.EXCHANGE
