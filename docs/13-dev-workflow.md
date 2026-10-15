# 13 — Development Workflow, Agent Rules & Context Management

How the AI assistant working on this codebase stays consistent, keeps track of where the
project is, and does it without burning tokens. You asked for a dynamic `skills.md` that
does all of this in one file; §1 explains why it is three things instead, and what you get
from the split.

---

## 1. Why not one file

A single always-loaded file that grows with every development session has one fatal
property: **it is re-sent, in full, on every single request.** A file that reaches 2,000
lines after three months of diligent appending costs ~25k tokens *per message* — so the
mechanism designed to reduce token usage becomes the largest consumer of it. Worse, the
useful content gets buried: by month three, rules from week one sit under 40 session logs,
and the assistant reads the logs with the same weight as the rules.

The fix is to separate content by **how often it changes and how often it is needed**:

| Concern | File | Changes | Loaded |
|---|---|---|---|
| Rules that always apply | `CLAUDE.md` (94 lines) | Rarely | Automatically, every request |
| Task procedures | `.claude/skills/*/SKILL.md` | Rarely | Only when the task matches |
| Where we are now | `docs/state/STATE.md` (~40 lines) | Every session, **rewritten** | At session start |
| Why things are as they are | `docs/state/DEVLOG.md` | Appended, monthly rollup | Grepped, or `tail` |
| What's broken | `docs/state/ISSUES.md` | As found | Open section only |
| Evidence a step works | `VERIFICATION.md` | On step completion | Grepped |

Same information, same dynamism — but a request pays only for what it needs. **`CLAUDE.md`
staying stable also matters for prompt caching**: a stable prefix is cached, while a prefix
that changes every session invalidates the cache on every request. Putting the churn in
separate files is what makes the caching work.

---

## 2. `CLAUDE.md` — the always-loaded contract

Claude Code reads `CLAUDE.md` from the repo root automatically. (A file named `skills.md`
would not be read unless something told the assistant to read it, which is exactly the kind
of instruction that gets forgotten — so the name matters.)

It holds four things and nothing else: the ten non-negotiable rules, the table of where
code goes, the conventions, and the session protocol. **It is deliberately capped at
around 100 lines.** Anything that wants to grow belongs in a skill or in `docs/state/`.

The rules are written as prohibitions with mechanical enforcement where possible — "an LLM
never sizes an order; adding a quantity field is a CI failure" rather than "be careful
about position sizing". A rule a test can enforce is a rule that survives; a rule that
relies on remembering is a rule that lasts six weeks.

---

## 3. Skills — procedures loaded on demand

Five skills, each a short procedure for one recurring task.

| Skill | Use it for | Why it exists |
|---|---|---|
| `session-start` | Resuming work, "where were we" | Orientation in <2k tokens instead of reading `docs/` |
| `build-step` | Implementing a numbered step | Same sequence every time: load step → place layers → implement → verify → record |
| `verify-step` | Proving a step is done | Separates "the code runs" from "there is evidence" |
| `architecture-guard` | Placing new code, layering questions | The clean-architecture decision procedure, as three questions |
| `issue-log` | Something broke, or a question blocks | Nothing found mid-step gets lost or fixed as a drive-by |

A skill is loaded only when its description matches the task, so none of them costs
anything the rest of the time. They are the "dynamic" part of your request, done in a way
that stays cheap: adding a sixth skill costs nothing on the sessions that don't need it.

**`build-step` is where your separation-of-concerns requirement is actually enforced**, in
sequence: decide the layer before writing, define a port before an adapter, keep pure logic
pure, put thresholds in config, write the verify script, then update state. Every step
through the same funnel.

---

## 4. The state files — development tracked

### `STATE.md` — rewritten, never appended

Current phase, active step, what's done, next action, what's blocked on you, risks being
watched. ~40 lines, so it can be read at the start of every session for almost nothing.

**It is rewritten in place.** This is the one file where history is deliberately discarded,
because its entire job is to answer "what now?" in as few tokens as possible.

### `DEVLOG.md` — appended, with a rollup policy

One entry per session that changed something: what was built, decisions made, surprises,
rejected alternatives, follow-ups. Five lines or fewer.

What goes in is strictly **decisions and surprises** — not what the code does, because the
code says that better. The test for an entry: *would a session three months from now
re-argue this if it weren't written down?*

