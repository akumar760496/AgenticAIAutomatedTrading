"""Runs the verification checks for one build step and prints PASS/FAIL per check.

Output contract (docs/12-verification.md section 1): every check prints the value it
measured and the threshold it was compared against. Never a bare "OK" - you want to
see a number drift before it crosses the line.

Usage:
    python -m scripts.verify 0_1
    make verify STEP=0_1
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class CheckResult:
    """One verification check. `measured` and `requirement` are printed, not just passed."""

    name: str
    passed: bool
    measured: str
    requirement: str


def run_step(step_id: str) -> int:
    """Import scripts/verify/step_<step_id>.py, run its checks, print them. Returns exit code."""
    module_name = f"scripts.verify.step_{step_id}"
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError:
        print(f"No verify script for step {step_id.replace('_', '.')} ({module_name}.py)")
        print("A step with no verify script is not finished - see docs/12-verification.md.")
        return 2

    title = getattr(module, "TITLE", step_id)
    results: list[CheckResult] = module.run()

    print(f"STEP {step_id.replace('_', '.')} — {title}")
    name_width = max(len(r.name) for r in results)
    for result in results:
        mark = "PASS" if result.passed else "FAIL"
        print(
            f"  [{mark}] {result.name:<{name_width}} = {result.measured:<24} ({result.requirement})"
        )

    # Always counts *passes*, both ways round. It used to print the number of
    # failures on the FAIL line and the number of passes on the PASS line, so
    # "FAIL (1 of 7)" meant six checks had passed. Read as a score, it looked
    # like a near-total collapse.
    failed = [r for r in results if not r.passed]
    passed = len(results) - len(failed)
    if failed:
        print(f"  RESULT: FAIL ({passed} of {len(results)} passed, {len(failed)} failed)")
        return 1
    print(f"  RESULT: PASS ({passed} of {len(results)} passed)")
    return 0


EXPECTED_ARG_COUNT = 2  # script name + step id


def main() -> int:
    if len(sys.argv) != EXPECTED_ARG_COUNT:
        print("Usage: python -m scripts.verify <step_id>   e.g. 0_1")
        return 2
    return run_step(sys.argv[1])
