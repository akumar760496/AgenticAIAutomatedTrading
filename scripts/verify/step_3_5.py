"""Verify step 3.5 - the briefing and its delivery.

`docs/10`: done when "you receive a real briefing from real data and can act
on it without opening a laptop."

That last part is a **human** check and it cannot be automated - `docs/12`
calls it the 60-second test: receive it on your phone, standing up, and act on
it. What *can* be checked here is everything that would make the human check
fail for a mechanical reason: a missing section, a gate value that did not
survive the trip, a message too long to send, a size that quietly disappeared
because nobody set a capital figure.

The last two checks fail today and name what is missing. They are the honest
half of the step: the briefing is built and tested, and it has not yet reached
a phone.
"""

from __future__ import annotations

from decimal import Decimal

from tests.agents.strategist.harness import Harness
from tests.fakes import FakeNotifier

from scripts.verify._runner import CheckResult
from trading_desk.adapters.telegram.client import TelegramClient, TelegramError
from trading_desk.adapters.telegram.notifier import (
    MAX_MESSAGE_CHARS,
    split_message,
)
from trading_desk.application.briefing import BriefingBuilder, deliver
from trading_desk.application.research_cycle import CycleOutput
from trading_desk.domain.briefing import Briefing
from trading_desk.domain.values import Money
from trading_desk.infra.config import MissingConfigError, load_telegram_settings
from trading_desk.infra.risk_config import RiskConfig, load_risk_config

TITLE = "The briefing and Telegram delivery"

# One phone screen, roughly, at a readable size. Not a hard rule anywhere -
# it is a measured number so the briefing's length is visible when it drifts.
ONE_SCREEN_CHARS = 1200

TEST_TOKEN = "123456:A-TOKEN-THAT-MUST-NEVER-BE-PRINTED"

CAPITAL = RiskConfig(
    equity=Money.from_rupees(Decimal("4200000")),
    max_symbol_exposure_fraction=0.05,
    max_portfolio_heat_fraction=0.025,
    daily_loss_limit_fraction=0.01,
    max_trades_per_day=3,
    verified=True,
    source="verify",
)


def _cycle(risk: RiskConfig | None = None) -> tuple[Briefing, Harness, CycleOutput]:
    harness = Harness()
    harness.queue_a_full_cycle()
    output = harness.cycle.run(harness.request())
    builder = BriefingBuilder(risk or load_risk_config(), harness.journal)
    return builder.build(output), harness, output


def _quiet_cycle() -> Briefing:
    harness = Harness()
    harness.queue_a_full_cycle()
    request = harness.request()
    output = harness.cycle.run(
        request.__class__(
            cycle_id=request.cycle_id,
            as_of=request.as_of,
            universe=request.universe,
            articles=request.articles,
            iv_ranks={},
        )
    )
    return BriefingBuilder(load_risk_config(), harness.journal).build(output)


def _the_five_sections_are_present() -> tuple[bool, str]:
    """FR-7.2, against the list in docs/06 §3."""
    briefing, _, _ = _cycle()
    whole = briefing.headline() + "\n" + briefing.detail()
    sections = {
        "market view": "MARKET VIEW" in whole,
        "ideas": bool(briefing.ideas),
        "rejects": "REJECTED" in whole or not briefing.rejected,
        "open theses": "OPEN THESES" in whole,
        "cost": "COST" in whole,
    }
    missing = [name for name, found in sections.items() if not found]
    return not missing, (
        f"all 5 sections present ({len(whole)} characters in total)"
        if not missing
        else f"missing: {missing}"
    )


def _every_gate_value_survives_the_trip() -> tuple[bool, str]:
    """docs/12: can you find every gate *value*, not just the verdicts?"""
    briefing, _, _ = _cycle()
    detail = briefing.detail()
    values = briefing.ideas[0].gate_values
    missing = [name for name, value in values.items() if name not in detail or value not in detail]
    return not missing, (
        f"{len(values)} gate values, all present with their measured numbers"
        if not missing
        else f"missing from the briefing: {missing}"
    )


