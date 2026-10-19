"""Verify step 0.6 - ports and fakes.

The claim being checked: every external dependency has a Protocol, every Protocol
has a fake, no fake pretends to place an order, and the suite cannot reach the
network.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests import fakes

from scripts.verify._runner import CheckResult
from trading_desk.ports.backup import BackupToolPort
from trading_desk.ports.bar_store import BarStorePort
from trading_desk.ports.broker import BrokerReadPort
from trading_desk.ports.chain_archive import ChainArchivePort
from trading_desk.ports.chat import ChatPort
from trading_desk.ports.clock import Clock
from trading_desk.ports.cycle_store import CycleStore
from trading_desk.ports.expiry_store import ExpiryStorePort
from trading_desk.ports.history import HistoricalBarsPort
from trading_desk.ports.journal import JournalPort
from trading_desk.ports.llm import LlmPort
from trading_desk.ports.llm_ledger import LlmLedger
from trading_desk.ports.market_data import MarketDataPort
from trading_desk.ports.news import FilingsPort, NewsPort
from trading_desk.ports.news_store import NewsStorePort
from trading_desk.ports.notifier import NotifierPort
from trading_desk.ports.option_chain import OptionChainPort
from trading_desk.ports.tracing import Tracer

TITLE = "Ports and fakes"

ROOT = Path(__file__).resolve().parents[2]

# Nineteen ports, eighteen files: ports/news.py declares both NewsPort and
# FilingsPort. `CycleStore` was added in 3.4, `ChatPort` in 3.6.
# Counting files here instead of ports is what let ChainArchivePort go unchecked
# from step 1.1b until a Phase 0 re-run in step 1.2 found the mismatch.
EXPECTED_PORT_COUNT = 19

# `GovernedLlmPort` (ports/llm.py) is deliberately NOT in this inventory and has
# no fake. It is not an external dependency - it is the internal seam that lets
# `agents/` depend on an interface rather than on `application.llm_governor`.
# The agent tests build a real `CostGovernedLlm` over `FakeLlm`, which exercises
# the cap and the ledger for free; a fake governor would make them weaker, not
# more isolated. Added in step 3.2.
EXPECTED_PORTS = [
    BackupToolPort,
    BarStorePort,
    BrokerReadPort,
    ChainArchivePort,
    ChatPort,
    Clock,
    CycleStore,
    ExpiryStorePort,
    HistoricalBarsPort,
    FilingsPort,
    JournalPort,
    LlmLedger,
    LlmPort,
    MarketDataPort,
    NewsPort,
    NewsStorePort,
    NotifierPort,
    OptionChainPort,
    Tracer,
]

# Anything that could put an order into the market. None of these may exist in v1.
ORDER_METHODS = [
    "place_order",
    "modify_order",
    "cancel_order",
    "submit_order",
    "square_off",
    "exit_position",
]


def _fake_count() -> int:
    return len(fakes.__all__)


def _ports_without_a_fake() -> list[str]:
    """Every port's structural shape must be satisfied by some fake."""
    fake_classes = [getattr(fakes, name) for name in fakes.__all__]
    orphans = []
    for port in EXPECTED_PORTS:
        methods = [name for name in dir(port) if not name.startswith("_")]
        satisfied = any(all(hasattr(fake, method) for method in methods) for fake in fake_classes)
        if not satisfied:
            orphans.append(port.__name__)
    return orphans


def _order_methods_anywhere() -> list[str]:
    """No port and no fake may expose a way to place an order."""
    found: list[str] = []
    for port in EXPECTED_PORTS:
        found.extend(f"{port.__name__}.{m}" for m in ORDER_METHODS if m in dir(port))
    for name in fakes.__all__:
        fake = getattr(fakes, name)
        found.extend(f"{name}.{m}" for m in ORDER_METHODS if m in dir(fake))
    return found


def _order_adapter_files() -> list[str]:
    """FR-13.6: no order-placing adapter may exist in the v1 codebase."""
    adapters = ROOT / "src" / "trading_desk" / "adapters"
    return [path.name for path in adapters.rglob("*order*.py")]


def _suite_passes_offline() -> tuple[bool, str]:
    """The headline claim of this step, run as its own subprocess."""
    result = subprocess.run(
        ["uv", "run", "pytest", "-q", "--no-header"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    last_line = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "no output"
    return result.returncode == 0, last_line


def run() -> list[CheckResult]:
    orphans = _ports_without_a_fake()
    order_methods = _order_methods_anywhere()
    order_files = _order_adapter_files()
    suite_ok, suite_detail = _suite_passes_offline()

    return [
        CheckResult(
            name="ports_defined",
            passed=len(EXPECTED_PORTS) == EXPECTED_PORT_COUNT,
            measured=str(len(EXPECTED_PORTS)),
            requirement=f"need {EXPECTED_PORT_COUNT}",
        ),
        CheckResult(
            name="fakes_defined",
            passed=_fake_count() == EXPECTED_PORT_COUNT,
            measured=str(_fake_count()),
            requirement=f"need {EXPECTED_PORT_COUNT} (one per port)",
        ),
        CheckResult(
            name="every_port_has_a_fake",
            passed=not orphans,
            measured=f"orphans: {orphans}" if orphans else "all satisfied",
            requirement="need 0 orphans",
        ),
        CheckResult(
            name="no_order_methods",
            passed=not order_methods,
            measured=f"found {order_methods}" if order_methods else "none",
            requirement="need 0 - v1 cannot place an order",
        ),
        CheckResult(
            name="no_order_adapter_file",
            passed=not order_files,
            measured=f"found {order_files}" if order_files else "none",
            requirement="need 0 (FR-13.6)",
        ),
        CheckResult(
            name="suite_passes_offline",
            passed=suite_ok,
            measured=suite_detail,
            requirement="need all tests green with the network blocked",
        ),
    ]
