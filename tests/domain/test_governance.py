"""The governance veto table.

The tests worth having here are not "does the dict contain this key". They are
the two design decisions: a management exit must **not** veto, because it is
also a tradable catalyst, and an ambiguous subject must veto, because a
disqualifier we cannot read is still a disqualifier.
"""

from __future__ import annotations

from datetime import date

from trading_desk.domain.catalysts import TRADABLE_CATALYSTS, CatalystType
from trading_desk.domain.filing_subjects import route
from trading_desk.domain.governance import (
    VETOING_FLAGS,
    CorporateAction,
    FlaggedFiling,
    GovernanceFlag,
    corporate_action_for,
    flag_for,
    is_within_lookback,
)

AS_OF = date(2026, 10, 19)


class TestFlagsFromSubjects:
    def test_a_regulatory_order_is_flagged(self) -> None:
        assert flag_for("Action(s) taken or orders passed") is GovernanceFlag.REGULATORY_ACTION

    def test_most_filings_raise_nothing(self) -> None:
        assert flag_for("Investor Presentation") is None
        assert flag_for("Copy of Newspaper Publication") is None

    def test_whitespace_in_the_subject_does_not_hide_a_flag(self) -> None:
        """NSE's subject strings arrive with inconsistent internal spacing."""
        assert flag_for("  Fraud/Default/Arrest  ") is GovernanceFlag.FRAUD_OR_DEFAULT

    def test_an_ambiguous_licence_subject_still_vetoes(self) -> None:
        """One subject covers granting *and* withdrawal. We cannot tell which
        without the document, and the document is a stub (ISS-025), so it
        vetoes rather than passes (CLAUDE.md rule 6)."""
        flag = flag_for(
            "Granting/withdrawal/surrender/cancellation/suspension of key licenses/"
            " regulatory approvals"
        )

        assert flag is GovernanceFlag.LICENCE_CHANGE
        assert flag in VETOING_FLAGS


class TestManagementExitIsReportedNotVetoed:
    def test_a_management_exit_is_flagged(self) -> None:
        assert flag_for("Resignation of Director/KMP/SMP") is GovernanceFlag.MANAGEMENT_EXIT

    def test_but_it_does_not_veto(self) -> None:
        assert GovernanceFlag.MANAGEMENT_EXIT not in VETOING_FLAGS

    def test_because_the_same_filing_is_a_tradable_catalyst(self) -> None:
        """The contradiction this prevents: if a management exit both triggered
        Setup A and vetoed the company, the setup could never fire on it, and
        nothing anywhere would report that an entire catalyst type was dead."""
        subject = "Resignation of Director/KMP/SMP"

        assert route(subject) is CatalystType.MANAGEMENT_EXIT
        assert CatalystType.MANAGEMENT_EXIT in TRADABLE_CATALYSTS
        assert flag_for(subject) not in VETOING_FLAGS

    def test_every_other_flag_does_veto(self) -> None:
        others = {flag for flag in GovernanceFlag if flag is not GovernanceFlag.MANAGEMENT_EXIT}
        assert others == set(VETOING_FLAGS)


class TestCorporateActions:
    def test_a_bonus_is_recognised(self) -> None:
        assert corporate_action_for("Bonus") is CorporateAction.BONUS

    def test_a_record_date_is_recognised(self) -> None:
        assert corporate_action_for("Record Date") is CorporateAction.RECORD_DATE

    def test_a_governance_flag_is_not_a_corporate_action(self) -> None:
        assert corporate_action_for("Fraud/Default/Arrest") is None


class TestLookback:
    def test_a_filing_inside_the_window_counts(self) -> None:
        assert is_within_lookback(date(2026, 9, 1), AS_OF, 180)

    def test_a_filing_outside_it_does_not(self) -> None:
        assert not is_within_lookback(date(2025, 1, 1), AS_OF, 180)

    def test_a_filing_dated_in_the_future_never_counts(self) -> None:
        """Lookahead, in the one place it could arrive quietly: a filing stamped
        tomorrow would otherwise pass a `>= as_of - window` test."""
        assert not is_within_lookback(date(2026, 11, 1), AS_OF, 180)

    def test_the_boundary_day_is_inside(self) -> None:
        assert is_within_lookback(date(2026, 4, 22), AS_OF, 180)


def test_a_flagged_filing_describes_itself_with_its_date_and_subject() -> None:
    """Rule 9: a veto that says only "governance" is unactionable."""
    flagged = FlaggedFiling(
        flag=GovernanceFlag.INSOLVENCY,
        subject="Corporate Insolvency Resolution Process",
        filed_on=date(2026, 10, 1),
        filing_id="NSE-1",
    )

    assert flagged.is_vetoing
    assert "INSOLVENCY" in flagged.describe()
    assert "2026-10-01" in flagged.describe()
