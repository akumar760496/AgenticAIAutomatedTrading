"""The domain layer must not read a clock, do I/O, or print.

import-linter stops domain importing an I/O library. It cannot stop
`date.today()`, because `datetime` is a legitimate *type* import here - an option
expiry is a date. So this test reads the domain source directly.

It is deliberately crude. A clever AST walk would be harder to read and would not
catch more of what actually goes wrong.

**The one piece of care it does take** is matching on a word boundary. A plain
substring search for "open(" also matches `is_open(`, which is a property name
and not a file being read - exactly the kind of false positive that teaches
people to rename good code to appease a test. `test_the_scan_still_catches_a_real_call`
proves the boundary did not weaken the guard.
"""

from __future__ import annotations

import re
from pathlib import Path

DOMAIN_ROOT = Path(__file__).resolve().parents[2] / "src" / "trading_desk" / "domain"

# Calls that read the clock. Domain functions take the time as an argument instead.
CLOCK_CALLS = [
    "datetime.now(",
    "datetime.utcnow(",
    "date.today(",
    "time.time(",
    "time.monotonic(",
]

# Side effects that have no place in pure logic.
FORBIDDEN_CALLS = [
    "open(",
    "print(",
    "requests.",
    "httpx.",
]


def _domain_modules() -> list[Path]:
    return sorted(DOMAIN_ROOT.rglob("*.py"))


def test_there_are_domain_modules_to_check() -> None:
    """Guards against the test passing because it found nothing."""
    assert len(_domain_modules()) >= 3


def _calls_in(source: str, calls: list[str]) -> list[str]:
    """Which of `calls` appear as a call, not as part of a longer name."""
    found = []
    for call in calls:
        # \b before the name, so `is_open(` and `self.now(` do not match while
        # `open(`, `.open(` and `datetime.now(` all do.
        if re.search(r"\b" + re.escape(call), source):
            found.append(call)
    return found


def test_the_scan_still_catches_a_real_call() -> None:
    """The boundary must not have made the check toothless."""
    assert _calls_in("data = open(path)", FORBIDDEN_CALLS) == ["open("]
    assert _calls_in("with Path(p).open() as handle:", ["open("]) == ["open("]
    assert _calls_in("stamp = datetime.now(UTC)", CLOCK_CALLS) == ["datetime.now("]
    assert _calls_in("if self.is_open(): pass", FORBIDDEN_CALLS) == []


def test_no_domain_module_reads_the_clock() -> None:
    offenders = []
    for module in _domain_modules():
        for call in _calls_in(module.read_text(), CLOCK_CALLS):
            offenders.append(f"{module.name}: {call}")

    assert offenders == [], (
        f"Domain code must take the time as an argument, not read it: {offenders}"
    )


def test_no_domain_module_does_io() -> None:
    offenders = []
    for module in _domain_modules():
        for call in _calls_in(module.read_text(), FORBIDDEN_CALLS):
            offenders.append(f"{module.name}: {call}")

    assert offenders == [], f"Domain code must be pure: {offenders}"
