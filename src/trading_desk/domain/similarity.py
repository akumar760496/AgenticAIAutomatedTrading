"""Near-duplicate detection: the same story, reworded.

Content hashing catches verbatim syndication. It cannot catch the same story
rewritten by a second desk, which is most of what a news feed carries, so two
outlets reporting one announcement would count as two pieces of evidence.

**Character shingles with Jaccard similarity**, not embeddings. The reasoning,
because the obvious choice was embeddings:

- The volume is a few hundred headlines a day. A neural model is ~2GB of
  dependency and a model download to compare short strings.
- It is deterministic. The same two headlines give the same score today and in
  six months, so a dedup decision can be re-derived when auditing an old
  briefing. An embedding model silently updated by its vendor cannot promise that.
- It is explainable. "82% of character 4-grams shared" is a sentence that can go
  in a briefing. A cosine distance in a 384-dimensional space is not.
- Character shingles survive the rewording that actually happens in news:
  reordered clauses, a changed verb, an added attribution. They are weaker on
  genuine paraphrase with no shared vocabulary - which is rarer, and which the
  pgvector column stays free for if it ever matters.

**Similarity alone is not safe, at any threshold.** Measured on real headline
shapes:

    "TCS wins $1bn deal with European retailer"
    "Infosys wins $1bn deal with European retailer"      -> 0.837

    "Infosys Q2 profit rises 12% beating estimates"
    "Infosys second quarter profit up 12 percent"        -> 0.260

The first pair is two companies and must never merge; the second is one story
and should. Same-story scores ranged 0.26-0.60 and different-story reached 0.84,
so no cut-off separates them. An embedding model would do *worse* here, not
better - those two sentences are semantically almost identical.

So grouping is **partitioned by resolved instrument** and similarity only decides
within a partition. Two stories about different companies are never compared, and
inside one company the measured gap was clean: 0.068-0.179 for different events
against 0.26+ for the same event reworded.

Pure module: text in, scores out, no clock and no model.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

# Four characters: long enough that common words do not match everything, short
# enough to survive a changed word ending. Tuned on headlines, which are short.
SHINGLE_SIZE = 4

# Above this, two texts about the *same instrument* are the same story. Measured
# on real headline shapes: different events for one company scored 0.068-0.179,
# the same event reworded scored 0.26 and up. 0.22 sits in that gap.
#
# The gap is narrow, and this number is the weakest thing in this module. It was
# tuned on a handful of constructed headlines, not on a month of real feed data.
# Re-measure it once articles have been flowing (ISS-011).
DEFAULT_THRESHOLD = 0.22

_NON_WORD = re.compile(r"[^a-z0-9 ]+")
_WHITESPACE = re.compile(r"\s+")


def _normalise(text: str) -> str:
    return _WHITESPACE.sub(" ", _NON_WORD.sub(" ", text.lower())).strip()


def shingles(text: str, size: int = SHINGLE_SIZE) -> frozenset[str]:
    """Overlapping character n-grams of normalised text."""
    cleaned = _normalise(text)
    if len(cleaned) < size:
        return frozenset({cleaned}) if cleaned else frozenset()
    return frozenset(cleaned[index : index + size] for index in range(len(cleaned) - size + 1))


def similarity(first: str, second: str) -> float:
    """Jaccard overlap of the two texts' shingles, 0.0 to 1.0."""
    left, right = shingles(first), shingles(second)
    if not left or not right:
        return 0.0
    intersection = len(left & right)
    union = len(left | right)
    return intersection / union


@dataclass(frozen=True)
class SimilarDocument:
    """A document, its text, and the instruments it is about.

    `symbols` is the partition key. Documents with different symbol sets are
    never compared, which is what stops "TCS wins deal" merging with "Infosys
    wins deal" at 0.84 similarity. An empty set is its own partition: an article
    naming no universe instrument can only duplicate another such article.
    """

    document_id: str
    text: str
    symbols: frozenset[str] = frozenset()


@dataclass(frozen=True)
class DuplicateGroup:
    """One story and every document that told it.

    `representative_id` is the first document seen, so grouping is stable under
    re-ingestion rather than depending on which copy happened to arrive first in
    a later batch.
    """

    representative_id: str
    member_ids: tuple[str, ...]
    closest_match: float | None
    """Similarity of the best match that joined this group, or None if nothing did.

    None rather than 1.0 for a single-source story: a lone document matching
    itself at 1.0 reads as a perfect duplicate detection when in fact nothing
    was detected."""

    @property
    def size(self) -> int:
        return len(self.member_ids)

    @property
    def is_syndicated(self) -> bool:
        """Whether more than one outlet carried this story."""
        return len(self.member_ids) > 1


def group_near_duplicates(
    documents: Sequence[SimilarDocument], threshold: float = DEFAULT_THRESHOLD
) -> list[DuplicateGroup]:
    """Group documents telling the same story about the same instruments.

    Partitioned by `symbols` first, so similarity never has the chance to merge
    two companies. Within a partition, each document is compared against each
    existing group's representative - not every member - so a chain of small
    similarities cannot drag unrelated stories into one group.

    Deliberately the simple O(n^2) comparison rather than clustering. A few
    hundred documents per poll is a few thousand set intersections, which is
    nothing, and the behaviour is obvious when a grouping has to be explained.
    """
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"threshold must be between 0 and 1, got {threshold}")

    by_partition: dict[frozenset[str], list[SimilarDocument]] = {}
    for document in documents:
        by_partition.setdefault(document.symbols, []).append(document)

    groups: list[DuplicateGroup] = []
    for partition in by_partition.values():
        groups.extend(_group_one_partition(partition, threshold))

    # Input order, so a re-ingest produces the same output.
    order = {document.document_id: index for index, document in enumerate(documents)}
    return sorted(groups, key=lambda group: order[group.representative_id])


def _group_one_partition(
    documents: Sequence[SimilarDocument], threshold: float
) -> list[DuplicateGroup]:
    representatives: list[SimilarDocument] = []
    members: list[list[str]] = []
    best_scores: list[float | None] = []

    for document in documents:
        best_index = -1
        best_score = 0.0

        for index, representative in enumerate(representatives):
            score = similarity(document.text, representative.text)
            if score >= threshold and score > best_score:
                best_index, best_score = index, score

        if best_index >= 0:
            members[best_index].append(document.document_id)
            previous = best_scores[best_index]
            best_scores[best_index] = best_score if previous is None else max(previous, best_score)
        else:
            representatives.append(document)
            members.append([document.document_id])
            best_scores.append(None)

    return [
        DuplicateGroup(
            representative_id=representative.document_id,
            member_ids=tuple(group_members),
            closest_match=score,
        )
        for representative, group_members, score in zip(
            representatives, members, best_scores, strict=True
        )
    ]
