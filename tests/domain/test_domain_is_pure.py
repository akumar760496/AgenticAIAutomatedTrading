"""The domain layer must not read a clock, do I/O, or print.

import-linter stops domain importing an I/O library. It cannot stop
`date.today()`, because `datetime` is a legitimate *type* import here - an option
expiry is a date. So this test reads the domain source directly.

It is deliberately crude. A clever AST walk would be harder to read and would not
catch more of what actually goes wrong.
"""

from __future__ import annotations

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


def test_no_domain_module_reads_the_clock() -> None:
    offenders = []
    for module in _domain_modules():
        source = module.read_text()
        for call in CLOCK_CALLS:
            if call in source:
                offenders.append(f"{module.name}: {call}")

    assert offenders == [], (
        f"Domain code must take the time as an argument, not read it: {offenders}"
    )


def test_no_domain_module_does_io() -> None:
    offenders = []
    for module in _domain_modules():
        source = module.read_text()
        for call in FORBIDDEN_CALLS:
            if call in source:
                offenders.append(f"{module.name}: {call}")

    assert offenders == [], f"Domain code must be pure: {offenders}"
