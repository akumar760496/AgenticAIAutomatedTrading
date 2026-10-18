"""Verify step 1.8 - IV rank.

**Every value this step produces today is a proxy, and that is the finding.**
The chain archive began on 2026-10-16, `MIN_SAMPLES` is 30 trading days, so no
instrument has enough real IV history and will not for roughly six months
(FR-5.6). The step is correct when that fact is visible in every value rather
than hidden behind a plausible-looking number.

docs/12 asks for four things. Three are automatable here:

- **R** ranks for the universe, each carrying `source` and a sample count
- **A** the boundary: below the minimum, "insufficient history" and no number.
  docs/12 says to verify this explicitly - "a rank computed from 11 days is a
  lie with a decimal point"
- **X** a coarse cross-check: ATM IV read from our own archive must be in a
  believable band, and the index must sit below single stocks

The fourth (**H**, plotting the ATM IV series against remembered events) needs
months of data and a human, and is listed in STATE.md rather than faked here.
"""

from __future__ import annotations

from scripts.verify._runner import CheckResult
from trading_desk.adapters.clock import SystemClock
from trading_desk.adapters.notify.logging_notifier import LoggingNotifier
from trading_desk.adapters.store.bar_repository import PostgresBarStore
from trading_desk.adapters.store.parquet_chain import ParquetChainArchive
from trading_desk.application.iv_rank import IvRankService
from trading_desk.application.universe import load_universe_request
from trading_desk.domain.vol import (
    MAX_PLAUSIBLE_IV,
    MIN_SAMPLES,
    IvSource,
    percentile_of,
    rank_within,
    realised_volatility,
)
from trading_desk.infra.config import load_infra_settings
from trading_desk.infra.db import create_db_engine, create_session_factory

TITLE = "IV rank"

# Names with both a chain capture and a long bar history.
SAMPLE = ["RELIANCE", "TITAN", "HINDALCO", "LT", "COALINDIA"]
INDEX = "NIFTY"

# A believable ATM band for Indian names. Wider than the realistic 10-60% so
# the check fails on a broken reading, not on an unusual but real market.
MIN_SANE_IV = 5.0
MAX_SANE_IV = 80.0

BARELY_TOO_FEW = MIN_SAMPLES - 1


def _service() -> IvRankService:
    engine = create_db_engine(load_infra_settings())
    return IvRankService(
        archive=ParquetChainArchive(),
        bars=PostgresBarStore(create_session_factory(engine)),
        notifier=LoggingNotifier(SystemClock()),
    )


def _every_value_carries_its_provenance() -> tuple[bool, str]:
    report = _service().run(SAMPLE)

    available = [rank for rank in report.ranks.values() if rank.is_available]
    tagged = [
        rank
        for rank in available
        if rank.source in (IvSource.ACTUAL, IvSource.PROXY) and rank.sample_count > 0
    ]
    missing_reason = [
        symbol for symbol, rank in report.ranks.items() if not rank.is_available and not rank.reason
    ]

    ok = len(tagged) == len(available) and not missing_reason
    return ok, report.summary()


def _no_value_claims_to_be_real_iv_yet() -> tuple[bool, str]:
    """Today's honest answer. If this ever fails, real IV history has accrued -
    which is good news, and means this check should be retired, not loosened."""
    report = _service().run(SAMPLE)
    actual = [
        symbol
        for symbol, rank in report.ranks.items()
        if rank.is_available and rank.source is IvSource.ACTUAL
    ]
    days = len(ParquetChainArchive().atm_iv_series("RELIANCE"))
    return not actual, (
        f"{days} day(s) of chain history, need {MIN_SAMPLES}; "
        f"{report.proxy_count} proxy, {report.actual_count} actual"
    )


def _below_the_minimum_returns_no_number() -> tuple[bool, str]:
    """docs/12 asks for this boundary explicitly, from both sides."""
    too_few = rank_within(20.0, [1.0] * BARELY_TOO_FEW, IvSource.ACTUAL)
    just_enough = rank_within(20.0, [1.0] * MIN_SAMPLES, IvSource.ACTUAL)

    correct = (
        too_few.value is None
        and "insufficient history" in too_few.reason
        and just_enough.value is not None
    )
    return correct, (
        f"{BARELY_TOO_FEW} samples -> no number ({too_few.reason}); "
        f"{MIN_SAMPLES} samples -> {just_enough.value:.1f}"
    )


def _atm_iv_read_from_the_archive_is_believable() -> tuple[bool, str]:
    """Cross-check against reality: these are our own captured numbers."""
    archive = ParquetChainArchive()
    readings = {}
    for symbol in [*SAMPLE, INDEX]:
        series = archive.atm_iv_series(symbol)
        if series:
            readings[symbol] = series[-1][1]

    if not readings:
        return False, "no ATM IV could be read from the archive at all"

    outside = {
        symbol: round(value, 1)
        for symbol, value in readings.items()
        if not MIN_SANE_IV <= value <= MAX_SANE_IV
    }
    detail = ", ".join(f"{symbol} {value:.1f}%" for symbol, value in sorted(readings.items()))
    if outside:
        return False, f"outside {MIN_SANE_IV}-{MAX_SANE_IV}%: {outside} (all: {detail})"
    return True, detail