**The rollup policy is the part that keeps it honest.** At each month end, a ≤10-line
summary replaces that month's entries, which move to `DEVLOG-ARCHIVE.md`. Without this, the
file meant to save tokens becomes the thing that costs them — the exact failure the split
in §1 exists to prevent.

### `ISSUES.md` — open items read every session

Each issue carries symptom, hypothesis, and — the field that matters — **impact on a
trading decision**. "Volume is 8% high" is a bug; "the volume gate would produce false
positives" is why it blocks a step.

Two severities block work: **safety** (anything touching the risk gate, sizing, limits or
the order path) and **data-correctness**. Everything else is logged and work continues,
because drive-by fixes make a step's diff unreviewable and its verification ambiguous.

One field exists specifically for this project: **is the affected data recoverable?** For
chain snapshots the answer is no, which raises the severity of anything touching them.

### `VERIFICATION.md` — the evidence trail

Appended on step completion: the verify output, what guard was deliberately induced, and
one line in your words for each human check. This is the file that tells you in February
why you believed Step 1.8 was correct in December.

---

## 5. Token discipline, concretely

Six rules that do the work:

1. **Grep, don't read.** The docs are ~2,900 lines. `grep -n "FR-6.3" docs/08*` costs ~100
   tokens; reading the file costs ~4,000.
2. **Read one step, not the plan.** `awk '/^### Step 1\.3 /,/^### Step 1\.4 /'` pulls the
   40 lines that matter out of 694.
3. **Stable prefix, volatile suffix.** `CLAUDE.md` barely changes, so it caches. State
   lives in files read on demand.
4. **Context recipes.** `CLAUDE.md` §6 lists, per task type, exactly which files to open.
   Following a recipe beats deciding what might be relevant.
5. **Rewrite over append** wherever the history has no value — `STATE.md` is the model.
6. **One step per session.** The cheapest context is the context you don't need because the
   work is finished and recorded.

The failure mode to watch for: an assistant that opens six documents to answer a
one-sentence question. If that happens, the recipe in §6 is wrong — fix the recipe.

---

## 6. The loop, per session

```
  session-start skill
        │  STATE.md + open ISSUES + the one active step
        ▼
  build-step skill
        │  architecture-guard if placement is unclear
        │  issue-log if something unrelated breaks
        ▼
  make check  →  verify-step skill  →  make verify STEP=x_y
        │
        │  X and H checks listed back to you — the assistant
        │  cannot cross-check against Dhan's screen
        ▼
  record: STATE.md rewritten · DEVLOG.md appended · VERIFICATION.md appended
        │
        ▼
  commit  [X.Y] module: what changed
```

**A session that ends without updating state did not happen**, because the next session
cannot see it. That is the one procedural rule worth being strict about.

---

## 7. Suggestions you didn't ask for

Four things I'd add, in order of value:

**1. A `plan-change` skill, and the discipline behind it.** The highest-probability failure
of this project is scope creep (`docs/05-realities.md` says so, and it remains true). Right
now a new setup or data source could be added without anything objecting. A skill that
requires a `DEVLOG` entry naming what is being added, what it replaces, and which stop
criterion it affects — before any code — turns scope creep into a decision you have to make
in writing rather than one that happens to you.

**2. Put the repo under git before Step 0.1 writes a line of code.** It isn't a repo yet.
The plan docs should be the first commit, so that when February's thresholds differ from
October's you can see exactly when and why they moved. `DEVLOG.md` is far more useful
alongside a commit history than on its own.

**3. A weekly `STATE.md` snapshot into `DEVLOG.md`.** Three lines, every Sunday: phase,
step, hours spent. After two months this is the only honest data you'll have on your real
velocity — and that number decides whether `docs/10` §7's cut-down plan is the one you
should be following. Guessing at it in December will be optimistic; measuring it won't be.

**4. A `pre-live-audit` skill, written now, used much later.** A checklist that must pass
before the first real order: kill switch induced and witnessed, drawdown halt induced,
reconciliation tested against a real divergence, static IP registered, every `safety` issue
closed. Writing it now, while nothing is at stake, is the same logic as
`docs/07-stop-criteria.md` — the judgment is better before the money is involved.

One thing I would **not** add: a skill per module or per agent. Skills earn their place by
being used repeatedly across the project. A skill used once is just a document that has to
be maintained, and five good ones beat fifteen stale ones.
