"""What we need from news and exchange filing sources.

Two ports, not one. A filing is fact from the exchange; an article is reporting
with a credibility score attached. Collapsing them would lose the distinction the
Setup A credibility gate depends on.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from trading_desk.domain.news import Filing, NewsArticle


class NewsPort(Protocol):
    def fetch_since(self, since: datetime, symbols: list[str]) -> list[NewsArticle]:
        """Articles published after `since` mentioning any of `symbols`.

        Returns the full body, never a summary. Deduplication happens later, in the
        news agent, so the raw record keeps every outlet that carried the story.
        """
        ...


class FilingsPort(Protocol):
    def fetch_since(self, since: datetime, symbols: list[str]) -> list[Filing]:
        """Exchange announcements filed after `since` for any of `symbols`."""
        ...
