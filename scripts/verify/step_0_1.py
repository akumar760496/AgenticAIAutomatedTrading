"""Verify step 0.1 - repository and toolchain.

Checks that the scaffold exists and that the guards are wired. It deliberately does
not run ruff/mypy/pytest itself: `make check` does that, and duplicating it here
would mean two places to keep in step.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

from scripts.verify._runner import CheckResult

TITLE = "Repository and toolchain"

ROOT = Path(__file__).resolve().parents[2]

EXPECTED_LAYERS = [
    "domain",
    "ports",
    "application",
    "agents",
    "adapters",
    "infra",
    "interfaces",
]

REQUIRED_DEV_TOOLS = [
    "ruff",
    "mypy",
    "pytest",
    "hypothesis",
    "import-linter",
    "pre-commit",
    "detect-secrets",
]


def _file_exists(name: str) -> bool:
    return (ROOT / name).is_file()


def _python_version() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def _dev_dependency_names() -> list[str]:
    """Read dev extras from pyproject, stripped of version specifiers."""
    with (ROOT / "pyproject.toml").open("rb") as handle:
        config = tomllib.load(handle)
    specs = config["project"]["optional-dependencies"]["dev"]
    return [spec.split(">")[0].split("=")[0].split("<")[0].strip() for spec in specs]


def _git_tracked_file_count() -> int:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return 0
    return len([line for line in result.stdout.splitlines() if line])


def _env_is_ignored() -> bool:
    """git check-ignore exits 0 when the path is ignored. .env must never be committable."""
    result = subprocess.run(
        ["git", "check-ignore", "-q", ".env"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def run() -> list[CheckResult]:
    present_layers = [
        layer
        for layer in EXPECTED_LAYERS
        if (ROOT / "src" / "trading_desk" / layer / "__init__.py").is_file()
    ]
    dev_tools = _dev_dependency_names()
    missing_tools = [tool for tool in REQUIRED_DEV_TOOLS if tool not in dev_tools]
    scaffold_files = ["pyproject.toml", "Makefile", ".gitignore", ".pre-commit-config.yaml"]
    missing_files = [name for name in scaffold_files if not _file_exists(name)]
    tracked = _git_tracked_file_count()

    return [
        CheckResult(
            name="python_version",
            passed=_python_version() == "3.12",
            measured=_python_version(),
            requirement="need 3.12",
        ),
        CheckResult(
            name="layer_packages",
            passed=len(present_layers) == len(EXPECTED_LAYERS),
            measured=f"{len(present_layers)} of {len(EXPECTED_LAYERS)}",
            requirement="need all 7",
        ),
        CheckResult(
            name="scaffold_files",
            passed=not missing_files,
            measured=f"missing {missing_files}" if missing_files else "all present",
            requirement="need pyproject, Makefile, gitignore, pre-commit",
        ),
        CheckResult(
            name="dev_tools_pinned",
            passed=not missing_tools,
            measured=f"missing {missing_tools}" if missing_tools else f"{len(dev_tools)} pinned",
            requirement="need all 7 tools",
        ),
        CheckResult(
            name="git_tracked_files",
            passed=tracked > 0,
            measured=str(tracked),
            requirement="need > 0 (repo initialised and committed)",
        ),
        CheckResult(
            name="dotenv_is_gitignored",
            passed=_env_is_ignored(),
            measured=str(_env_is_ignored()),
            requirement="need true - a secret must not be committable",
        ),
    ]