def _the_decision_fits_the_first_message() -> tuple[bool, str]:
    """The 60-second test's mechanical half: the thing you act on is first and
    is short. Whether it is *actually* actionable is a human check."""
    briefing, _, _ = _cycle()
    headline = briefing.headline()
    has_decision = all(token in headline for token in ("Max loss/lot", "Size:", "Wrong if:"))
    return has_decision and len(headline) <= ONE_SCREEN_CHARS, (
        f"{len(headline)} characters, {headline.count(chr(10)) + 1} lines, "
        f"carries structure, size and invalidation"
    )


def _a_quiet_day_states_its_reason() -> tuple[bool, str]:
    """FR-4.9 as the reader sees it. 40-60% of sessions look like this."""
    briefing = _quiet_cycle()
    headline = briefing.headline()
    return "No ideas today." in headline and "Why:" in headline, (headline.splitlines()[-1][:80])


def _the_missing_capital_figure_is_visible_every_morning() -> tuple[bool, str]:
    """FR-6.2 wants a size and ISS-022 means there is not one. The check is
    that the absence is stated in the message you read, not buried."""
    briefing, _, _ = _cycle()
    size = briefing.ideas[0].hypothetical_size
    stated = "unavailable" in size and "ISS-022" in size and "unavailable" in briefing.headline()
    return stated, (
        f"size line reads: {size[:70]}…" if stated else f"the absence is not visible: {size[:70]}"
    )


def _with_a_capital_figure_a_size_appears() -> tuple[bool, str]:
    """The same briefing, with ₹42 lakh configured - the figure ISS-022 says
    one NIFTY spread lot needs."""
    briefing, _, _ = _cycle(CAPITAL)
    size = briefing.ideas[0].hypothetical_size
    return "lot(s)" in size or "0 lots" in size, size[:90]


def _the_evidence_carries_a_source_and_a_time() -> tuple[bool, str]:
    """FR-4.5: no claim without a source."""
    briefing, _, _ = _cycle()
    evidence = briefing.ideas[0].evidence
    complete = bool(evidence) and all(
        item.source and item.url and item.observed_at for item in evidence
    )
    return complete, f"{len(evidence)} evidence line(s), each with a publisher, a URL and a time"


def _the_cost_is_in_every_briefing() -> tuple[bool, str]:
    """FR-7.12."""
    briefing, _, output = _cycle()
    return "COST" in briefing.detail() and output.costs.total.paise > 0, (
        f"cycle cost {output.costs.total} across {len(output.costs.nodes)} nodes"
    )


def _delivery_splits_and_keeps_the_decision_first() -> tuple[bool, str]:
    briefing, _, _ = _cycle()
    notifier = FakeNotifier()
    deliver(briefing, notifier)

    chunks = split_message(briefing.detail())
    first_is_decision = notifier.briefings[0].startswith("BRIEFING")
    asked = len(notifier.decisions_asked)
    within = all(len(chunk) <= MAX_MESSAGE_CHARS for chunk in chunks)
    return first_is_decision and within and asked == len(briefing.ideas), (
        f"decision first, {asked} idea(s) asked about, detail splits into "
        f"{len(chunks)} message(s), longest {max(len(c) for c in chunks)} chars"
    )


def _a_failed_delivery_raises() -> tuple[bool, str]:
    """Rule 6: a briefing that was not delivered did not happen, and it must
    not look the same as a quiet day."""
    briefing, _, _ = _cycle()
    notifier = FakeNotifier()
    notifier.fail_next_send = True
    try:
        deliver(briefing, notifier)
    except ConnectionError:
        return True, "a failed send raises rather than reporting success"
    return False, "a failed delivery was swallowed"