def _the_index_is_calmer_than_the_single_stocks() -> tuple[bool, str]:
    """A coarse directional check that catches a sign or column error.

    An index is a basket, so its implied volatility is structurally below the
    average of its constituents - correlation is never 1. If NIFTY ever reads
    above the stock average, something is wired wrong.
    """
    archive = ParquetChainArchive()
    index_series = archive.atm_iv_series(INDEX)
    stocks = [series[-1][1] for symbol in SAMPLE if (series := archive.atm_iv_series(symbol))]

    if not index_series or not stocks:
        return False, "need both the index and at least one stock captured"

    index_iv = index_series[-1][1]
    stock_mean = sum(stocks) / len(stocks)
    return index_iv < stock_mean, (
        f"{INDEX} {index_iv:.1f}% vs {len(stocks)}-stock mean {stock_mean:.1f}%"
    )


def _the_proxy_is_computable_from_real_bars() -> tuple[bool, str]:
    """The path that actually runs today, against the real backfill."""
    engine = create_db_engine(load_infra_settings())
    store = PostgresBarStore(create_session_factory(engine))
    closes = [close for _, close in store.daily_closes("RELIANCE")]

    if len(closes) < MIN_SAMPLES:
        return False, f"only {len(closes)} daily bars for RELIANCE"

    annualised = realised_volatility(closes[-21:])
    if annualised is None:
        return False, "realised volatility could not be computed"

    plausible = MIN_SANE_IV <= annualised <= MAX_PLAUSIBLE_IV
    return plausible, f"{len(closes)} bars, 20-day realised vol {annualised:.1f}% annualised"


def _the_universe_has_the_history_the_proxy_needs() -> tuple[bool, str]:
    """`docs/10` says "ranks computed for the full universe". Today it cannot be.

    The 1.4 backfill was run for NIFTY and RELIANCE only, so every other name
    has no bars and therefore no proxy either - the service correctly refuses
    rather than inventing a number, which is why the other checks pass while
    the step is not finished. This fails by design until the backfill is run
    across the universe, in the same way `verify 1_2` fails on the unsigned
    thresholds. It is a job to run, not a defect to fix.
    """
    engine = create_db_engine(load_infra_settings())
    store = PostgresBarStore(create_session_factory(engine))

    request = load_universe_request()
    universe = [*request.index_symbols, *request.stock_symbols]
    with_history = [symbol for symbol in universe if store.count_daily_bars(symbol) > MIN_SAMPLES]
    missing = len(universe) - len(with_history)
    return missing == 0, (
        f"{len(with_history)} of {len(universe)} universe names have > {MIN_SAMPLES} "
        f"daily bars; {missing} cannot produce even a proxy"
    )


def _a_flat_series_ranks_at_the_middle() -> tuple[bool, str]:
    """Guards the tie rule. With a strict comparison this reads 0 or 100, and
    both misdescribe a day that is exactly typical."""
    value = percentile_of(20.0, [20.0] * 50)
    return value == 50.0, f"flat series ranks at {value:.1f}"  # noqa: PLR2004


def run() -> list[CheckResult]:
    provenance_ok, provenance_detail = _every_value_carries_its_provenance()
    honest_ok, honest_detail = _no_value_claims_to_be_real_iv_yet()
    boundary_ok, boundary_detail = _below_the_minimum_returns_no_number()
    sane_ok, sane_detail = _atm_iv_read_from_the_archive_is_believable()
    index_ok, index_detail = _the_index_is_calmer_than_the_single_stocks()
    proxy_ok, proxy_detail = _the_proxy_is_computable_from_real_bars()
    flat_ok, flat_detail = _a_flat_series_ranks_at_the_middle()
    universe_ok, universe_detail = _the_universe_has_the_history_the_proxy_needs()

    return [
        CheckResult(
            name="every_value_carries_source_and_sample_count",
            passed=provenance_ok,
            measured=provenance_detail,
            requirement="need source + sample count on every value, a reason on every refusal",
        ),
        CheckResult(
            name="nothing_claims_real_iv_before_the_history_exists",
            passed=honest_ok,
            measured=honest_detail,
            requirement=f"need 0 ACTUAL ranks until {MIN_SAMPLES} days of chain history accrue",
        ),
        CheckResult(
            name="below_the_minimum_sample_count_returns_no_number",
            passed=boundary_ok,
            measured=boundary_detail,
            requirement="need a refusal at n-1 and a value at n - a rank from 11 days is a lie",
        ),
        CheckResult(
            name="atm_iv_from_our_archive_is_believable",
            passed=sane_ok,
            measured=sane_detail,
            requirement=f"need every captured ATM IV within {MIN_SANE_IV}-{MAX_SANE_IV}%",
        ),
        CheckResult(
            name="the_index_reads_calmer_than_the_single_stocks",
            passed=index_ok,
            measured=index_detail,
            requirement="need index IV < stock mean - a basket cannot be the most volatile",
        ),
        CheckResult(
            name="the_realised_vol_proxy_computes_from_real_bars",
            passed=proxy_ok,
            measured=proxy_detail,
            requirement="need a plausible annualised number from the 1.4 backfill",
        ),
        CheckResult(
            name="the_universe_has_the_bar_history_the_proxy_needs",
            passed=universe_ok,
            measured=universe_detail,
            requirement="need the 1.4 backfill run across the universe, not 2 names",
        ),
        CheckResult(
            name="a_typical_day_ranks_at_the_middle_not_an_extreme",
            passed=flat_ok,
            measured=flat_detail,
            requirement="need 50.0 - ties count half, or a flat series reads 0 or 100",
        ),
    ]
