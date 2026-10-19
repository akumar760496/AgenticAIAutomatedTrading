"""Building and reading the hand-labelled news set.

`docs/10` calls the 200-item labelling "your work, and the step most likely to
slip". This module is the half that can be automated: drawing a sample worth
labelling, writing it somewhere a human can work through it, and reading it
back so the classifier can be scored.

**The sample is drawn oldest-first across the whole archive, not newest-first.**
A sample of the last three days is a sample of whatever happened in the last
three days - probably one earnings season and no rating actions at all - and a
classifier tuned on it learns the week rather than the task.

**Labelling order is shuffled by a stable hash, not by date.** Reading 200
articles in date order means reading them in topic clusters, and by item 40 the
labeller is answering from momentum. The hash also decides the held-out split
(`domain/classification_report.py`), so neither depends on the other.

The CSV deliberately has a blank `true_label` column and nothing else to fill
in. Every extra field is a field that gets left empty.
"""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from pathlib import Path

from trading_desk.domain.catalysts import CatalystType
from trading_desk.domain.classification_report import (
    ClassificationReport,
    LabelledItem,
    build_report,
    is_held_out,
)
from trading_desk.domain.enums import SourceTier
from trading_desk.domain.news import NewsArticle

DEFAULT_LABEL_PATH = Path("data/labels/news_catalysts.csv")
DEFAULT_SAMPLE_SIZE = 200
COLUMNS = ("item_id", "published_at", "publisher", "headline", "body_excerpt", "true_label")
BODY_EXCERPT_CHARS = 600

VALID_LABELS = frozenset(item.value for item in CatalystType)


class LabelFileError(RuntimeError):
    """The label file is unusable, and says exactly which row is wrong."""


@dataclass(frozen=True)
class LabelSetSummary:
    """What a label file contains, before anything is scored with it."""

    total: int
    by_label: dict[str, int]
    held_out: int
    tuning: int

    def as_lines(self) -> list[str]:
        lines = [f"{self.total} labelled ({self.tuning} tuning, {self.held_out} held out)"]
        for label, count in sorted(self.by_label.items(), key=lambda item: -item[1]):
            lines.append(f"  {label:<24}{count:>5}")
        return lines


def _shuffle_key(article: NewsArticle) -> str:
    """Stable, content-free ordering so the labeller does not read in clusters."""
    return hashlib.sha256(article.article_id.encode()).hexdigest()


def _stratum(article: NewsArticle) -> str:
    """Which bucket an item is sampled from.

    Filings are bucketed by their exchange subject, which is a controlled
    vocabulary; articles all share one bucket because their headlines are free
    text and would each form a stratum of one.
    """
    if article.source_tier is SourceTier.EXCHANGE:
        return f"filing:{' '.join(article.headline.split())}"
    return "article"


def stratified_sample(articles: list[NewsArticle], size: int) -> list[NewsArticle]:
    """Draw a sample that contains the rare categories.

    A uniform draw over this archive is 93% compliance boilerplate, because
    that is what the archive is: five subjects account for over half of it.
    Labelling 200 of those teaches the classifier nothing and measures it
    against a test set a constant could pass.

    So this takes a round-robin across strata - one from each, then a second
    from each, and so on - which gives every subject a place before any
    subject gets a second. Rare catalyst types are the whole reason the test
    set exists (`docs/12`: "the rare catalyst types ... are the ones that move
    prices"), and a uniform draw is exactly how they go missing.

    **The cost of this, stated plainly: the accuracy number from a stratified
    set is not the production accuracy.** Rare types are deliberately
    over-represented, so a score here is harder to get than in the wild. That
    is the right trade - per-class recall is what `docs/12` asks for and what
    a uniform sample cannot give - but the headline percentage must never be
    quoted as "how often the classifier is right on a normal day".
    """
    buckets: dict[str, list[NewsArticle]] = {}
    for article in sorted(articles, key=_shuffle_key):
        buckets.setdefault(_stratum(article), []).append(article)

    chosen: list[NewsArticle] = []
    depth = 0
    while len(chosen) < size:
        took_any = False
        for bucket in sorted(buckets):
            members = buckets[bucket]
            if depth < len(members):
                chosen.append(members[depth])
                took_any = True
                if len(chosen) == size:
                    return chosen
        if not took_any:
            break  # the archive is smaller than the requested sample
        depth += 1
    return chosen


def write_sample(
    articles: list[NewsArticle],
    destination: Path = DEFAULT_LABEL_PATH,
    size: int = DEFAULT_SAMPLE_SIZE,
) -> int:
    """Write a blank labelling sheet. Returns how many rows were written.

    Refuses to overwrite: half a day of labelling is not something a re-run
    should be able to destroy.
    """
    if destination.exists():
        raise LabelFileError(
            f"{destination} already exists and will not be overwritten. "
            "Move it aside if you really want a fresh sample."
        )

    chosen = stratified_sample(articles, size)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for article in chosen:
            writer.writerow(
                [
                    article.article_id,
                    article.published_at.isoformat(),
                    article.publisher,
                    article.headline,
                    article.body[:BODY_EXCERPT_CHARS].replace("\n", " "),
                    "",  # for you to fill in
                ]
            )
    return len(chosen)


def load_labels(path: Path = DEFAULT_LABEL_PATH) -> list[LabelledItem]:
    """Read the labelled set, failing loudly on anything unusable.

    A blank or misspelled label raises rather than being skipped. Skipping bad
    rows silently shrinks the test set, and a 90% score on 140 items that was
    reported as 200 is a number nobody can trust.
    """
    if not path.is_file():
        raise LabelFileError(f"No label file at {path}. Run `trading-desk label-news` first.")

    items: list[LabelledItem] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for line_number, row in enumerate(csv.DictReader(handle), start=2):
            label = (row.get("true_label") or "").strip().upper()
            if not label:
                raise LabelFileError(f"{path} line {line_number}: true_label is empty")
            if label not in VALID_LABELS:
                raise LabelFileError(
                    f"{path} line {line_number}: '{label}' is not a catalyst type. "
                    f"Use one of: {', '.join(sorted(VALID_LABELS))}"
                )
            items.append(
                LabelledItem(
                    item_id=row["item_id"],
                    text=f"{row['headline']}\n{row['body_excerpt']}",
                    true_label=label,
                )
            )
    return items


def summarise(items: list[LabelledItem]) -> LabelSetSummary:
    by_label: dict[str, int] = {}
    for item in items:
        by_label[item.true_label] = by_label.get(item.true_label, 0) + 1
    held_out = sum(1 for item in items if is_held_out(item.item_id))
    return LabelSetSummary(
        total=len(items),
        by_label=by_label,
        held_out=held_out,
        tuning=len(items) - held_out,
    )


def score_predictions(
    items: list[LabelledItem], predictions: dict[str, str], held_out_only: bool
) -> ClassificationReport:
    """Build a report over one half of the set.

    `held_out_only` is an explicit argument with no default on purpose. Which
    half you are scoring is the single most important thing about a reported
    number, and a default would let it go unstated.
    """
    pairs = [
        (item.true_label, predictions[item.item_id])
        for item in items
        if item.item_id in predictions and is_held_out(item.item_id) == held_out_only
    ]
    return build_report(pairs, classes=tuple(sorted(VALID_LABELS)))
