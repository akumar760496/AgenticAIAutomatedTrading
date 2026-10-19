"""The Fundamental agent: a veto source, not a buy reason.

`docs/02` sets the scope and it is narrower than the name suggests:

> "for intraday and short-horizon options trades this agent mostly provides
> *disqualifiers* (pledged shares, auditor resignation, governance flags), not
> buy reasons. Treat it as a veto source."

So it reads one company's recent filings and answers two questions: is there a
governance flag open, and is there a corporate action that breaks the price
series the technical agent is about to measure. Both answers come from
`domain/governance.py`, which is a lookup over the exchange's own subject
vocabulary - no model call, because there is nothing here a model would do
better than a table a human can read and correct.

**A clear report from this agent does not mean the company is clean**, and the
view says so out loud in `unavailable` on every single call. Promoter pledge
and sell-down disclosures - the headline disqualifier in `docs/02` - are filed
under a different NSE endpoint from the one we poll and are simply not in the
archive (ISS-026). Silence about a thing we never looked at must not read like
evidence of its absence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from trading_desk.domain.governance import (
    DEFAULT_LOOKBACK_DAYS,
    CorporateAction,
    FlaggedFiling,
    GovernanceFlag,
    corporate_action_for,
    flag_for,
)
from trading_desk.domain.specialists import SpecialistFinding, SpecialistName
from trading_desk.domain.timezones import to_ist
from trading_desk.ports.news_store import NewsStorePort

# Enough to cover six months for a busy large-cap. RELIANCE filed 96 times in
# the three weeks the archive covers, so a cap of 50 would silently truncate.
MAX_FILINGS_READ = 500

# Said on every call, not only when something is wrong. See the module docstring.
PLEDGE_BLIND_SPOT = (
    "promoter pledge and promoter sell-down: not in the archive at all - filed "
    "under SEBI SAST/PIT on an endpoint we do not poll (ISS-026)"
)


@dataclass(frozen=True)
class DatedAction:
    """A corporate action and when it was announced."""

    action: CorporateAction
    announced_on: date
    subject: str


@dataclass(frozen=True)
class FundamentalView:
    """What the filings say about whether to leave this company alone."""

    finding: SpecialistFinding
    flagged: tuple[FlaggedFiling, ...]
    corporate_actions: tuple[DatedAction, ...]
    filings_read: int

    @property
    def governance_flags(self) -> tuple[str, ...]:
        """In the shape `SetupACandidate.governance_flags` wants: flag names.

        Deduplicated, because three resignations in a month are one flag, and
        a gate counting them would read staff turnover as three problems.
        """
        return tuple(sorted({item.flag.value for item in self.flagged}))

    @property
    def vetoing_flags(self) -> tuple[GovernanceFlag, ...]:
        return tuple(sorted({item.flag for item in self.flagged if item.is_vetoing}))


class FundamentalAgent:
    def __init__(self, store: NewsStorePort, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> None:
        self._store = store
        self._lookback_days = lookback_days

    def assess(self, symbol: str, as_of: date) -> FundamentalView:
        since = as_of - timedelta(days=self._lookback_days)
        filings = self._store.filings_for(symbol, since, MAX_FILINGS_READ)

        flagged: list[FlaggedFiling] = []
        actions: list[DatedAction] = []
        for filing in filings:
            filed_on = to_ist(filing.filed_at).date()
            flag = flag_for(filing.subject)
            if flag is not None:
                flagged.append(
                    FlaggedFiling(
                        flag=flag,
                        subject=filing.subject,
                        filed_on=filed_on,
                        filing_id=filing.filing_id,
                    )
                )
            action = corporate_action_for(filing.subject)
            if action is not None:
                actions.append(
                    DatedAction(action=action, announced_on=filed_on, subject=filing.subject)
                )

        return FundamentalView(
            finding=self._finding(symbol, filings_read=len(filings), flagged=flagged),
            flagged=tuple(flagged),
            corporate_actions=tuple(actions),
            filings_read=len(filings),
        )

    def _finding(
        self, symbol: str, *, filings_read: int, flagged: list[FlaggedFiling]
    ) -> SpecialistFinding:
        vetoes = tuple(item.describe() for item in flagged if item.is_vetoing)
        notes = tuple(item.describe() for item in flagged if not item.is_vetoing)

        measurements = {
            "filings_read": str(filings_read),
            "lookback_days": str(self._lookback_days),
            "governance_flags": str(len({item.flag for item in flagged})),
            "vetoing_flags": str(len(vetoes)),
        }
        unavailable = [PLEDGE_BLIND_SPOT]
        if filings_read == 0:
            # Not the same as "nothing is wrong". A symbol with no filings in
            # the window is one we know nothing about (rule 6).
            unavailable.append(
                f"no filings for {symbol} in the last {self._lookback_days} days - "
                "this is an absence of data, not a clean record"
            )

        return SpecialistFinding(
            specialist=SpecialistName.FUNDAMENTAL,
            symbol=symbol,
            measurements=measurements,
            vetoes=vetoes,
            unavailable=tuple(unavailable),
            notes=notes,
        )
