"""One event, many sources.

Five outlets carrying the same announcement is one piece of evidence, not five.
Counting it five times is how a single press release becomes an apparent
consensus - which is exactly the signal a credibility gate is supposed to weigh.

Two stages, and only the first is here:

- **Exact duplication**, by a hash of the normalised body. Catches syndication,
  which is most of it: wire copy republished verbatim across outlets.
- **Near duplication**, the same story reworded. Hashing cannot see it; it needs
  embeddings, which need a model that is not yet chosen. `event_key` is
  deliberately assigned by this module so the near-duplicate stage can later
  merge keys without every consumer changing.

The normalisation is intentionally mild - whitespace and case only. Stripping
punctuation or stopwords would merge stories that differ by a negation, and
"will not proceed" against "will now proceed" is the difference between two
opposite trades.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass

_WHITESPACE = re.compile(r"\s+")


def content_hash(headline: str, body: str) -> str:
    """A stable id for identical content, regardless of whitespace or case.

    Headline and body are joined with a separator that cannot occur in either
    after normalisation, so a headline ending where a body begins cannot collide
    with a different split of the same characters.
    """
    normalised = _WHITESPACE.sub(" ", f"{headline}\n\x00\n{body}".lower()).strip()
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SourcedEvent:
    """One story, and every outlet that carried it."""

    event_key: str
    headline: str
    source_ids: tuple[str, ...]

    @property
    def source_count(self) -> int:
        return len(self.source_ids)


@dataclass(frozen=True)
class Document:
    """The minimum needed to deduplicate. Adapters map their types to this."""

    document_id: str
    headline: str
    body: str


def group_identical(documents: Sequence[Document]) -> list[SourcedEvent]:
    """Group documents whose content hashes match, preserving input order.

    Returns one event per distinct story, with the sources that carried it. The
    event_key is the content hash, so the same story ingested tomorrow lands on
    the same key without any state being kept.
    """
    events: dict[str, list[str]] = {}
    headlines: dict[str, str] = {}

    for document in documents:
        key = content_hash(document.headline, document.body)
        if key not in events:
            events[key] = []
            headlines[key] = document.headline
        events[key].append(document.document_id)

    return [
        SourcedEvent(event_key=key, headline=headlines[key], source_ids=tuple(ids))
        for key, ids in events.items()
    ]
