"""Routing an exchange filing by its subject line.

**The exchange has already classified these, and better than we can.** NSE
filings carry a `subject` drawn from a controlled vocabulary - "Credit Rating",
"Bagging/Receiving of orders/contracts", "Resignation of Director/KMP/SMP" -
and for a large share of filings that subject *is* the catalyst type. Paying a
model to re-derive it would be slower, dearer and less accurate than a lookup,
and the lookup can be read and corrected by a human.

Measured on the 1,542 filings in the archive on 2026-10-19: 60 distinct
subjects, of which the top five account for over half the volume and are all
routine.

**The three-way answer is the important part.** `route()` returns a catalyst
type, or NONE, or `None` meaning "the subject does not decide - read the
document". Collapsing the last two would be the serious error: "Outcome of
Board Meeting" is not routine, it is *undetermined*, and marking it NONE would
silently discard every earnings announcement in the archive.

The filing bodies we store are one-line stubs ("X has informed the Exchange
about Y", 144 characters on average) because the content lives in a PDF
attachment we do not fetch. So for a subject that needs reading, there is
currently nothing to read - see ISS-025.
"""

from __future__ import annotations

from trading_desk.domain.catalysts import CatalystType

# The subject decides, and it decides a catalyst.
DECIDES_CATALYST: dict[str, CatalystType] = {
    "Resignation of Director/KMP/SMP": CatalystType.MANAGEMENT_EXIT,
    "Resignation": CatalystType.MANAGEMENT_EXIT,
    "Cessation": CatalystType.MANAGEMENT_EXIT,
    "Change in Management": CatalystType.MANAGEMENT_EXIT,
    "Demise": CatalystType.MANAGEMENT_EXIT,
    "Credit Rating": CatalystType.CREDIT_RATING_ACTION,
    "Credit Rating- New": CatalystType.CREDIT_RATING_ACTION,
    "Credit Rating- Others": CatalystType.CREDIT_RATING_ACTION,
    "Acquisition": CatalystType.MERGER_OR_ACQUISITION,
    "Amalgamation/Merger": CatalystType.MERGER_OR_ACQUISITION,
    "Scheme of Arrangement": CatalystType.MERGER_OR_ACQUISITION,
    "Demerger": CatalystType.MERGER_OR_ACQUISITION,
    "Other Restructuring": CatalystType.MERGER_OR_ACQUISITION,
    "Sale or disposal": CatalystType.MERGER_OR_ACQUISITION,
    "Bagging/Receiving of orders/contracts": CatalystType.LARGE_ORDER_WIN,
    "Awarding of order(s)/contract(s)": CatalystType.LARGE_ORDER_WIN,
    "Action(s) taken or orders passed": CatalystType.REGULATORY_DECISION,
    "Action(s) initiated or orders passed": CatalystType.REGULATORY_DECISION,
    "Granting/withdrawal/surrender/cancellation/suspension of key licenses/"
    " regulatory approvals": CatalystType.REGULATORY_DECISION,
    "Corporate Insolvency Resolution Process": CatalystType.REGULATORY_DECISION,
    "Fraud/Default/Arrest": CatalystType.REGULATORY_DECISION,
}

# The subject decides, and it decides "routine". These are compliance
# paperwork: real filings that move nothing.
DECIDES_ROUTINE: frozenset[str] = frozenset(
    {
        "Certificate under SEBI (Depositories and Participants) Regulations, 2018",
        "Copy of Newspaper Publication",
        "Analysts/Institutional Investor Meet/Con. Call Updates",
        "Investor Presentation",
        "Shareholders meeting",
        "ESOP/ESOS/ESPS",
        "Structural Digital Database",
        "Trading Window",
        "Record Date",
        "Monitoring Agency Report",
        "Committee Meeting Updates",
        "Change in Company Secretary/Compliance Officer",
        "Amendment to AOA/MOA",
        "Name Change",
        "Corrigendum",
        "Integrated Filing- Governance",
        "Statement of deviation(s) or variation(s) under Reg. 32",
        # Exchange-initiated queries, not company news. "Spurt in Volume" is
        # the exchange asking the company why the stock moved - the move came
        # first, so treating it as a catalyst inverts cause and effect.
        "Spurt in Volume",
        "Price movement",
        "News Verification",
    }
)


def route(subject: str) -> CatalystType | None:
    """Classify by subject, or return None meaning "read the document".

    None is not a failure and not a NONE. It is the honest answer for
    "Outcome of Board Meeting", which might be an earnings beat, a dividend, a
    fundraise or nothing at all, and the subject cannot tell you which.
    """
    cleaned = " ".join(subject.split())
    if cleaned in DECIDES_CATALYST:
        return DECIDES_CATALYST[cleaned]
    if cleaned in DECIDES_ROUTINE:
        return CatalystType.NONE
    return None


def needs_reading(subject: str) -> bool:
    """True when only the document itself can decide."""
    return route(subject) is None