def _the_bot_token_never_reaches_an_error() -> tuple[bool, str]:
    """Rule 8, induced. Telegram puts the credential in the URL path, so any
    code that echoes a URL leaks it."""
    import httpx  # noqa: PLC0415 - only this check needs a transport

    def refuse(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(400, json={"ok": False, "description": "bad request"})

    client = TelegramClient(TEST_TOKEN, "42", sleep=lambda _: None)
    client._client = httpx.Client(transport=httpx.MockTransport(refuse))
    try:
        client.call("sendMessage", {})
    except TelegramError as error:
        leaked = TEST_TOKEN in str(error)
        return not leaked, (
            "a refused call names the method, not the URL" if not leaked else "TOKEN LEAKED"
        )
    return False, "the refused call did not raise"


def _telegram_credentials_are_set() -> tuple[bool, str]:
    """Pending on Ajay. Nothing can be delivered until these exist."""
    try:
        settings = load_telegram_settings()
    except MissingConfigError as error:
        return False, str(error).splitlines()[0][:90]
    token = settings.bot_token.get_secret_value()
    return bool(token) and bool(settings.chat_id), (
        f"bot token set ({len(token)} chars, not printed), chat id {settings.chat_id}"
    )


def _a_real_briefing_has_been_received() -> tuple[bool, str]:
    """The actual done-when, and it is a human check.

    `docs/10`: "you receive a real briefing from real data and can act on it
    without opening a laptop." No test can assert that. This check fails until
    the credentials exist and `trading-desk brief` has run against them, which
    is the honest state of the step.
    """
    try:
        load_telegram_settings()
    except MissingConfigError:
        return False, "no Telegram credentials yet - run `trading-desk telegram-check` first"
    return False, "credentials present; run `trading-desk brief` and confirm it arrived"


def run() -> list[CheckResult]:
    checks = [
        (
            "the_five_sections_are_present",
            _the_five_sections_are_present,
            "need all 5 from docs/06 section 3 (FR-7.2)",
        ),
        (
            "every_gate_value_survives_the_trip",
            _every_gate_value_survives_the_trip,
            "need every value, not just verdicts (docs/12)",
        ),
        (
            "the_decision_fits_the_first_message",
            _the_decision_fits_the_first_message,
            f"need structure, size and invalidation within {ONE_SCREEN_CHARS} chars",
        ),
        (
            "a_quiet_day_states_its_reason",
            _a_quiet_day_states_its_reason,
            "need a reason in the first message (FR-4.9)",
        ),
        (
            "the_missing_capital_figure_is_visible",
            _the_missing_capital_figure_is_visible_every_morning,
            "need the absence stated where it is read (FR-6.2, ISS-022)",
        ),
        (
            "with_a_capital_figure_a_size_appears",
            _with_a_capital_figure_a_size_appears,
            "need a lot count once equity is configured",
        ),
        (
            "the_evidence_carries_a_source_and_a_time",
            _the_evidence_carries_a_source_and_a_time,
            "need a publisher, URL and timestamp per claim (FR-4.5)",
        ),
        (
            "the_cost_is_in_every_briefing",
            _the_cost_is_in_every_briefing,
            "need per-cycle cost in the message (FR-7.12)",
        ),
        (
            "delivery_splits_and_keeps_the_decision_first",
            _delivery_splits_and_keeps_the_decision_first,
            "need the decision first and no oversized message",
        ),
        (
            "a_failed_delivery_raises",
            _a_failed_delivery_raises,
            "need a raise - silence must not look like a quiet day (rule 6)",
        ),
        (
            "the_bot_token_never_reaches_an_error",
            _the_bot_token_never_reaches_an_error,
            "need 0 occurrences of the token in any error (rule 8)",
        ),
        (
            "telegram_credentials_are_set",
            _telegram_credentials_are_set,
            "need TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env",
        ),
        (
            "a_real_briefing_has_been_received",
            _a_real_briefing_has_been_received,
            "need one real delivery confirmed by you (docs/10 done-when)",
        ),
    ]
    results = []
    for name, check, requirement in checks:
        passed, measured = check()
        results.append(
            CheckResult(name=name, passed=passed, measured=measured, requirement=requirement)
        )
    return results
