"""Verify step 3.3 - the other four specialists.

`docs/10`: done when "each runs standalone against fakes and returns valid
typed output; a malformed-output fixture fails the cycle rather than degrading
it."

`docs/12` adds the one that is easy to skip and hardest to recover from:

> "Determinism boundary: run the same cycle on the same inputs three times. The
> *prose* will differ; the **gate values must be identical**, because gates are
> arithmetic. Differing gate values mean an LLM has leaked into the decision
> path, which is the most important invariant in the system."

That check is cheap here and expensive later, so it runs now, per specialist.

Four of the checks below are measurements against the real filing archive and
the real config rather than against fixtures. They are what turns "the veto
table looks reasonable" into a number, and two of them are the evidence behind
ISS-026 and ISS-027.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Protocol, get_args, get_type_hints

from sqlalchemy import text
from tests.agents.fixtures import NIFTY, RELIANCE, chain, flat_bars, nifty_chain
from tests.fakes import FakeBarStore, FakeBrokerRead, FakeJournal, FakeNewsStore, FakeOptionChain

from scripts.verify._runner import CheckResult
from trading_desk.agents.fundamental.agent import FundamentalAgent, FundamentalView
from trading_desk.agents.options.agent import OptionsAgent, OptionsView
from trading_desk.agents.portfolio.agent import PortfolioAgent, PortfolioView
from trading_desk.agents.technical.agent import SIGNALS_USED, TechnicalAgent, TechnicalView
from trading_desk.domain.enums import Direction, OptionRight, SetupId
from trading_desk.domain.governance import VETOING_FLAGS, flag_for
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.news import Filing
from trading_desk.domain.options.selection import StructureKind, choose_structure
from trading_desk.domain.setups.thresholds import load_setup_thresholds
from trading_desk.domain.signals.registry import SignalStatus, require_registered
from trading_desk.domain.specialists import SpecialistFinding, SpecialistName
from trading_desk.domain.values import Money, Price, Quantity
from trading_desk.domain.vol import IvRank, IvSource
from trading_desk.infra.config import load_infra_settings
from trading_desk.infra.db import create_db_engine
from trading_desk.ports.broker import BrokerPosition

TITLE = "The other four specialists"

ROOT = Path(__file__).resolve().parents[2]

EXPECTED_SPECIALISTS = 4
DETERMINISM_RUNS = 3
STOCK_EXPIRY = date(2026, 11, 5)
INDEX_EXPIRY = date(2026, 10, 26)
AS_OF = date(2026, 10, 19)
NOW = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
WING = Money.from_rupees(Decimal("200"))
THRESHOLDS = load_setup_thresholds()
INDEX_SYMBOLS = frozenset({"NIFTY", "BANKNIFTY"})

# Rule 1, as a list of words that may not appear as a field name anywhere a
# specialist's output can reach.
BANNED_FIELDS = {"quantity", "lots", "lot_count", "size", "order_price", "order_type"}

# IV ranks swept across the whole range, one per point.
IV_SWEEP = [float(value) for value in range(0, 101)]


def _fundamental() -> tuple[FundamentalAgent, FundamentalView]:
    store = FakeNewsStore()
    store.save_filings(
        [
            Filing(
                filing_id="f1",
                symbol="RELIANCE",
                subject="Action(s) taken or orders passed",
                body="RELIANCE has informed the Exchange.",
                url="https://nsearchives.nseindia.com/f1.pdf",
                filed_at=NOW,
                ingested_at=NOW,
            )
        ]
    )
    agent = FundamentalAgent(store)
    return agent, agent.assess("RELIANCE", AS_OF)


def _technical() -> tuple[TechnicalAgent, TechnicalView]:
    store = FakeBarStore()
    store.write_daily_bars(flat_bars(80), "verify")
    agent = TechnicalAgent(store)
    return agent, agent.assess("RELIANCE")


def _options() -> tuple[OptionsAgent, OptionsView]:
    port = FakeOptionChain()
    port.add_chain(chain(RELIANCE, STOCK_EXPIRY), STOCK_EXPIRY)
    agent = OptionsAgent(port, THRESHOLDS, WING)
    view = agent.propose(
        instrument=RELIANCE,
        setup=SetupId.CATALYST_CONTINUATION,
        direction=Direction.BULLISH,
        iv_rank=IvRank(value=40.0, source=IvSource.PROXY, sample_count=130, window=252),
        as_of=AS_OF,
    )
    return agent, view


def _portfolio() -> tuple[PortfolioAgent, PortfolioView]:
    broker = FakeBrokerRead()
    broker.funds = Money.from_rupees(Decimal("500000"))
    broker.positions = [_nifty_call("24000", 75)]
    chains = FakeOptionChain()
    chains.add_chain(nifty_chain(INDEX_EXPIRY), INDEX_EXPIRY)
    agent = PortfolioAgent(broker, FakeJournal(), chains, INDEX_SYMBOLS)
    return agent, agent.assess("NIFTY", NOW)


def _nifty_call(strike: str, units: int) -> BrokerPosition:
    instrument = InstrumentRef(
        symbol="NIFTY",
        exchange=NIFTY.exchange,
        segment=NIFTY.segment,
        security_id="26000",
        lot_size=75,
        expiry=INDEX_EXPIRY,
        strike=Price.from_rupees(Decimal(strike)),
        right=OptionRight.CALL,
    )
    return BrokerPosition(
        instrument=instrument,
        quantity=Quantity(units=units),
        average_price=Price.from_rupees(Decimal("265")),
        unrealised_pnl=Money.zero(),
        fetched_at=NOW,
    )


class _HasFinding(Protocol):
    """The one thing all four views have in common, statically."""

    @property
    def finding(self) -> SpecialistFinding: ...


def _all_views() -> dict[str, _HasFinding]:
    return {
        "fundamental": _fundamental()[1],
        "technical": _technical()[1],
        "options": _options()[1],
        "portfolio": _portfolio()[1],
    }


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------


def _four_specialists_return_typed_output() -> tuple[bool, str]:
    views = _all_views()
    findings: list[object] = [view.finding for view in views.values()]
    wrong = [type(item).__name__ for item in findings if not isinstance(item, SpecialistFinding)]
    if wrong:
        return False, f"not a SpecialistFinding: {wrong}"

    names = {view.finding.specialist for view in views.values()}
    complete = names == set(SpecialistName)
    return complete and len(views) == EXPECTED_SPECIALISTS, (
        f"{len(views)} specialists, all typed: {sorted(name.value for name in names)}"
    )


def _every_finding_reports_values_not_verdicts() -> tuple[bool, str]:
    """Rule 9. A measurement reading PASS has thrown the number away."""
    offenders = []
    total = 0
    for name, view in _all_views().items():
        finding = view.finding
        total += len(finding.measurements)
        for key, value in finding.measurements.items():
            if value in {"PASS", "FAIL", "True", "False", "OK"} and key != (
                "closed_beyond_range_edge"
            ):
                offenders.append(f"{name}.{key}={value}")
    return not offenders, (
        f"{total} measurements across 4 specialists, none a bare verdict"
        if not offenders
        else f"verdicts found: {offenders}"
    )


def _dataclasses_reachable_from(cls: type) -> set[type]:
    """Every dataclass reachable from `cls`, through its fields and generics.

    `field.type` is a **string** in every module using `from __future__ import
    annotations`, which is all of them - so a walker reading `field.type`
    directly sees nothing nested and reports a clean sweep of one level. That
    is the version this check started as, and it is why the planted-field
    self-test below exists.
    """
    found: set[type] = set()
    queue: list[type] = [cls]
    while queue:
        current = queue.pop()
        if current in found or not is_dataclass(current):
            continue
        found.add(current)
        try:
            hints = get_type_hints(current)
        except Exception:
            continue
        for hint in hints.values():
            for candidate in _types_inside(hint):
                if is_dataclass(candidate) and candidate not in found:
                    queue.append(candidate)
    return found


def _types_inside(hint: object) -> list[type]:
    """Flatten `tuple[Leg, ...]`, `Money | None`, `dict[str, Thing]` to types."""
    if isinstance(hint, type):
        return [hint]
    inside: list[type] = []
    for argument in get_args(hint):
        inside.extend(_types_inside(argument))
    return inside


@dataclass(frozen=True)
class _PlantedLeg:
    """Nested one level down, to prove the walker actually descends."""

    quantity: int


@dataclass(frozen=True)
class _PlantedView:
    finding: SpecialistFinding
    legs: tuple[_PlantedLeg, ...]


def _no_specialist_can_express_a_quantity() -> tuple[bool, str]:
    """Rule 1, by introspection, with the detector itself induced first.

    A clean result only means something if the same walk finds a planted size
    field, so the planted case runs in the same breath as the real one.
    """
    planted = [
        name for cls in _dataclasses_reachable_from(_PlantedView) for name in _size_fields_in(cls)
    ]
    if not planted:
        return False, "the detector missed a planted nested quantity field"

    reachable: set[type] = set()
    found: list[str] = []
    for view in _all_views().values():
        reachable |= _dataclasses_reachable_from(type(view))
    for cls in sorted(reachable, key=lambda item: item.__name__):
        found.extend(_size_fields_in(cls))

    return not found, (
        f"{len(reachable)} dataclasses reachable from the four views, none with a "
        f"size field (detector proved on a planted {planted[0]})"
        if not found
        else f"found {found}"
    )


def _size_fields_in(cls: type) -> list[str]:
    return [
        f"{cls.__name__}.{field.name}"
        for field in fields(cls)
        if field.name.lower() in BANNED_FIELDS
    ]


def _no_specialist_makes_a_model_call() -> tuple[bool, str]:
    """These four are arithmetic. A model in any of them would be a cost with
    no answer attached - see the DEVLOG entry for 3.3."""
    packages = ("fundamental", "technical", "options", "portfolio")
    with_llm = []
    for package in packages:
        source = (ROOT / "src" / "trading_desk" / "agents" / package / "agent.py").read_text()
        if "GovernedLlmPort" in source or "LlmPort" in source:
            with_llm.append(package)
    return not with_llm, (
        f"0 of {len(packages)} specialists take a model port"
        if not with_llm
        else f"{with_llm} import an LLM port"
    )


def _measurements_are_identical_across_three_runs() -> tuple[bool, str]:
    """docs/12's determinism boundary, at the specialist level.

    Same inputs, three runs, identical measured values. A difference here
    means something non-deterministic has reached the decision path, and this
    is the cheapest place in the system to notice it.
    """
    drifted = []
    for name in ("fundamental", "technical", "options", "portfolio"):
        runs = [_all_views()[name].finding.measurements for _ in range(DETERMINISM_RUNS)]
        if any(run != runs[0] for run in runs[1:]):
            drifted.append(name)
    return not drifted, (
        f"4 specialists x {DETERMINISM_RUNS} runs: every measured value identical"
        if not drifted
        else f"drifted: {drifted}"
    )


def _malformed_output_fails_rather_than_degrades() -> tuple[bool, str]:
    """Induced, two ways, because they fail in different places.

    1. A port that raises must propagate - no half-built view.
    2. A finding that cannot describe its own veto must raise at construction.
    """
    failures = []

    port = FakeOptionChain()
    port.add_chain(chain(RELIANCE, STOCK_EXPIRY), STOCK_EXPIRY)
    port.fail_next_fetch = True
    agent = OptionsAgent(port, THRESHOLDS, WING)
    try:
        agent.propose(
            instrument=RELIANCE,
            setup=SetupId.CATALYST_CONTINUATION,
            direction=Direction.BULLISH,
            iv_rank=IvRank(value=40.0, source=IvSource.PROXY, sample_count=130, window=252),
            as_of=AS_OF,
        )
    except ConnectionError:
        pass
    else:
        failures.append("a failing chain fetch produced a view instead of raising")

    try:
        SpecialistFinding(specialist=SpecialistName.OPTIONS, symbol="RELIANCE", vetoes=("",))
    except ValueError:
        pass
    else:
        failures.append("a veto with no reason was accepted")

    return not failures, (
        "2 of 2 malformed cases failed the cycle" if not failures else "; ".join(failures)
    )


def _no_naked_short_is_reachable() -> tuple[bool, str]:
    """FR-5.9, induced: a chain whose short leg is priced and whose wing is not
    there at all. Must veto, not return a single leg."""
    from dataclasses import replace  # noqa: PLC0415

    full = nifty_chain(INDEX_EXPIRY)
    trimmed = replace(
        full,
        strikes=tuple(
            row for row in full.strikes if row.strike >= Price.from_rupees(Decimal("23600"))
        ),
    )
    port = FakeOptionChain()
    port.add_chain(trimmed, INDEX_EXPIRY)
    view = OptionsAgent(port, THRESHOLDS, WING).propose(
        instrument=NIFTY,
        setup=SetupId.INDEX_VOL_REGIME,
        direction=Direction.BULLISH,
        iv_rank=IvRank(value=85.0, source=IvSource.PROXY, sample_count=130, window=252),
        as_of=AS_OF,
        time_of_day=time(10, 0),
    )
    refused = view.spread is None and any("naked short" in veto for veto in view.finding.vetoes)
    return refused, (
        "a wingless credit spread is refused, not returned as one leg"
        if refused
        else f"built {view.spread}"
    )


def _setup_a_never_sells_premium() -> tuple[bool, str]:
    """Swept rather than spot-checked: Setup A has a ceiling, not a regime, so
    no IV rank anywhere in 0-100 may produce a credit structure."""
    ceiling = THRESHOLDS.setup_a.max_iv_rank
    kinds = [
        choose_structure(
            iv_rank=value,
            direction=Direction.BULLISH,
            credit_above=None,
            debit_below=ceiling,
        ).kind
        for value in IV_SWEEP
    ]
    sells = [
        value
        for value, kind in zip(IV_SWEEP, kinds, strict=True)
        if kind is StructureKind.CREDIT_VERTICAL
    ]
    buys = sum(1 for kind in kinds if kind is StructureKind.DEBIT_VERTICAL)
    return not sells, (
        f"{len(IV_SWEEP)} IV ranks: {buys} buy, 0 sell, {len(IV_SWEEP) - buys} refuse "
        f"(ceiling {ceiling:.0f})"
        if not sells
        else f"sold premium at IV rank {sells[:3]}"
    )


def _the_middle_band_refuses() -> tuple[bool, str]:
    """Setup B's no-trade band, measured. If this ever reads 0, the system has
    an answer for every input, which is the failure mode docs/02 warns about."""
    limits = THRESHOLDS.setup_b
    kinds = [
        choose_structure(
            iv_rank=value,
            direction=Direction.BULLISH,
            credit_above=limits.credit_regime_min_iv_rank,
            debit_below=limits.debit_regime_max_iv_rank,
        ).kind
        for value in IV_SWEEP
    ]
    refused = sum(1 for kind in kinds if kind is StructureKind.NONE)
    return refused > 0, (
        f"{refused} of {len(IV_SWEEP)} IV ranks trade nothing "
        f"(between {limits.debit_regime_max_iv_rank:.0f} and "
        f"{limits.credit_regime_min_iv_rank:.0f})"
    )


def _signals_are_registered_and_their_status_is_reported() -> tuple[bool, str]:
    """FR-5.10. The closed list is the control; the disclosure is the honesty."""
    statuses = [require_registered(name).status for name in SIGNALS_USED]
    backtested = sum(1 for status in statuses if status is SignalStatus.BACKTESTED)
    _, view = _technical()
    reported = view.finding.measurements["signals_backtested"]
    consistent = reported == f"{backtested} of {len(SIGNALS_USED)}"
    return consistent, (
        f"{len(SIGNALS_USED)} signals, all registered, {backtested} backtested; "
        f"the view reports '{reported}'"
    )


def _net_delta_refuses_on_a_partial_portfolio() -> tuple[bool, str]:
    """Induced. One unlisted strike must make the whole figure unavailable."""
    broker = FakeBrokerRead()
    broker.funds = Money.from_rupees(Decimal("500000"))
    broker.positions = [_nifty_call("24000", 75), _nifty_call("24050", 75)]
    chains = FakeOptionChain()
    chains.add_chain(nifty_chain(INDEX_EXPIRY), INDEX_EXPIRY)
    view = PortfolioAgent(broker, FakeJournal(), chains, INDEX_SYMBOLS).assess("NIFTY", NOW)

    refused = view.net_delta is None and any(
        "no chain quote" in item for item in view.finding.unavailable
    )
    return refused, (
        "1 unpriceable position of 2 -> net delta unavailable, not a partial sum"
        if refused
        else f"reported {view.net_delta}"
    )


def _the_veto_table_covers_the_archive() -> tuple[bool, str]:
    """Measured against the real filings, not against the fixtures."""
    counts = _archive_subject_counts()
    if not counts:
        return False, "database unreachable - cannot measure"

    total = sum(counts.values())
    flagged = 0
    vetoing = 0
    for subject, count in counts.items():
        flag = flag_for(subject)
        if flag is None:
            continue
        flagged += count
        if flag in VETOING_FLAGS:
            vetoing += count

    return flagged > 0, (
        f"{total} filings: {flagged} raise a flag ({flagged / total:.0%}), {vetoing} of those veto"
    )


def _pledge_disclosures_are_absent() -> tuple[bool, str]:
    """The evidence behind ISS-026, measured rather than asserted.

    This check **passes when the count is zero**, which reads backwards until
    you see what it is for: it records that the headline disqualifier in
    docs/02 is not in our data. If it ever finds some, the blind spot has
    closed and the agent should start reading them.
    """
    counts = _archive_subject_counts()
    if not counts:
        return False, "database unreachable - cannot measure"

    terms = ("pledge", "encumbr", "promoter")
    matching = {
        subject: count
        for subject, count in counts.items()
        if any(term in subject.lower() for term in terms)
    }
    found = sum(matching.values())
    return found == 0, (
        f"0 of {sum(counts.values())} filings mention pledge/encumbrance/promoter "
        "- the ISS-026 blind spot is real"
        if found == 0
        else f"found {found}: {sorted(matching)}"
    )


def _no_sector_map_exists() -> tuple[bool, str]:
    """The evidence behind ISS-027. Same shape as the check above."""
    config_dir = ROOT / "config"
    with_sectors = [
        path.name
        for path in sorted(config_dir.glob("*.yaml"))
        if "sector" in path.read_text().lower()
    ]
    return not with_sectors, (
        f"{len(list(config_dir.glob('*.yaml')))} config files, none naming a sector "
        "- concentration is reported as unavailable (ISS-027)"
        if not with_sectors
        else f"sector data in {with_sectors}"
    )


def _agents_take_ports_not_adapters() -> tuple[bool, str]:
    """The import-linter contract says agents may not import adapters. This
    checks the weaker thing it cannot: that each one is *constructed* from
    ports, so a test can run it with no network at all."""
    constructors = {
        "fundamental": FundamentalAgent,
        "technical": TechnicalAgent,
        "options": OptionsAgent,
        "portfolio": PortfolioAgent,
    }
    counts = {name: len(inspect.signature(cls).parameters) for name, cls in constructors.items()}
    built = _all_views()
    return len(built) == EXPECTED_SPECIALISTS, (
        "all 4 built from fakes alone: " + ", ".join(f"{k} takes {v}" for k, v in counts.items())
    )


def _archive_subject_counts() -> dict[str, int]:
    """Subject counts from the database, or {} when it is not reachable."""
    try:
        engine = create_db_engine(load_infra_settings())
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT subject, count(*) FROM filings GROUP BY subject")
            ).all()
        return {str(subject): int(count) for subject, count in rows}
    except Exception:
        return {}


def run() -> list[CheckResult]:
    typed_ok, typed_detail = _four_specialists_return_typed_output()
    values_ok, values_detail = _every_finding_reports_values_not_verdicts()
    size_ok, size_detail = _no_specialist_can_express_a_quantity()
    llm_ok, llm_detail = _no_specialist_makes_a_model_call()
    determinism_ok, determinism_detail = _measurements_are_identical_across_three_runs()
    malformed_ok, malformed_detail = _malformed_output_fails_rather_than_degrades()
    naked_ok, naked_detail = _no_naked_short_is_reachable()
    sell_ok, sell_detail = _setup_a_never_sells_premium()
    middle_ok, middle_detail = _the_middle_band_refuses()
    signals_ok, signals_detail = _signals_are_registered_and_their_status_is_reported()
    delta_ok, delta_detail = _net_delta_refuses_on_a_partial_portfolio()
    veto_ok, veto_detail = _the_veto_table_covers_the_archive()
    pledge_ok, pledge_detail = _pledge_disclosures_are_absent()
    sector_ok, sector_detail = _no_sector_map_exists()
    ports_ok, ports_detail = _agents_take_ports_not_adapters()

    return [
        CheckResult(
            name="four_specialists_return_typed_output",
            passed=typed_ok,
            measured=typed_detail,
            requirement=f"need {EXPECTED_SPECIALISTS}, each with a SpecialistFinding",
        ),
        CheckResult(
            name="each_runs_standalone_against_fakes",
            passed=ports_ok,
            measured=ports_detail,
            requirement="need all 4 constructible from ports alone, no network",
        ),
        CheckResult(
            name="findings_report_values_not_verdicts",
            passed=values_ok,
            measured=values_detail,
            requirement="need no bare PASS/FAIL in a measurement (rule 9)",
        ),
        CheckResult(
            name="no_specialist_can_express_a_quantity",
            passed=size_ok,
            measured=size_detail,
            requirement="need 0 size fields anywhere reachable (rule 1, FR-4.7)",
        ),
        CheckResult(
            name="no_specialist_makes_a_model_call",
            passed=llm_ok,
            measured=llm_detail,
            requirement="need 0 - these four are arithmetic (DEVLOG 2026-10-19)",
        ),
        CheckResult(
            name="measurements_identical_across_three_runs",
            passed=determinism_ok,
            measured=determinism_detail,
            requirement="need byte-identical measured values (docs/12 determinism)",
        ),
        CheckResult(
            name="malformed_output_fails_the_cycle",
            passed=malformed_ok,
            measured=malformed_detail,
            requirement="need a raise, never a partial view (rule 6)",
        ),
        CheckResult(
            name="no_naked_short_is_reachable",
            passed=naked_ok,
            measured=naked_detail,
            requirement="need a veto when no wing exists (FR-5.9)",
        ),
        CheckResult(
            name="setup_a_never_sells_premium",
            passed=sell_ok,
            measured=sell_detail,
            requirement="need 0 credit structures across the whole IV range",
        ),
        CheckResult(
            name="the_middle_band_trades_nothing",
            passed=middle_ok,
            measured=middle_detail,
            requirement="need > 0 refusals - an answer for every input is the bug",
        ),
        CheckResult(
            name="signals_registered_and_status_disclosed",
            passed=signals_ok,
            measured=signals_detail,
            requirement="need every signal registered and the count reported (FR-5.10)",
        ),
        CheckResult(
            name="net_delta_refuses_on_a_partial_portfolio",
            passed=delta_ok,
            measured=delta_detail,
            requirement="need None, never a partial sum (rule 6)",
        ),
        CheckResult(
            name="veto_table_covers_the_real_archive",
            passed=veto_ok,
            measured=veto_detail,
            requirement="need the table to fire on real filings, not only fixtures",
        ),
        CheckResult(
            name="pledge_blind_spot_is_measured",
            passed=pledge_ok,
            measured=pledge_detail,
            requirement="need 0 found - records ISS-026 rather than assuming it",
        ),
        CheckResult(
            name="sector_map_absence_is_measured",
            passed=sector_ok,
            measured=sector_detail,
            requirement="need 0 found - records ISS-027 rather than inventing one",
        ),
    ]
