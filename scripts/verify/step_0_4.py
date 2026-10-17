"""Verify step 0.4 - value objects and the money rule.

The point of this step is that certain mistakes become impossible. So the checks
try to make them and confirm they fail.
"""

from __future__ import annotations

import ast
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from scripts.verify._runner import CheckResult
from trading_desk.adapters.clock import SystemClock, to_ist
from trading_desk.domain.enums import Exchange, OptionRight, Segment
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.values import Money, Percent, Price, Quantity

TITLE = "Value objects and the money rule"

ROOT = Path(__file__).resolve().parents[2]
DOMAIN = ROOT / "src" / "trading_desk" / "domain"


def _raises(action: object) -> bool:
    """True when calling `action` raises - i.e. the mistake was prevented."""
    try:
        action()  # type: ignore[operator]
    except (TypeError, ValueError):
        return True
    return False


def _float_money_rejected() -> bool:
    return _raises(lambda: Money.from_rupees(10.5))  # type: ignore[arg-type]


def _float_multiply_rejected() -> bool:
    return _raises(lambda: Money.from_rupees(Decimal("10")) * 1.5)  # type: ignore[operator]


def _half_option_rejected() -> bool:
    return _raises(
        lambda: InstrumentRef(
            symbol="NIFTY",
            exchange=Exchange.NSE,
            segment=Segment.FNO,
            security_id="1",
            expiry=date(2026, 10, 20),
        )
    )


def _decimal_arithmetic_is_exact() -> bool:
    total = Money.from_rupees(Decimal("0.10")) + Money.from_rupees(Decimal("0.20"))
    return total.rupees() == Decimal("0.30")


def _risk_sizing_is_exact() -> bool:
    """0.25% of 10 lakh must be exactly 2500 - the v1 flat risk number."""
    equity = Money.from_rupees(Decimal("1000000"))
    return Percent(Decimal("0.25")).of(equity).rupees() == Decimal("2500.00")


def _clock_calls_outside_adapter() -> list[str]:
    """Only the clock adapter may read the system clock.

    Walks the AST rather than scanning text. The text version flagged its own
    docstring the moment a comment explained what it looked for - and its docstring
    said so, which is a sign the check was wrong rather than the comment. The AST
    sees calls only, so prose about `datetime.now()` is free to exist.

    Catches the aliased forms too: `dt.now()` after `import datetime as dt`, and
    `date.today()`, which the text scan never looked for at all.
    """
    banned = {"now", "today", "utcnow", "fromtimestamp"}
    offenders = []

    for module in sorted((ROOT / "src").rglob("*.py")):
        if "adapters/clock.py" in module.as_posix():
            continue

        tree = ast.parse(module.read_text(), filename=str(module))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if not isinstance(function, ast.Attribute) or function.attr not in banned:
                continue
            # Only flag it when the receiver is a plain name - datetime.now(),
            # dt.now(), date.today(). self._clock.now() is the correct pattern
            # and must stay allowed.
            receiver = function.value
            if isinstance(receiver, ast.Name) and receiver.id in {"datetime", "date", "dt", "time"}:
                offenders.append(f"{module.name}:{node.lineno}")

    return offenders


def _option_renders_readably() -> tuple[bool, str]:
    option = InstrumentRef(
        symbol="NIFTY",
        exchange=Exchange.NSE,
        segment=Segment.FNO,
        security_id="43925",
        lot_size=65,
        expiry=date(2026, 10, 20),
        strike=Price.from_rupees(Decimal("25000")),
        right=OptionRight.CALL,
    )
    rendered = str(option)
    return rendered == "NIFTY 2026-10-20 25000 CE", rendered


def _utc_clock_is_tz_aware() -> bool:
    now = SystemClock().utc_now()
    return now.tzinfo is not None and now.utcoffset() == datetime.now(UTC).utcoffset()


MARKET_OPEN_HOUR_IST = 9
MARKET_OPEN_MINUTE_IST = 30


def _ist_conversion_at_market_open() -> bool:
    """04:00 UTC is 09:30 IST - the NSE open. Catches a wrong or missing tz."""
    ist = to_ist(datetime(2026, 10, 15, 4, 0, tzinfo=UTC))
    return ist.hour == MARKET_OPEN_HOUR_IST and ist.minute == MARKET_OPEN_MINUTE_IST


def run() -> list[CheckResult]:
    stray_clock_calls = _clock_calls_outside_adapter()
    renders_ok, rendered = _option_renders_readably()
    premium = Price.from_rupees(Decimal("125.50"))
    position_value = premium.times(Quantity.from_lots(lots=2, lot_size=65))

    return [
        CheckResult(
            name="float_money_rejected",
            passed=_float_money_rejected(),
            measured=str(_float_money_rejected()),
            requirement="need true - Money(10.5) must raise",
        ),
        CheckResult(
            name="float_multiply_rejected",
            passed=_float_multiply_rejected(),
            measured=str(_float_multiply_rejected()),
            requirement="need true - Money * 1.5 must raise",
        ),
        CheckResult(
            name="decimal_arithmetic_exact",
            passed=_decimal_arithmetic_is_exact(),
            measured="0.10 + 0.20 = 0.30",
            requirement="need exact, not 0.30000000000000004",
        ),
        CheckResult(
            name="risk_sizing_exact",
            passed=_risk_sizing_is_exact(),
            measured="0.25% of 10,00,000 = 2500.00",
            requirement="need exactly 2500.00",
        ),
        CheckResult(
            name="price_times_quantity",
            passed=position_value.rupees() == Decimal("16315.00"),
            measured=str(position_value),
            requirement="need ₹16,315.00 (125.50 x 130)",
        ),
        CheckResult(
            name="half_option_rejected",
            passed=_half_option_rejected(),
            measured=str(_half_option_rejected()),
            requirement="need true - expiry without strike must raise",
        ),
        CheckResult(
            name="option_renders_readably",
            passed=renders_ok,
            measured=rendered,
            requirement="need 'NIFTY 2026-10-20 25000 CE'",
        ),
        CheckResult(
            name="no_stray_clock_calls",
            passed=not stray_clock_calls,
            measured=f"{len(stray_clock_calls)} outside adapter",
            requirement="need 0 - only adapters/clock.py reads the clock",
        ),
        CheckResult(
            name="clock_is_tz_aware_utc",
            passed=_utc_clock_is_tz_aware(),
            measured=str(_utc_clock_is_tz_aware()),
            requirement="need true",
        ),
        CheckResult(
            name="ist_conversion",
            passed=_ist_conversion_at_market_open(),
            measured="04:00 UTC -> 09:30 IST",
            requirement="need market open to convert correctly",
        ),
    ]
