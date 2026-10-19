"""The daily briefing: the five sections of `docs/06` §3, as data and as text.

Pure. No clock, no network, no Telegram - so the exact words that reach the
phone are testable without sending anything.

**Two requirements pull against each other here and the split resolves them.**
`docs/12` asks that you can act on the briefing "standing up, without scrolling
past the decision", and also that you can find *every gate value, not just
verdicts*. Two ideas carry eighteen gate values between them, so both cannot be
in the first screen. The briefing therefore renders in two parts:

- `headline()` - one line of market view, then the ideas with the numbers that
  decide: structure, max loss per lot, hypothetical size, confidence, the
  invalidation. This is the message you act on.
- `detail()` - every gate value, the evidence with URLs and timestamps, the
  rejected candidates with the gate that killed each one, open theses, and the
  cycle's cost. This is the message you audit.

`docs/06` lists market view first and it mostly still is: the one line that
changes a decision leads, the rest of the regime detail sits in the detail
message. Written down because it is a deviation from the stated order.

**Plain text, not Markdown.** Telegram's MarkdownV2 requires escaping `. - ( )
! +` and more, and this text is full of them: "₹10,500.00", "0.52 x ATR",
"(proxy)". One missed escape is a 400 from the API, which means a briefing that
did not arrive. A briefing that did not arrive is the one failure this whole
step exists to prevent, so the formatting is plain and the risk is zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

# Telegram's hard limit is 4096 characters per message. The renderer does not
# enforce it - splitting is the adapter's job, because 4096 is a fact about
# Telegram - but the detail message is built in blocks so it can be split on a
# boundary that makes sense rather than mid-sentence.
BLOCK_SEPARATOR = "\n\n"


@dataclass(frozen=True)
class EvidenceLine:
    """One claim with where it came from. FR-4.5: no claim without a source."""

    claim: str
    source: str
    url: str
    observed_at: str


@dataclass(frozen=True)
class IdeaCard:
    """One idea, with everything needed to judge it and nothing to place it.

    `hypothetical_size` is a string rather than a number because its most
    common value today is a sentence explaining why there isn't one (ISS-022).
    A number with an optional "unavailable" beside it invites reading past the
    absence.
    """

    idea_id: UUID
    rank: int
    symbol: str
    setup: str
    direction: str
    structure: str
    expiry: str
    max_loss_per_lot: str
    reward_to_risk: str
    confidence: str
    claim: str
    invalidation: str
    entry: str
    stop: str
    target: str
    hypothetical_size: str
    gate_values: dict[str, str] = field(default_factory=dict)
    evidence: tuple[EvidenceLine, ...] = ()
    used_iv_proxy: bool = False

    def decision_text(self) -> str:
        """What you read to decide. Everything auditable is in `detail_text`."""
        lines = [
            f"#{self.rank}  {self.symbol}  {self.direction}  ({self.setup})",
            f"  {self.structure}  exp {self.expiry}",
            f"  Max loss/lot {self.max_loss_per_lot}   R:R {self.reward_to_risk}",
            f"  Size: {self.hypothetical_size}",
            f"  Confidence {self.confidence}" + (" [IV proxy]" if self.used_iv_proxy else ""),
            f"  {self.claim}",
            f"  Wrong if: {self.invalidation}",
        ]
        return "\n".join(lines)

    def detail_text(self) -> str:
        """Every gate value and every source. The audit half."""
        lines = [f"#{self.rank} {self.symbol} - all gate values"]
        lines.extend(f"  {name}: {value}" for name, value in sorted(self.gate_values.items()))
        lines.append(f"  entry: {self.entry}")
        lines.append(f"  stop: {self.stop}")
        lines.append(f"  target: {self.target}")
        if self.evidence:
            lines.append("  evidence:")
            lines.extend(
                f"    - {item.claim} ({item.source}, {item.observed_at}) {item.url}"
                for item in self.evidence
            )
        return "\n".join(lines)


@dataclass(frozen=True)
class RejectedLine:
    symbol: str
    killed_by: str
    measured: str
    threshold: str

    def as_line(self) -> str:
        return f"  {self.symbol}: {self.killed_by} = {self.measured} (needed {self.threshold})"


@dataclass(frozen=True)
class Briefing:
    """One cycle, rendered. Sections 1-5 of docs/06 §3."""

    cycle_id: UUID
    generated_at_ist: str
    market_headline: str
    market_view: tuple[str, ...] = ()
    ideas: tuple[IdeaCard, ...] = ()
    rejected: tuple[RejectedLine, ...] = ()
    open_theses: tuple[str, ...] = ()
    cost_lines: tuple[str, ...] = ()
    no_ideas_reason: str | None = None
    missing_inputs: tuple[str, ...] = ()

    @property
    def has_ideas(self) -> bool:
        return bool(self.ideas)

    def headline(self) -> str:
        """Message 1: the decision. Nothing below this needs to be read to act."""
        lines = [
            f"BRIEFING {self.generated_at_ist}",
            self.market_headline,
            "",
        ]
        if not self.ideas:
            lines.append("No ideas today.")
            lines.append(f"Why: {self.no_ideas_reason or 'no reason recorded'}")
            return "\n".join(lines)

        for idea in self.ideas:
            lines.append(idea.decision_text())
            lines.append("")
        lines.append(f"{len(self.rejected)} rejected - see the detail message.")
        return "\n".join(lines).rstrip()

    def detail_blocks(self) -> list[str]:
        """Message 2 onward, as blocks the adapter may split between.

        Returned as a list rather than one string so a long briefing breaks
        between sections instead of mid-evidence.
        """
        blocks: list[str] = []
        if self.market_view:
            blocks.append("MARKET VIEW\n" + "\n".join(f"  {line}" for line in self.market_view))

        blocks.extend(idea.detail_text() for idea in self.ideas)

        if self.rejected:
            blocks.append(
                "REJECTED (what nearly qualified, and what killed it)\n"
                + "\n".join(item.as_line() for item in self.rejected)
            )

        blocks.append(
            "OPEN THESES\n" + ("\n".join(f"  {line}" for line in self.open_theses) or "  none open")
        )

        if self.cost_lines:
            blocks.append("COST\n" + "\n".join(f"  {line}" for line in self.cost_lines))

        if self.missing_inputs:
            blocks.append(
                "INPUTS THIS CYCLE DID NOT HAVE\n"
                + "\n".join(f"  - {line}" for line in self.missing_inputs)
            )
        return blocks

    def detail(self) -> str:
        return BLOCK_SEPARATOR.join(self.detail_blocks())
