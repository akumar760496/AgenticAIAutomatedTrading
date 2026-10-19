"""Checking that a written sentence did not invent its numbers.

The synthesis model writes the thesis claim and the invalidation condition.
Prose is the one thing in this system an LLM is allowed to produce, and the
failure mode of prose is a number: "profit rose 23%" reads exactly like a fact
whether or not anybody measured 23.

So every digit string in the written text must also appear in the facts the
model was given. That is a cheap, mechanical check and it catches the case that
matters - a figure that came from nowhere - without trying to judge whether the
sentence is *right*, which is not checkable here.

**What it deliberately does not do.** It does not check wording, direction or
reasoning, and it does not stop a model from drawing a silly conclusion from
real numbers. It stops one specific thing: a number nobody measured appearing
in a briefing as though somebody had.
"""

from __future__ import annotations

import re

# Digit runs, with internal commas, decimal points and percent signs stripped
# off the edges by the pattern rather than by cleanup afterwards.
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> list[str]:
    """Every number in the text, normalised so 1,400 and 1400 compare equal."""
    return [match.group().replace(",", "") for match in _NUMBER.finditer(text)]


def ungrounded_numbers(text: str, source_facts: str) -> list[str]:
    """Numbers in `text` that do not appear anywhere in `source_facts`.

    Comparison is on the normalised digit string, so a model that reformats
    1,23,456 as 123456 is not accused of inventing it. The trade is that a
    number which happens to be a substring of a larger supplied number passes;
    that is the right way round, because a false accusation here drops a
    perfectly good idea.
    """
    available = set(numbers_in(source_facts))
    normalised_source = source_facts.replace(",", "")
    return [
        number
        for number in numbers_in(text)
        if number not in available and number not in normalised_source
    ]
