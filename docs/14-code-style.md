# 14 — Code Style: Write It for an Intermediate Developer

The standing instruction for all code in this repo: **an intermediate Python developer
should understand any file in this project on a first read, without asking anyone.**

Who that reader is, concretely: comfortable with classes, functions, type hints, decorators
and `async`/`await`; has used pytest; has not memorised the descriptor protocol, metaclass
tricks, generic variance or `functools.singledispatch`. Can follow a 30-line function
easily. Loses the thread in a chain of three generic abstractions.

That reader is also **you in six months**, at 10pm, trying to work out why the briefing is
wrong. Write for that person, because that person is the maintainer.

---

## 1. The ten rules

1. **Boring beats clever.** If two versions work, ship the one that needs less thinking.
2. **No cleverness without a comment explaining why it was necessary.** If it was
   unavoidable, say what broke with the simple version.
3. **Explicit over implicit.** No metaclasses, no `__getattr__` magic, no dynamic class
   creation, no monkeypatching outside tests.
4. **Simple types.** `str`, `int`, `Decimal`, `bool`, enums, frozen Pydantic models,
   `list`, `dict`. Generics only where they genuinely prevent a bug.
5. **Functions do one thing** and are under ~30 lines. If you need a comment saying "now
   we do X", X is a function.
6. **Name things so they don't need comments.** `move_since_catalyst_atr_ratio` beats
   `ratio` with a comment explaining what ratio.
7. **Comments say *why*, never *what*.** The code says what. A comment that restates the
   line is noise that will go stale.
8. **Early return beats nesting.** Three levels of indentation is the limit.
9. **No inheritance for code reuse.** Pass a function or a port in. Inheritance is only for
   Protocol implementations.
10. **No premature abstraction.** Write it twice concretely. Abstract on the third use,
    when you can see what actually varies.

---

## 2. Examples from this project

### Gate functions — the most-read code in the repo

**Too clever:**

```python
GATES = {
    "catalyst_fresh": lambda c, cfg: (c.now - c.catalyst_at).days <= cfg.max_days,
    "credible": lambda c, cfg: c.credibility >= cfg.min_credibility,
    "not_priced_in": lambda c, cfg: abs(c.move_pct) < cfg.atr_mult * c.atr_pct,
}

def evaluate(ctx, cfg):
    return {k: f(ctx, cfg) for k, f in GATES.items()}
```

Compact, and wrong for this codebase. It returns bare booleans (breaking the
"gates return values" rule), the lambdas can't be unit-tested individually, a failure gives
you a dict key instead of a stack trace pointing at a line, and adding a gate that needs
two config values means rewriting the dispatch.

**Clear:**

```python
def check_catalyst_is_fresh(
    catalyst_at: datetime,
    now: datetime,
    max_age_days: int,
) -> GateResult:
    """Reject catalysts older than max_age_days. Old news is already in the price."""
    age_days = (now - catalyst_at).days
    return GateResult(
        name="catalyst_fresh",
        passed=age_days <= max_age_days,
        measured=age_days,
        threshold=max_age_days,
        unit="days",
    )


def check_not_already_priced_in(
    move_pct_since_catalyst: Decimal,
    atr_pct: Decimal,
    max_atr_multiple: Decimal,
) -> GateResult:
    """
    Reject ideas where the market has already moved.

    Why ATR and not a fixed percentage: a 4% move in a quiet stock is a big
    deal; in a volatile one it is a normal Tuesday. ATR normalises for that.
    """
    atr_multiple = abs(move_pct_since_catalyst) / atr_pct
    return GateResult(
        name="not_already_priced_in",
        passed=atr_multiple < max_atr_multiple,
        measured=atr_multiple,
        threshold=max_atr_multiple,
        unit="× ATR",
    )
```

Longer, and better: each function is independently testable, takes plain arguments rather
than a context object (so a test needs three values, not a fixture), and a failure names
itself in the traceback. The docstring on the second one earns its place — it explains a
decision you'd otherwise re-litigate.

### Composition — a plain loop beats a framework

```python
def evaluate_setup_a(ctx: SetupAContext, cfg: SetupAConfig) -> list[GateResult]:
    """Run every gate. Deliberately does not short-circuit - the briefing shows all of them."""
    return [
        check_catalyst_is_fresh(ctx.catalyst_at, ctx.now, cfg.max_catalyst_age_days),
        check_catalyst_type_allowed(ctx.catalyst_type, cfg.allowed_catalyst_types),
        check_source_credibility(ctx.credibility_score, cfg.min_credibility),
        check_not_already_priced_in(ctx.move_pct, ctx.atr_pct, cfg.max_atr_multiple),
        check_technical_confirmation(ctx.close, ctx.range_high_20d, ctx.volume_ratio, cfg),
        check_iv_rank_not_elevated(ctx.iv_rank, cfg.max_iv_rank),
        check_option_liquidity(ctx.open_interest_lots, ctx.spread_pct_of_mid, cfg),
        check_expiry_window(ctx.days_to_expiry, ctx.results_in_window, cfg),
        check_no_governance_flags(ctx.governance_flags),
    ]
```

Nine lines you can read top to bottom and compare against the table in
[06-v1-scope.md](06-v1-scope.md). No registry, no decorators, no plugin discovery. Adding a
gate is adding a line.

### Money — a boring class, deliberately

