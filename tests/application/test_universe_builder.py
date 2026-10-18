"""The generated universe file: round-trips, diffs, and keeps money exact."""

from __future__ import annotations

from datetime import date, time
from pathlib import Path

import yaml

from trading_desk.application.exchange_rules import ExchangeRules, load_exchange_rules
from trading_desk.application.universe import load_universe_request
from trading_desk.application.universe_builder import (
    diff_universe,
    previous_symbols,
    previous_version,
    render_universe,
)
from trading_desk.domain.liquidity import (
    Exclusion,
    LiquidityMeasure,
    LiquidityRanking,
    LiquidityRules,
)
from trading_desk.domain.sessions import SessionHours
from trading_desk.domain.values import Money

MEASURED_ON = date(2026, 10, 16)

RULES = ExchangeRules(
    liquidity=LiquidityRules(
        min_premium_turnover=Money.from_rupees("100000"),
        min_open_interest=1000,
        min_priced_fraction=0.25,
        max_stocks=25,
    ),
    session=SessionHours(open_ist=time(9, 15), close_ist=time(15, 30)),
    effective_from=date(2026, 10, 17),
    verified=False,
    source="test",
)


def _measure(symbol: str, turnover: str, is_index: bool = False) -> LiquidityMeasure:
    return LiquidityMeasure(
        symbol=symbol,
        is_index=is_index,
        premium_turnover=Money.from_rupees(turnover),
        open_interest=12_345,
        legs_priced=30,
        legs_total=40,
    )


def _ranking() -> LiquidityRanking:
    return LiquidityRanking(
        included=(
            _measure("NIFTY", "9000000.55", is_index=True),
            _measure("RELIANCE", "800000"),
        ),
        excluded=(Exclusion("THIN", "premium turnover below the floor"),),
    )


def _render(tmp_path: Path, version: int = 1) -> Path:
    document = render_universe(
        ranking=_ranking(),
        rules=RULES,
        as_of=date(2026, 10, 17),
        version=version,
        window_days=1,
        measured_on=MEASURED_ON,
    )
    target = tmp_path / "universe.yaml"
    target.write_text(document)
    return target


class TestRender:
    def test_the_generated_file_loads_as_a_universe_request(self, tmp_path: Path) -> None:
        """The whole point of 1.2: the snapshotter must not care which file it reads."""
        request = load_universe_request(_render(tmp_path))

        assert request.index_symbols == ("NIFTY",)
        assert request.stock_symbols == ("RELIANCE",)
        assert request.version == 1
        assert "survey" in request.source

    def test_turnover_survives_as_an_exact_decimal_string(self, tmp_path: Path) -> None:
        """A money figure written as a YAML float has stopped being exact."""
        raw = yaml.safe_load(_render(tmp_path).read_text())

        turnover = raw["measurements"]["NIFTY"]["premium_turnover_rupees"]
        assert isinstance(turnover, str)
        assert turnover == "9000000.55"

    def test_exclusions_are_recorded_with_reasons(self, tmp_path: Path) -> None:
        raw = yaml.safe_load(_render(tmp_path).read_text())

        assert raw["exclusions"] == [
            {"symbol": "THIN", "reason": "premium turnover below the floor"}
        ]

    def test_window_days_is_recorded_so_one_day_is_not_read_as_twenty(self, tmp_path: Path) -> None:
        raw = yaml.safe_load(_render(tmp_path).read_text())

        assert raw["window_days"] == 1
        assert raw["rules_verified"] is False

    def test_measured_on_records_which_session_was_measured(self, tmp_path: Path) -> None:
        """Without this, a universe built on Friday's figures looks live.

        The date comes from the calendar, not the feed: Dhan's exchange_time is the
        time it answered, so it reads as "now" even over stale volume.
        """
        raw = yaml.safe_load(_render(tmp_path).read_text())

        assert raw["measured_on"] == MEASURED_ON

    def test_the_file_says_not_to_hand_edit_it(self, tmp_path: Path) -> None:
        assert "Do not hand-edit" in _render(tmp_path).read_text()


class TestPreviousFile:
    def test_missing_file_reads_as_empty_rather_than_raising(self, tmp_path: Path) -> None:
        absent = tmp_path / "nope.yaml"

        assert previous_symbols(absent) == ()
        assert previous_version(absent) == 0

    def test_reads_symbols_and_version_back(self, tmp_path: Path) -> None:
        target = _render(tmp_path, version=4)

        assert previous_symbols(target) == ("NIFTY", "RELIANCE")
        assert previous_version(target) == 4


class TestDiff:
    def test_reports_added_removed_and_kept(self) -> None:
        diff = diff_universe(("A", "B", "C"), ("B", "C", "D"))

        assert diff.added == ("D",)
        assert diff.removed == ("A",)
        assert diff.kept == ("B", "C")
        assert diff.changed

    def test_identical_lists_report_no_change(self) -> None:
        diff = diff_universe(("A", "B"), ("B", "A"))

        assert not diff.changed
        assert "No change" in diff.summary()

    def test_first_run_is_all_additions(self) -> None:
        diff = diff_universe((), ("A", "B"))

        assert diff.added == ("A", "B")
        assert diff.removed == ()


def test_the_shipped_rules_file_loads() -> None:
    """Catches a YAML typo in config before a half-hour survey hits it."""
    rules = load_exchange_rules()

    assert rules.liquidity.max_stocks == 25
    assert rules.liquidity.min_premium_turnover.paise > 0
    assert not rules.verified  # still unreviewed; flip this test when it is signed off
