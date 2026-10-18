"""Entity resolution, and the traps docs/12 names explicitly.

FR-3.4 requires 100% on this fixture set. Not 95%: a wrong link puts a thesis on
the wrong instrument, which is worse than no thesis because it looks researched.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from trading_desk.application.news_ingest import load_aliases_from_file
from trading_desk.application.universe import load_universe_request
from trading_desk.domain.entities import AliasTable, normalise, resolve_entities

SHIPPED = load_aliases_from_file()

# The fixture set. Each case is (text, the symbols that must resolve).
# The pairs docs/12 calls out are first, because they are the ones a substring
# matcher gets wrong.
FIXTURES: list[tuple[str, set[str]]] = [
    # "Tata Motors" vs "Tata Steel" vs "Tata Consultancy"
    ("Tata Motors reported record quarterly sales", set()),
    ("Tata Steel commissioned a new blast furnace", {"TATASTEEL"}),
    ("Tata Consultancy Services won a large deal", {"TCS"}),
    ("Tata Power signed a solar agreement", set()),
    ("Tata Steel will supply Tata Motors from January", {"TATASTEEL"}),
    # "M&M" vs "Mahindra" vs "Tech Mahindra"
    ("Mahindra and Mahindra unveiled a new SUV", {"M&M"}),
    ("Mahindra & Mahindra unveiled a new SUV", {"M&M"}),
    ("M&M reported monthly tractor volumes", {"M&M"}),
    ("Tech Mahindra announced a leadership change", set()),
    ("Mahindra Logistics opened a warehouse", set()),
    # "Bajaj Finance" vs "Bajaj Finserv"
    ("Bajaj Finance reported higher disbursements", {"BAJFINANCE"}),
    ("Bajaj Finserv reported higher disbursements", set()),
    ("Bajaj Auto exported a record number of units", set()),
    # "HDFC" vs "HDFC Bank"
    ("HDFC Bank raised its deposit rates", {"HDFCBANK"}),
    ("HDFC Life launched a new policy", set()),
    ("HDFC AMC reported inflows", set()),
    # Adani, where several listings share a prefix
    ("Adani Enterprises completed a fundraise", {"ADANIENT"}),
    ("Adani Ports handled record cargo", set()),
    ("Adani Green commissioned capacity", set()),
    # ICICI and SBI families
    ("ICICI Bank cut its lending rate", {"ICICIBANK"}),
    ("ICICI Prudential launched a fund", set()),
    ("State Bank of India raised capital", {"SBIN"}),
    ("SBI Life reported premium growth", set()),
    # Reliance, where an unrelated listing shares the name
    ("Reliance Industries announced a demerger", {"RELIANCE"}),
    ("Reliance Power restructured its debt", set()),
    # Punctuation and abbreviation forms
    ("L&T won a metro contract", {"LT"}),
    ("Larsen & Toubro won a metro contract", {"LT"}),
    ("Larsen and Toubro won a metro contract", {"LT"}),
    # Short tickers must not match inside longer words
    ("The witch brewed a potion", set()),
    ("ITC reported cigarette volumes", {"ITC"}),
    ("SBIC is an unrelated entity", set()),
    # Several universe names in one story
    ("Both Infosys and TCS beat estimates", {"INFY", "TCS"}),
]


class TestNormalise:
    def test_lowercases_and_collapses_whitespace(self) -> None:
        assert normalise("  Tata   STEEL  ") == "tata steel"

    def test_keeps_ampersands_because_they_are_part_of_names(self) -> None:
        assert normalise("M&M") == "m&m"
        assert normalise("Larsen & Toubro") == "larsen & toubro"

    def test_strips_other_punctuation(self) -> None:
        assert normalise("Infosys, Ltd.") == "infosys ltd"


class TestFixtureSet:
    @pytest.mark.parametrize(("text", "expected"), FIXTURES)
    def test_resolves_correctly(self, text: str, expected: set[str]) -> None:
        found = {match.symbol for match in resolve_entities(text, SHIPPED)}

        assert found == expected

    def test_the_whole_set_passes(self) -> None:
        """FR-3.4 states 100%, so the aggregate is asserted as well as the parts -
        a parametrised failure is one red test among many and easy to wave at."""
        wrong = [
            text
            for text, expected in FIXTURES
            if {match.symbol for match in resolve_entities(text, SHIPPED)} != expected
        ]

        assert wrong == []


class TestMatchReporting:
    def test_reports_which_alias_matched(self) -> None:
        """So a wrong link can be traced to the entry that caused it."""
        found = resolve_entities("Larsen & Toubro won a contract", SHIPPED)

        assert found[0].matched_alias == "Larsen & Toubro"

    def test_prefers_the_longest_alias(self) -> None:
        table = AliasTable(aliases={"X": ("Acme", "Acme Industries Limited")})

        found = resolve_entities("Acme Industries Limited filed", table)

        assert found[0].matched_alias == "Acme Industries Limited"


class TestShippedTable:
    def test_covers_every_universe_symbol(self) -> None:
        stocks = set(load_universe_request().stock_symbols)

        assert stocks <= set(SHIPPED.aliases), (
            f"no aliases for: {sorted(stocks - set(SHIPPED.aliases))}"
        )

    def test_no_bare_group_name_is_an_alias(self) -> None:
        """ "Tata", "Bajaj", "Adani", "Mahindra" are ambiguous between listings.
        Admitting one would silently break several fixtures at once."""
        ambiguous = {"tata", "bajaj", "adani", "mahindra", "reliance", "icici", "hdfc"}

        for symbol, names in SHIPPED.aliases.items():
            for name in names:
                assert normalise(name) not in ambiguous, f"{symbol} claims bare '{name}'"

    def test_the_file_parses_as_yaml(self) -> None:
        raw = yaml.safe_load(Path("config/aliases.yaml").read_text())

        assert raw["version"] == 1
        assert raw["aliases"]
