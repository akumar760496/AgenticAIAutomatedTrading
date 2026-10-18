"""Linking prose to instruments, by whole-phrase alias and never by substring.

The failure this prevents is specific and expensive. "Tata Motors" contains
"Tata", which also begins "Tata Steel" and "Tata Consultancy Services". A
substring match links a Tata Motors story to TATASTEEL, and a thesis on the wrong
instrument is worse than no thesis, because it looks researched.

Three rules, each bought by a trap in the fixture set:

1. **Whole phrase, on normalised text.** Lowercase, strip punctuation, collapse
   whitespace, then match on word boundaries. So "L&T" and "L & T" both work and
   "ITC" does not match inside "WITCH".
2. **Longest alias first.** "Bajaj Finserv" is tested before any shorter alias
   that might be a prefix of it, so the specific name wins.
3. **Confusables block, they do not merely fail to match.** Finding "Tata Motors"
   in a body actively prevents a TATASTEEL link from that phrase. Relying on the
   alias list simply not containing the wrong name is a guarantee that decays the
   first time somebody adds "Tata" as a convenience.

Pure module: aliases are supplied, nothing is loaded or looked up here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Keep & and alphanumerics: "M&M" and "L&T" are names, not punctuation.
_PUNCTUATION = re.compile(r"[^a-z0-9&\s]+")
_WHITESPACE = re.compile(r"\s+")


def normalise(text: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace.

    Done once per text and once per alias so matching compares like with like.
    """
    lowered = text.lower()
    without_punctuation = _PUNCTUATION.sub(" ", lowered)
    return _WHITESPACE.sub(" ", without_punctuation).strip()


@dataclass(frozen=True)
class AliasTable:
    """Aliases by symbol, plus names that must never resolve.

    Built once at load and reused: compiling a pattern per article per symbol
    would dominate the cost of ingestion.
    """

    aliases: dict[str, tuple[str, ...]]
    confusable: tuple[str, ...] = ()

    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted(self.aliases))


@dataclass(frozen=True)
class EntityMatch:
    """One instrument found in a text, and the alias that found it.

    The alias is kept so a wrong link can be traced to the entry that caused it
    rather than guessed at.
    """

    symbol: str
    matched_alias: str


def _contains_phrase(haystack: str, phrase: str) -> bool:
    """Whole-phrase match on already-normalised text.

    Word boundaries rather than `in`: "itc" must not match inside "witch", and
    "sbi" must not match inside "sbic". `re.escape` because aliases contain & and
    would otherwise be read as a pattern.
    """
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", haystack) is not None


def resolve_entities(text: str, table: AliasTable) -> list[EntityMatch]:
    """Every universe instrument named in `text`, sorted by symbol.

    A confusable name found in the text removes nothing already matched by a
    *different, longer* phrase - it only prevents a match that the confusable
    itself would have caused. "Tata Steel announced a deal with Tata Motors"
    correctly resolves to TATASTEEL and nothing else.
    """
    haystack = normalise(text)

    blocked_spans = [
        normalise(name) for name in table.confusable if _contains_phrase(haystack, normalise(name))
    ]

    matches = []
    for symbol in sorted(table.aliases):
        # Longest first, so the most specific alias is the one reported.
        for alias in sorted(table.aliases[symbol], key=len, reverse=True):
            normalised_alias = normalise(alias)
            if not _contains_phrase(haystack, normalised_alias):
                continue
            if _is_only_inside_a_confusable(haystack, normalised_alias, blocked_spans):
                continue
            matches.append(EntityMatch(symbol=symbol, matched_alias=alias))
            break

    return matches


def _is_only_inside_a_confusable(haystack: str, alias: str, blocked: list[str]) -> bool:
    """Whether every occurrence of `alias` sits inside a confusable name.

    "Tata Steel" inside "Tata Steel Limited" is fine. "Tata" inside "Tata Motors"
    is not - but only if "Tata" never appears outside it, because a body can
    mention both companies and the universe one still counts.
    """
    for match in re.finditer(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", haystack):
        inside_any = any(
            _spans_overlap(match.start(), match.end(), blocked_match)
            for name in blocked
            for blocked_match in re.finditer(re.escape(name), haystack)
        )
        if not inside_any:
            return False
    return True


def _spans_overlap(start: int, end: int, other: re.Match[str]) -> bool:
    return start >= other.start() and end <= other.end()
