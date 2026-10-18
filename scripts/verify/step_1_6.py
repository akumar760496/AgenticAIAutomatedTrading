"""Verify step 1.6 - calendars.

The central claim is negative: **nothing computes an expiry date.** So the first
check induces the mistake - it applies the "NIFTY weekly is Tuesday" rule the
plan originally stated, against the exchange's live list, and requires them to
disagree. If they ever agree, either the holidays have moved or someone has
quietly started computing dates, and both are worth knowing (ISS-004).

The second claim is that the expiry list audits the holiday file. A Tuesday the
exchange skipped must appear in config/holidays.yaml. This is what found
2026-11-24 missing after four verification sweeps had passed.
"""

from __future__ import annotations

from datetime import date, timedelta

from scripts.verify._runner import CheckResult
from trading_desk.adapters.clock import SystemClock
from trading_desk.adapters.dhan.chain import DhanOptionChainAdapter
from trading_desk.adapters.dhan.client import DhanClient
from trading_desk.adapters.dhan.token_store import StoredTokenProvider, load_token
from trading_desk.application.calendars import load_trading_calendar
from trading_desk.application.event_calendar import load_event_calendar
from trading_desk.domain.enums import Exchange, Segment
from trading_desk.domain.events import EventKind
from trading_desk.domain.expiries import displaced_expiries, weekly_expiries
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.infra.config import load_dhan_settings

TITLE = "Calendars: expiries, holidays and events"

TUESDAY = 1

NIFTY = InstrumentRef(
    symbol="NIFTY",
    exchange=Exchange.NSE,
    segment=Segment.FNO,
    security_id="13",
    lot_size=65,
)


def _stored_token_exists() -> bool:
    try:
        load_token()
    except (FileNotFoundError, KeyError, ValueError):
        return False
    return True


def _live_expiries() -> tuple[list[date], str]:
    if not _stored_token_exists():
        return [], "no stored Dhan token - run: make login"

    settings = load_dhan_settings()
    client = DhanClient(client_id=settings.client_id, token_provider=StoredTokenProvider())
    try:
        return DhanOptionChainAdapter(client, SystemClock()).available_expiries(NIFTY), ""
    except Exception as error:  # report it, do not crash the harness
        return [], f"{type(error).__name__}: {str(error)[:100]}"
    finally:
        client.close()


def _computed_tuesdays(start: date, count: int) -> list[date]:
    """The naive rule the plan originally stated: every Tuesday, no exceptions."""
    first = start + timedelta(days=(TUESDAY - start.weekday()) % 7)
    return [first + timedelta(days=7 * week) for week in range(count)]


def run() -> list[CheckResult]:
    expiries, fetch_error = _live_expiries()
    calendar = load_trading_calendar()
    events = load_event_calendar()

    if not expiries:
        return [CheckResult("live_expiries", False, fetch_error, "need the exchange's list")]

    weekly = weekly_expiries(expiries)
    displaced = displaced_expiries(expiries)
    implied_holidays = [item.expected for item in displaced]
    missing = calendar.missing_holidays(implied_holidays)

    computed = _computed_tuesdays(weekly[0], len(weekly))
    rule_disagrees = computed != weekly

    evidence_backed = [
        day for day in calendar.holidays if calendar.evidence_for(day) == "expiry_shift"
    ]
    policy_events = events.of_kind(EventKind.RBI_POLICY)
    sourced = all("rbi.org.in" in event.source for event in policy_events)

    return [
        CheckResult(
            name="weekday_rule_disagrees_with_the_exchange",
            passed=rule_disagrees,
            measured=(
                f"rule says {[d.isoformat() for d in computed[:3]]}, "
                f"exchange says {[d.isoformat() for d in weekly[:3]]}"
            ),
            requirement="need them to differ - a computed expiry names contracts "
            "that do not exist (ISS-004)",
        ),
        CheckResult(
            name="displaced_expiries_detected",
            passed=len(displaced) > 0,
            measured=f"{len(displaced)} displaced: "
            + ", ".join(f"{d.expected}->{d.actual}" for d in displaced),
            requirement="need the holiday-shifted expiries found from the live list",
        ),
        CheckResult(
            name="holiday_file_matches_the_exchange",
            passed=not missing,
            measured=(
                "every implied holiday is listed"
                if not missing
                else f"missing {[d.isoformat() for d in missing]}"
            ),
            requirement="need every Tuesday the exchange skipped to be in holidays.yaml",
        ),
        CheckResult(
            name="holidays_carry_evidence",
            passed=len(evidence_backed) > 0 and calendar.verified,
            measured=(
                f"{len(evidence_backed)}/{len(calendar.holidays)} proven by expiry shift, "
                f"verified={calendar.verified}"
            ),
            requirement="need stated provenance for every date",
        ),
        CheckResult(
            name="events_load_and_are_sourced",
            passed=bool(policy_events) and sourced,
            measured=f"{len(events.events)} events, {len(policy_events)} RBI, "
            f"{len(events.unverified())} unverified",
            requirement="need RBI dates traced to rbi.org.in",
        ),
        CheckResult(
            name="event_query_answers_a_hand_checked_window",
            passed=(
                [event.on_date for event in events.between(date(2026, 12, 1), date(2026, 12, 31))]
                == [date(2026, 12, 4)]
            ),
            measured=str(
                [
                    event.on_date.isoformat()
                    for event in events.between(date(2026, 12, 1), date(2026, 12, 31))
                ]
            ),
            requirement="need exactly the 2026-12-04 MPC decision in December",
        ),
    ]
