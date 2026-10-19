"""Governance flags and corporate actions, read off the filing subject line.

Same method as `domain/filing_subjects.py`, and for the same reason: NSE's
subject field is a controlled vocabulary, so a lookup beats a model. This one
answers a different question. `filing_subjects` asks "is this a catalyst worth
trading?"; this asks "is there a reason not to touch this company at all?"

`docs/02` is blunt about the Fundamental agent's job: for short-horizon options
trades it "mostly provides *disqualifiers* ... not buy reasons. Treat it as a
veto source." So this module is a veto table and a corporate-action table, and
nothing else.

**Two measured gaps, both of which matter more than the table does.** Against
the 1,542 filings held on 2026-10-19:

1. **Promoter pledge and promoter sell-down are not in the archive at all.**
   `docs/02` names pledged shares as the headline disqualifier. Those arrive
   under SEBI's SAST and PIT disclosure regimes, which NSE publishes on a
   different endpoint from the one step 1.5 polls. We cannot flag what we never
   fetched, so this agent is blind to its own headline case (ISS-026).
2. **Auditor resignation has no subject of its own.** It arrives as plain
   "Resignation" (15 filings), indistinguishable from a sales head leaving
   until someone reads the document - and the document is a 144-character stub
   (ISS-025). So `MANAGEMENT_EXIT` means "somebody left", no more.

Neither gap is fixable here. Both are written down so that a clear report from
this agent is not read as "the company is fine".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

# How far back a flag still counts. Governance problems do not expire on a
# fixed schedule, so this is a judgement, not a measurement: six months is long
# enough to catch a regulatory order from last quarter and short enough that a
# settled 2019 dispute does not veto every idea forever. Needs sign-off.
DEFAULT_LOOKBACK_DAYS = 180


class GovernanceFlag(StrEnum):
    """A reason to leave a company alone, named by what was filed."""

    REGULATORY_ACTION = "REGULATORY_ACTION"
    INSOLVENCY = "INSOLVENCY"
    FRAUD_OR_DEFAULT = "FRAUD_OR_DEFAULT"
    LITIGATION = "LITIGATION"
    TRADING_SUSPENDED = "TRADING_SUSPENDED"
    # The subject covers granting *and* withdrawal of a licence, so it cannot
    # be told apart without the document. Treated as a veto: a disqualifier we
    # cannot read is still a disqualifier (CLAUDE.md rule 6).
    LICENCE_CHANGE = "LICENCE_CHANGE"
    # Reported, never a veto on its own. See VETOING_FLAGS below.
    MANAGEMENT_EXIT = "MANAGEMENT_EXIT"


FLAG_BY_SUBJECT: dict[str, GovernanceFlag] = {
    "Action(s) taken or orders passed": GovernanceFlag.REGULATORY_ACTION,
    "Action(s) initiated or orders passed": GovernanceFlag.REGULATORY_ACTION,
    "Corporate Insolvency Resolution Process": GovernanceFlag.INSOLVENCY,
    "Fraud/Default/Arrest": GovernanceFlag.FRAUD_OR_DEFAULT,
    "Delay/default in the payment of fines/penalties/dues etc. to authority": (
        GovernanceFlag.FRAUD_OR_DEFAULT
    ),
    "Pendency of Litigation(s)/dispute(s) or the outcome impacting the Company": (
        GovernanceFlag.LITIGATION
    ),
    "Suspension of Trading": GovernanceFlag.TRADING_SUSPENDED,
    "Granting/withdrawal/surrender/cancellation/suspension of key licenses/"
    " regulatory approvals": GovernanceFlag.LICENCE_CHANGE,
    "Resignation of Director/KMP/SMP": GovernanceFlag.MANAGEMENT_EXIT,
    "Resignation": GovernanceFlag.MANAGEMENT_EXIT,
    "Cessation": GovernanceFlag.MANAGEMENT_EXIT,
    "Change in Management": GovernanceFlag.MANAGEMENT_EXIT,
    "Change in Company Secretary/Compliance Officer": GovernanceFlag.MANAGEMENT_EXIT,
}

# Everything except MANAGEMENT_EXIT, and that exclusion is deliberate.
# `domain/catalysts.py` lists MANAGEMENT_EXIT as a *tradable catalyst*: a CEO
# leaving is one of the seven events Setup A exists to trade. If it also vetoed
# the company, the setup could never fire on it, and the contradiction would be
# invisible - the idea would simply never appear. It is reported as a note so a
# reader sees it; whether it kills an idea is a gate's decision, not this
# table's.
VETOING_FLAGS: frozenset[GovernanceFlag] = frozenset(
    flag for flag in GovernanceFlag if flag is not GovernanceFlag.MANAGEMENT_EXIT
)


class CorporateAction(StrEnum):
    """Events that silently break a price series and an options position.

    `docs/02` singles these out, and the reason is practical rather than
    analytical: a 1:1 bonus halves every historical close, so ATR, range and
    the realised-vol proxy are all wrong across the gap unless someone knows.
    """

    DIVIDEND = "DIVIDEND"
    BONUS = "BONUS"
    BUYBACK = "BUYBACK"
    RESTRUCTURING = "RESTRUCTURING"
    ISSUE_OF_SECURITIES = "ISSUE_OF_SECURITIES"
    RECORD_DATE = "RECORD_DATE"
    NAME_CHANGE = "NAME_CHANGE"


ACTION_BY_SUBJECT: dict[str, CorporateAction] = {
    "Dividend": CorporateAction.DIVIDEND,
    "Bonus": CorporateAction.BONUS,
    "Closure of Buy Back": CorporateAction.BUYBACK,
    "Public Announcement - Buyback of Shares": CorporateAction.BUYBACK,
    "Scheme of Arrangement": CorporateAction.RESTRUCTURING,
    "Amalgamation/Merger": CorporateAction.RESTRUCTURING,
    "Demerger": CorporateAction.RESTRUCTURING,
    "Other Restructuring": CorporateAction.RESTRUCTURING,
    "Issue of Securities": CorporateAction.ISSUE_OF_SECURITIES,
    "Allotment of Securities": CorporateAction.ISSUE_OF_SECURITIES,
    "Record Date": CorporateAction.RECORD_DATE,
    "Name Change": CorporateAction.NAME_CHANGE,
    "Name and Symbol Change": CorporateAction.NAME_CHANGE,
}

# A stock split is the textbook price-series break and there is **no split in
# the archive**, so no subject string for one has been observed and none is
# guessed here. NSE's vocabulary for it would have to be read off a real split
# filing before it could be added. Until then a split arrives unflagged, which
# is a known hole rather than an oversight.
UNOBSERVED_ACTIONS = ("stock split",)


@dataclass(frozen=True)
class FlaggedFiling:
    """One filing that said something about governance, kept with its date."""

    flag: GovernanceFlag
    subject: str
    filed_on: date
    filing_id: str

    @property
    def is_vetoing(self) -> bool:
        return self.flag in VETOING_FLAGS

    def describe(self) -> str:
        return f"{self.flag.value} on {self.filed_on.isoformat()} ({self.subject})"


def _normalise(subject: str) -> str:
    return " ".join(subject.split())


def flag_for(subject: str) -> GovernanceFlag | None:
    """The governance flag this subject raises, or None for most filings."""
    return FLAG_BY_SUBJECT.get(_normalise(subject))


def corporate_action_for(subject: str) -> CorporateAction | None:
    return ACTION_BY_SUBJECT.get(_normalise(subject))


def is_within_lookback(filed_on: date, as_of: date, lookback_days: int) -> bool:
    """True when a filing is recent enough to still count.

    Takes `as_of` rather than reading a clock: domain code is pure (rule 3).
    """
    if filed_on > as_of:
        return False
    return filed_on >= as_of - timedelta(days=lookback_days)