```python
@dataclass(frozen=True)
class Money:
    """
    An amount in paise. Integer, because floats lose rupees.

    Use from_rupees() for input, rupees() for display. Never build one
    from a float - that is how 0.1 + 0.2 becomes a position-sizing bug.
    """

    paise: int

    @classmethod
    def from_rupees(cls, rupees: Decimal) -> "Money":
        if not isinstance(rupees, Decimal):
            raise TypeError(f"Use Decimal, not {type(rupees).__name__}. Floats lose money.")
        return cls(paise=int((rupees * 100).to_integral_value(ROUND_HALF_UP)))

    def rupees(self) -> Decimal:
        return Decimal(self.paise) / 100

    def __add__(self, other: "Money") -> "Money":
        return Money(self.paise + other.paise)

    def __str__(self) -> str:
        return f"₹{self.rupees():,.2f}"
```

No generic `Currency` parameter, no unit framework, no arithmetic via `__getattr__`. This
project trades in one currency. If that ever changes, we'll change this class — and that
day will be cheaper than maintaining the general version for the years before it.

### Adapters — keep the mapping visible

```python
async def fetch_option_chain(self, instrument: InstrumentRef, expiry: date) -> OptionChainSnapshot:
    """Fetch one chain from Dhan and map it to our domain model."""
    raw = await self._client.option_chain(
        security_id=instrument.security_id,
        exchange_segment=instrument.segment.value,
        expiry=expiry.isoformat(),
    )

    strikes = []
    for row in raw["data"]["oc"]:
        strikes.append(
            OptionStrike(
                strike=Price.from_rupees(Decimal(row["strike_price"])),
                call=self._build_leg(row.get("ce")),
                put=self._build_leg(row.get("pe")),
            )
        )

    return OptionChainSnapshot(
        instrument=instrument,
        expiry=expiry,
        strikes=strikes,
        exchange_time=parse_ist_to_utc(raw["data"]["last_updated"]),
        received_at=self._clock.utc_now(),
    )
```

An explicit loop, not a nested comprehension. When Dhan changes a field name — and it
will — you want the line that breaks to be obvious.

---

## 3. What is banned outright

| Banned | Use instead |
|---|---|
| Metaclasses, `__init_subclass__` tricks | A plain function or an explicit registry list |
| `eval`, `exec`, dynamic imports by string | An explicit mapping |
| Deep inheritance (>1 level, Protocols aside) | Composition; pass the dependency in |
| Nested comprehensions, or any comprehension over ~2 lines | A `for` loop |
| Walrus in a condition that also calls something | Two lines |
| `*args, **kwargs` pass-through in our own code | Named parameters |
| `Any` outside an adapter boundary | A real type, or a `TypedDict` for raw payloads |
| Bare `except:` or `except Exception: pass` | Catch what you expect; let the rest fail closed |
| `utils.py`, `helpers.py`, `common.py`, `misc.py` | Name the concept |
| Clever one-liners in the risk gate or sizing code | Four boring lines. **This code is read under pressure** |

Two notes on that last row. The risk gate and sizing are the code most likely to be read in
a hurry, during an incident, by someone slightly panicked. It is the wrong place to be
proud of a dense expression.

And `Any` is allowed at adapter boundaries because broker JSON genuinely is unknown —
but it stops there. Nothing typed `Any` crosses into `application/` or `domain/`.

---

## 4. Docstrings and comments

**Every public function in `domain/` gets a docstring**, because that's the business logic
and six months from now you won't remember what "not already priced in" meant.

**Write the *why*, not the what:**

```python
# Bad - restates the code
# loop through strikes and find the closest to spot
for strike in strikes: ...

# Good - explains a decision
# Dhan sends a snapshot tick on subscribe that repeats the day's cumulative
# volume. Counting it would double the first bar, so we skip the first tick
# per instrument per session.
if self._is_first_tick(token):
    return
```

**Module docstrings say what belongs in the file** — a one-liner that stops the module
drifting into a junk drawer:

```python
"""Setup A gates: catalyst continuation on F&O stocks. Pure functions, no I/O.

Thresholds live in config/setups.yaml. See docs/06-v1-scope.md section 2.
"""
```

---

## 5. Tests are read more than code

```python
def test_rejects_when_stock_already_moved_more_than_1_5_atr():
    result = check_not_already_priced_in(
        move_pct_since_catalyst=Decimal("6.0"),
        atr_pct=Decimal("3.0"),           # 6.0 / 3.0 = 2.0x ATR
        max_atr_multiple=Decimal("1.5"),
    )

    assert result.passed is False
    assert result.measured == Decimal("2.0")
```

Long test names that state the rule. Literal values with the arithmetic in a comment, not
computed in the test. **No loops or fixtures where three literals would do** — a test is
documentation, and parametrising it to save eight lines costs you the ability to read it.

---

## 6. When simple genuinely isn't enough

Three places in this project where complexity is earned, and they are the only ones:

1. **The async market-data path.** Concurrency is inherent; it can't be simplified away.
   Keep it isolated in the adapter, and keep the rest synchronous.
2. **`hypothesis` property tests on the risk gate.** Harder to read than example-based
   tests, and worth it — they find the inputs you wouldn't have thought of, in the one
   place a bug costs money.
3. **The LangGraph agent graph.** A framework whose structure you have to learn. Confined
   to `agents/`, and even there the node bodies stay plain functions.

Outside those three, if a solution feels sophisticated, it's probably the wrong solution.

---

## 7. The review question

Before committing, ask one thing:

> **Would an intermediate developer reading this file, with no context, understand what it
> does and why — or would they have to ask me?**

If they'd have to ask, the code isn't finished. Rename something, split a function, or add
a *why* comment. **This applies to code written by an AI assistant as much as code written
by you** — arguably more, since an assistant can produce something sophisticated faster
than you can evaluate whether it needed to be.
