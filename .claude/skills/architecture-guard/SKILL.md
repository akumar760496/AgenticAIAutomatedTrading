---
name: architecture-guard
description: Decide which architectural layer new code belongs in for this trading project, and check a change against the clean-architecture and separation-of-concerns rules. Use before adding a module, when a layering question arises, when import-linter fails, or when reviewing a diff.
---

# Architecture guard

The layering is the only thing preventing this project becoming a script with opinions.

## The dependency rule

```
interfaces  →  adapters  →  application  →  ports  →  domain
(CLI, API,     (Dhan, news,   (use cases,    (Protocols)  (pure logic)
 Telegram)      LLM, DB)       scheduling)
```

Arrows are the **only** permitted direction. `domain/` imports nothing internal, and
nothing that does I/O or reads a clock. `agents/` may import `domain` and `ports` only —
an agent never touches an adapter directly.

## Placing new code — three questions

1. **Does it make a network call, read a file, hit a DB, call an LLM, or read the time?**
   → Its *implementation* is an adapter. Define a Protocol in `ports/` first.
2. **Is it a decision, a calculation, a rule or an invariant?**
   → `domain/`, as a pure function taking everything it needs as arguments.
3. **Does it sequence other things in order?**
   → `application/`.

If the answer is "both 1 and 2", you have two modules: the pure part and the I/O part.
Split them. That split is usually the whole design.

## Smells, with the fix

| Smell | Fix |
|---|---|
| `from ..adapters import` inside `domain/` or `application/` | Depend on the port; inject the adapter at the edge |
| `datetime.now()` outside the clock adapter | Pass the timestamp in |
| `float` holding money | `Money` (integer paise) or `Decimal` |
| A gate returning `True`/`False` | Return the measured value, threshold and verdict |
| An agent deciding whether a threshold is met | The agent gathers inputs; the gate in `domain/` decides |
| A threshold literal in code | `config/setups.yaml` |
| `utils.py`, `helpers.py`, `common.py`, `manager.py` | Name the concept instead |
| A module over ~300 lines | Probably two concepts sharing a file |
| Test needing a live connection | The port lacks a fake |
| `if mode == "live"` in `domain/` or `application/` | Mode is a wiring choice; inject different adapters |

## Checks

```bash
lint-imports          # the layering contracts
mypy --strict src/
grep -rn "datetime.now()" src/ | grep -v adapters/clock
grep -rn ": float" src/ | grep -iE "price|premium|pnl|amount|money"
```

## When the rule is inconvenient

It is supposed to be. Two legitimate moves: lift the I/O into an adapter and pass data in,
or introduce a port. **Adding an exception to `.importlinter` is not one of them** — if it
ever seems necessary, write the case in `docs/state/DEVLOG.md` and ask the user, because
that decision is architectural, not tactical.
