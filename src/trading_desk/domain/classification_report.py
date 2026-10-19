"""Scoring a classifier honestly.

`docs/12` step 3.2 is unusually specific, and the reason is worth repeating
here because it is the whole design of this module:

> ">=90% on your 200-item labelled set, with a **confusion matrix, not just an
> accuracy number** - accuracy hides a classifier that never predicts the rare
> catalyst types, which are the ones that move prices."

In a realistic sample most items are not catalysts at all. A classifier that
answers NONE to everything would score somewhere near 85% and be worthless.
So `accuracy` exists but `ClassificationReport` will not let you read it alone:
`weakest_class()` names the worst-performing label every time it is printed.

The held-out split is by a **stable hash of the item id**, not by shuffling.
Shuffling with a seed is reproducible only as long as nobody changes the sample
size; hashing means an item stays in the same half for ever, even as the set
grows, so a held-out item can never drift into the tuning half.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

HOLDOUT_FRACTION = 0.20
_HASH_BUCKETS = 100


@dataclass(frozen=True)
class LabelledItem:
    """One hand-labelled example."""

    item_id: str
    text: str
    true_label: str


def is_held_out(item_id: str, fraction: float = HOLDOUT_FRACTION) -> bool:
    """Stable membership of the held-out set, from the id alone.

    No seed, no shuffle, no ordering. The same id is always on the same side,
    which is what makes "never looked at while tuning" an enforceable claim
    rather than a promise.
    """
    digest = hashlib.sha256(item_id.encode()).hexdigest()
    bucket = int(digest[:8], 16) % _HASH_BUCKETS
    return bucket < fraction * _HASH_BUCKETS


@dataclass(frozen=True)
class ClassPerformance:
    label: str
    support: int  # how many truly were this label
    correct: int
    predicted: int  # how many times the classifier said this label

    @property
    def recall(self) -> float:
        """Of the ones that really were this, how many did we catch?"""
        return self.correct / self.support if self.support else 0.0

    @property
    def precision(self) -> float:
        """When we said this, how often were we right?"""
        return self.correct / self.predicted if self.predicted else 0.0

    @property
    def never_predicted(self) -> bool:
        """The failure accuracy hides: a label the classifier never uses."""
        return self.predicted == 0 and self.support > 0


@dataclass(frozen=True)
class ClassificationReport:
    """Accuracy, the full matrix, and the worst class - never accuracy alone."""

    matrix: dict[tuple[str, str], int]  # (true, predicted) -> count
    classes: tuple[str, ...]
    total: int
    correct: int

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    def performance(self, label: str) -> ClassPerformance:
        support = sum(count for (true, _), count in self.matrix.items() if true == label)
        predicted = sum(count for (_, pred), count in self.matrix.items() if pred == label)
        return ClassPerformance(
            label=label,
            support=support,
            correct=self.matrix.get((label, label), 0),
            predicted=predicted,
        )

    def per_class(self) -> list[ClassPerformance]:
        return [self.performance(label) for label in self.classes]

    def never_predicted_classes(self) -> list[str]:
        """Labels that exist in the truth but that the classifier never gives.

        The single most important line in the report. A classifier with one of
        these is not 90% accurate in any sense that matters - it has quietly
        decided the rare catalysts do not exist, and the rare ones are the ones
        that move prices.
        """
        return [item.label for item in self.per_class() if item.never_predicted]

    def weakest_class(self) -> ClassPerformance | None:
        present = [item for item in self.per_class() if item.support > 0]
        if not present:
            return None
        return min(present, key=lambda item: item.recall)

    def as_lines(self) -> list[str]:
        lines = [f"{'label':<24}{'n':>5}{'recall':>9}{'precision':>11}"]
        for item in sorted(self.per_class(), key=lambda entry: entry.label):
            lines.append(
                f"{item.label:<24}{item.support:>5}{item.recall:>9.2f}{item.precision:>11.2f}"
            )
        lines.append(f"{'accuracy':<24}{self.total:>5}{self.accuracy:>9.2f}")
        missing = self.never_predicted_classes()
        if missing:
            lines.append(f"NEVER PREDICTED: {', '.join(missing)}")
        return lines


def build_report(pairs: list[tuple[str, str]], classes: tuple[str, ...]) -> ClassificationReport:
    """Build the matrix from (true, predicted) pairs.

    `classes` is passed in rather than inferred from the data, so a label that
    never appears in either column still shows up as a row with zero support.
    An inferred class list makes a missing category invisible, which is the
    exact failure this report exists to surface.
    """
    matrix: dict[tuple[str, str], int] = {}
    correct = 0
    for true_label, predicted in pairs:
        matrix[(true_label, predicted)] = matrix.get((true_label, predicted), 0) + 1
        if true_label == predicted:
            correct += 1
    return ClassificationReport(matrix=matrix, classes=classes, total=len(pairs), correct=correct)
