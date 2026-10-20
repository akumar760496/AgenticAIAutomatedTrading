# CLAUDE.md — operating rules for this repository

Loaded automatically into every session. **Keep it short and stable**: everything here is
re-sent on every request, and a stable prefix is what makes prompt caching cheap. Volatile
content belongs in `docs/state/` (read on demand), never here.

Full workflow explanation: [docs/13-dev-workflow.md](docs/13-dev-workflow.md).

---

## 1. What this project is

An agentic market-research system for Indian equities and options. **v1 produces a daily
briefing with at most two ranked ideas, delivered to Telegram, traded manually. There is no
order path in v1.** Plan docs in `docs/`, numbered 00–12.

## 2. Non-negotiable rules

1. **An LLM never places an order, and never sizes one.** `TradeProposal` has no quantity,
   lot, price or order-type field. Adding one is a CI failure, not a review comment.
2. **The dependency rule points inward**, always: `interfaces → adapters → application →
   ports → domain`. `domain/` imports nothing internal and nothing with I/O or a clock.
   Enforced by `import-linter`; never work around it.
3. **`domain/` is pure.** No network, no DB, no clock, no LLM, no `pandas`. If a domain
   function needs the time, it takes it as an argument.
4. **Money is never a float.** `Money` (integer paise) or `Decimal`. Never `float` for a
   price, premium or P&L.
5. **All timestamps are tz-aware UTC internally.** IST only at the edges. `datetime.now()`
   appears nowhere outside the clock adapter.
6. **Fail closed.** Stale data, disconnect, unhandled exception → no output, never a
   guessed value. Silence beats a wrong briefing.
7. **Exchange rules are config, not code.** Lot sizes, expiry days, eligibility live in
   `config/*.yaml` with effective-from dates.
8. **No secret in git, ever.** Secrets come from `.env` / the secret store. Never echo a
   credential into a log, a commit, a briefing or a chat message.
9. **Gates return values, not booleans.** Every gate reports what it measured and the
   threshold it compared against.
10. **Never widen scope silently.** A new setup, data source or agent needs an entry in
    `docs/state/DEVLOG.md` saying why, before the code.
11. **Warn before writing, not after.** A legal or compliance doubt about a source, API or
    document is raised **in that message, before any code is written** — and the finding is
    recorded, quoted, in the config the next session reads (`config/news_sources.yaml` for
    news, an `ISS-` entry otherwise). A source is used when its terms **clearly allow** it,
    not when they merely fail to forbid it. Unread terms are not permission; robots.txt is
    not a licence. Says nothing about terms → treat it as blocked and say so.

## 3. Where code goes

| Kind of code | Location |
|---|---|
| Entities, value objects, pure maths, gate logic, risk rules | `src/<pkg>/domain/` |
| Interface definitions (Protocols) for anything external | `src/<pkg>/ports/` |
| Use cases, orchestration, scheduling | `src/<pkg>/application/` |
| LLM agent graphs and prompts | `src/<pkg>/agents/` |
| Dhan, news, Telegram, DB, LLM implementations | `src/<pkg>/adapters/` |
| Config loading, logging, DB session, metrics | `src/<pkg>/infra/` |
| CLI, FastAPI, Telegram handlers | `src/<pkg>/interfaces/` |

**If unsure where something goes, it goes in `domain/` as a pure function, with the I/O
lifted into an adapter.** That answer is right more often than it looks.

## 4. Conventions

- Python 3.12. `uv` for everything. `ruff` + `mypy --strict` must be clean.
- Frozen Pydantic v2 models / dataclasses for domain objects. No mutation.
- Names say what a thing is, not what it's made of: `OptionChainSnapshot`, not
  `ChainDataDict`. No `utils.py`, no `helpers.py`, no `manager.py`.
- One public concept per module. A module over ~300 lines is probably two modules.
- Tests mirror the source tree. Every port has a fake; the suite runs with no network.
- Commit messages reference the build step: `[1.3] bar builder: handle session boundary`.

## 4b. Write code an intermediate developer can read

**Standing instruction, applies to everything.** The reader is a competent Python developer
who has not memorised metaclasses or generic variance — and is also you in six months at
10pm debugging a wrong briefing.

- Boring beats clever. Two working versions → ship the one needing less thought.
- No cleverness without a comment saying why the simple version failed.
- Functions under ~30 lines, one job, max 3 indent levels, early returns.
- Plain types: `str`, `int`, `Decimal`, enums, frozen models, `list`, `dict`.
- Comments explain **why**, never what. Names remove the need for comments.
- No metaclasses, no `eval`, no `__getattr__` magic, no inheritance for reuse, no
  `utils.py`. No comprehension over 2 lines — write the loop.
- Write it twice concretely before abstracting. Abstract on the third use.
- **The risk gate and sizing code get the most boring version available.** It is read under
  pressure, during an incident.

Full rules with before/after examples: [docs/14-code-style.md](docs/14-code-style.md).

## 5. Session protocol

**At the start of any work session**, read in this order and stop:
`docs/state/STATE.md` → `docs/state/ISSUES.md` (open items only) → the one step you are
working on in `docs/10-build-plan.md`.

**Do not read all of `docs/` to answer a question.** Grep for the term. The docs are
~2,900 lines; reading them all costs more than the work.

**At the end of any session that changed something**, update `docs/state/STATE.md` and
append to `docs/state/DEVLOG.md`. A session that ends without updating state did not happen,
because the next session cannot see it.

**Never run `git commit` or `git push` unless the user asks in that message.** Write the
files, run `make check` and `make verify`, update the state docs, then stop and list what
changed. The user reviews the diff in their editor's Source Control panel and commits it
themselves. Leaving changes uncommitted is the point: it is how they see the work.

An earlier yes is never standing permission for the next time.

**Skills** live in `.claude/skills/`. Use `build-step` to implement a step, `verify-step`
to prove one is done, `architecture-guard` before adding a module, `issue-log` when
something breaks. Load a skill when the task matches; don't inline its content here.

## 6. Context budget

| Task | Read this, nothing else |
|---|---|
| Implementing a step | `STATE.md`, the step in `10-build-plan.md`, the target module, its test |
| Verifying a step | `STATE.md`, the step in `12-verification.md`, `VERIFICATION.md` tail |
| "Why was this decided?" | `grep` `DEVLOG.md`, then the relevant doc section |
| A requirement question | `grep` the FR id in `08-requirements.md` |
| A market-rule question | `config/*.yaml` first, then `06-v1-scope.md` |

Prefer `grep -n` and `sed -n 'X,Yp'` over reading whole files. State the step ID you are
working on in your first message so the session has an anchor.
