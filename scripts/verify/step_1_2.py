"""Verify step 1.2 - universe service.

Checks what can be checked without a half-hour live survey: the candidate list is
real and comes from the exchange, the thresholds load, the ranking separates the
traps from the liquid names, and a generated universe is readable by the
snapshotter.

The live survey itself is verified by running it (`trading-desk survey`) during a
session and reading the diff by hand - evidence kind H in docs/12, because the
question "are these the right 25 names?" has no automated answer.
"""

from __future__ import annotations

from scripts.verify._runner import CheckResult
from trading_desk.adapters.clock import SystemClock
from trading_desk.adapters.dhan.instruments import DhanInstrumentMaster
from trading_desk.application.exchange_rules import load_exchange_rules
from trading_desk.application.universe import (
    GENERATED_UNIVERSE_PATH,
    STARTER_UNIVERSE_PATH,
    default_universe_path,
    load_universe_request,
)
from trading_desk.application.universe_builder import diff_universe, previous_symbols
from trading_desk.domain.liquidity import LiquidityMeasure, rank_by_liquidity
from trading_desk.domain.values import Money

TITLE = "Universe service: candidates, thresholds, ranking"

# The F&O list runs to roughly 180-230 names. Far outside that and the instrument
# master was parsed wrongly, which is the failure this bound exists to catch.
MIN_CANDIDATES = 150
MAX_CANDIDATES = 300


def _candidate_count() -> tuple[bool, str]:
    """The candidate set must come from the exchange, not from the starter list."""
    try:
        catalogue = DhanInstrumentMaster().option_underlyings(SystemClock().utc_now().date())
    except Exception as error:  # report it, do not crash the harness
        return False, f"{type(error).__name__}: {error}"

    count = len(catalogue)
    return MIN_CANDIDATES <= count <= MAX_CANDIDATES, f"{count} underlyings with listed options"


def _candidates_exceed_the_starter_list() -> tuple[bool, str]:
    """If the candidate set is the starter list, the ranking has nothing to discover."""
    try:
        catalogue = DhanInstrumentMaster().option_underlyings(SystemClock().utc_now().date())
    except Exception as error:  # report it, do not crash the harness
        return False, f"{type(error).__name__}: {error}"

    starter = len(load_universe_request(STARTER_UNIVERSE_PATH).all_symbols)
    return len(catalogue) > starter * 3, f"{len(catalogue)} candidates vs {starter} in the starter"


def _ranking_catches_the_concentrated_chain() -> tuple[bool, str]:
    """A chain with good turnover concentrated in two legs must be excluded."""
    rules = load_exchange_rules().liquidity
    above_floor = Money(rules.min_premium_turnover.paise * 10)

    liquid = LiquidityMeasure(
        symbol="LIQUID",
        is_index=False,
        premium_turnover=above_floor,
        open_interest=rules.min_open_interest * 10,
        legs_priced=40,
        legs_total=40,
    )
    concentrated = LiquidityMeasure(
        symbol="CONCENTRATED",
        is_index=False,
        premium_turnover=above_floor,
        open_interest=rules.min_open_interest * 10,
        legs_priced=2,
        legs_total=40,
    )

    ranking = rank_by_liquidity((liquid, concentrated), rules)
    correct = ranking.symbols == ("LIQUID",)
    reasons = {item.symbol: item.reason for item in ranking.excluded}
    return correct, f"included {ranking.symbols}, excluded {reasons}"


def _every_name_is_accounted_for() -> tuple[bool, str]:
    """Nothing may disappear between survey and universe."""
    rules = load_exchange_rules().liquidity
    measures = tuple(
        LiquidityMeasure(
            symbol=f"S{index:02d}",
            is_index=False,
            premium_turnover=Money(rules.min_premium_turnover.paise * (index + 1)),
            open_interest=rules.min_open_interest * 2,
            legs_priced=40,
            legs_total=40,
        )
        for index in range(rules.max_stocks + 5)
    )

    ranking = rank_by_liquidity(measures, rules)
    accounted = set(ranking.symbols) | {item.symbol for item in ranking.excluded}
    expected = {measure.symbol for measure in measures}
    return accounted == expected, f"{len(accounted)} of {len(expected)} names accounted for"


def _generated_file_state() -> tuple[bool, str]:
    """Either no survey has run yet, or its output loads and differs from the guess."""
    if not GENERATED_UNIVERSE_PATH.is_file():
        return True, "no survey has run yet - snapshotter is using the starter list"

    request = load_universe_request(GENERATED_UNIVERSE_PATH)
    starter = load_universe_request(STARTER_UNIVERSE_PATH)
    diff = diff_universe(starter.all_symbols, previous_symbols(GENERATED_UNIVERSE_PATH))

    if not diff.changed:
        return False, "generated universe is identical to the hand-written guess"
    return True, f"{len(request.all_symbols)} symbols, {len(diff.added)} not in the starter list"


def run() -> list[CheckResult]:
    rules = load_exchange_rules()
    candidates_ok, candidates_detail = _candidate_count()
    discovery_ok, discovery_detail = _candidates_exceed_the_starter_list()
    trap_ok, trap_detail = _ranking_catches_the_concentrated_chain()
    accounted_ok, accounted_detail = _every_name_is_accounted_for()
    generated_ok, generated_detail = _generated_file_state()

    return [
        CheckResult(
            name="candidates_come_from_the_exchange",
            passed=candidates_ok,
            measured=candidates_detail,
            requirement=f"need {MIN_CANDIDATES}-{MAX_CANDIDATES} from the instrument master",
        ),
        CheckResult(
            name="candidate_set_is_wider_than_the_guess",
            passed=discovery_ok,
            measured=discovery_detail,
            requirement="need many more candidates than the starter list",
        ),
        CheckResult(
            name="thresholds_load_from_config",
            passed=rules.liquidity.max_stocks > 0,
            measured=(
                f"turnover floor {rules.liquidity.min_premium_turnover}, "
                f"OI {rules.liquidity.min_open_interest:,}, "
                f"priced {rules.liquidity.min_priced_fraction:.0%}, "
                f"cap {rules.liquidity.max_stocks}"
            ),
            requirement="need config/exchange_rules.yaml to parse",
        ),
        CheckResult(
            name="thresholds_reviewed",
            passed=rules.verified,
            measured=f"verified={rules.verified} ({rules.source})",
            requirement="need sign-off before the universe is trusted",
        ),
        CheckResult(
            name="illiquid_chain_with_good_turnover_excluded",
            passed=trap_ok,
            measured=trap_detail,
            requirement="need the concentrated chain dropped",
        ),
        CheckResult(
            name="no_symbol_disappears_silently",
            passed=accounted_ok,
            measured=accounted_detail,
            requirement="need every surveyed name included or excluded with a reason",
        ),
        CheckResult(
            name="snapshotter_reads_the_right_file",
            passed=generated_ok,
            measured=f"{default_universe_path()} - {generated_detail}",
            requirement="need the generated file once a survey has run",
        ),
    ]
