"""The labelling sheet: writing it, reading it back, and refusing bad input."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from trading_desk.application.label_set import (
    LabelFileError,
    load_labels,
    score_predictions,
    summarise,
    write_sample,
)
from trading_desk.domain.classification_report import LabelledItem, is_held_out
from trading_desk.domain.enums import SourceTier
from trading_desk.domain.news import NewsArticle

START = datetime(2026, 8, 1, 4, 0, tzinfo=UTC)


def _articles(count: int) -> list[NewsArticle]:
    return [
        NewsArticle(
            article_id=f"a-{index}",
            headline=f"Headline {index}",
            body=f"Body text for article {index}. " * 40,
            url=f"https://example.invalid/{index}",
            publisher="Example Wire",
            source_tier=SourceTier.TIER_2,
            published_at=START + timedelta(hours=index),
            ingested_at=START + timedelta(hours=index, minutes=5),
        )
        for index in range(count)
    ]


class TestWritingTheSheet:
    def test_it_writes_the_requested_number_of_rows(self, tmp_path: Path) -> None:
        destination = tmp_path / "labels.csv"

        written = write_sample(_articles(50), destination=destination, size=10)

        assert written == 10
        assert len(destination.read_text().splitlines()) == 11  # plus the header

    def test_the_true_label_column_is_left_blank(self, tmp_path: Path) -> None:
        destination = tmp_path / "labels.csv"
        write_sample(_articles(5), destination=destination, size=5)

        lines = destination.read_text().splitlines()
        assert lines[0].endswith("true_label")
        assert all(line.endswith(",") for line in lines[1:])

    def test_an_existing_file_is_never_overwritten(self, tmp_path: Path) -> None:
        """Half a day of labelling is not something a re-run gets to destroy."""
        destination = tmp_path / "labels.csv"
        write_sample(_articles(5), destination=destination, size=5)

        with pytest.raises(LabelFileError, match="will not be overwritten"):
            write_sample(_articles(5), destination=destination, size=5)

    def test_the_rows_are_not_in_date_order(self, tmp_path: Path) -> None:
        """Reading 200 articles in date order means reading them in topic
        clusters, and by item 40 the labeller answers from momentum."""
        destination = tmp_path / "labels.csv"
        write_sample(_articles(60), destination=destination, size=30)

        ids = [line.split(",")[0] for line in destination.read_text().splitlines()[1:]]
        assert ids != sorted(ids, key=lambda item: int(item.split("-")[1]))


class TestReadingItBack:
    def _write(self, tmp_path: Path, rows: list[str]) -> Path:
        path = tmp_path / "labels.csv"
        header = "item_id,published_at,publisher,headline,body_excerpt,true_label"
        path.write_text("\n".join([header, *rows]) + "\n")
        return path

    def test_a_valid_file_loads(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            [
                "a-1,2026-08-01T04:00:00+00:00,Wire,Headline,Body,EARNINGS_SURPRISE",
                "a-2,2026-08-01T05:00:00+00:00,Wire,Headline,Body,NONE",
            ],
        )

        items = load_labels(path)

        assert [item.true_label for item in items] == ["EARNINGS_SURPRISE", "NONE"]

    def test_a_blank_label_raises_rather_than_being_skipped(self, tmp_path: Path) -> None:
        """Skipping bad rows silently shrinks the test set, and 90% on 140
        items reported as 200 is a number nobody can trust."""
        path = self._write(tmp_path, ["a-1,2026-08-01T04:00:00+00:00,Wire,H,B,"])

        with pytest.raises(LabelFileError, match="line 2: true_label is empty"):
            load_labels(path)

    def test_a_misspelled_label_names_the_line_and_the_valid_options(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, ["a-1,2026-08-01T04:00:00+00:00,Wire,H,B,EARNINGS"])

        with pytest.raises(LabelFileError) as caught:
            load_labels(path)

        assert "line 2" in str(caught.value)
        assert "EARNINGS_SURPRISE" in str(caught.value)

    def test_a_missing_file_says_which_command_makes_one(self, tmp_path: Path) -> None:
        with pytest.raises(LabelFileError, match="label-news"):
            load_labels(tmp_path / "nothing.csv")


class TestScoring:
    def _items(self, count: int) -> list[LabelledItem]:
        return [
            LabelledItem(item_id=f"a-{index}", text="x", true_label="NONE")
            for index in range(count)
        ]

    def test_held_out_and_tuning_are_scored_separately(self) -> None:
        items = self._items(200)
        predictions = {item.item_id: "NONE" for item in items}

        held = score_predictions(items, predictions, held_out_only=True)
        tuning = score_predictions(items, predictions, held_out_only=False)

        assert held.total + tuning.total == 200
        assert held.total > 0 and tuning.total > 0

    def test_scoring_the_held_out_half_uses_only_held_out_items(self) -> None:
        items = self._items(200)
        predictions = {item.item_id: "NONE" for item in items}

        held = score_predictions(items, predictions, held_out_only=True)

        assert held.total == sum(1 for item in items if is_held_out(item.item_id))

    def test_an_unpredicted_item_is_left_out_rather_than_counted_wrong(self) -> None:
        """A classifier that errored on an item has not got it wrong; counting
        it as wrong would understate accuracy and hide the real failure."""
        items = self._items(10)
        predictions = {items[0].item_id: "NONE"}

        report = score_predictions(items, predictions, held_out_only=is_held_out(items[0].item_id))

        assert report.total == 1


def test_the_summary_counts_both_halves() -> None:
    items = [
        LabelledItem(
            item_id=f"a-{index}", text="x", true_label="NONE" if index % 2 else "EARNINGS_SURPRISE"
        )
        for index in range(100)
    ]

    summary = summarise(items)

    assert summary.total == 100
    assert summary.held_out + summary.tuning == 100
    assert set(summary.by_label) == {"NONE", "EARNINGS_SURPRISE"}
