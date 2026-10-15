"""Scaffold tests: the project layout and the toolchain itself.

These look trivial and are not. They fail loudly if a layer package is deleted or
renamed, which is exactly when the layering rule silently stops being enforced.
"""

from __future__ import annotations

import sys
from pathlib import Path

import ativa

LAYERS = ["domain", "ports", "application", "agents", "adapters", "infra", "interfaces"]

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "ativa"


def test_runs_on_python_3_12() -> None:
    # Quant wheels lag new releases; 3.14 has no wheels for several of them yet.
    assert sys.version_info[:2] == (3, 12)


def test_every_layer_package_exists() -> None:
    missing = [layer for layer in LAYERS if not (PACKAGE_ROOT / layer / "__init__.py").is_file()]
    assert missing == []


def test_every_layer_documents_what_belongs_in_it() -> None:
    """A layer without a docstring becomes a junk drawer. Cheap rule, real effect."""
    for layer in LAYERS:
        source = (PACKAGE_ROOT / layer / "__init__.py").read_text()
        assert source.lstrip().startswith('"""'), f"{layer}/__init__.py has no docstring"


def test_package_docstring_states_the_layer_rule() -> None:
    assert ativa.__doc__ is not None
    assert "inward" in ativa.__doc__
