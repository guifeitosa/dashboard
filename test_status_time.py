"""
Unit tests for status_time.py.

All transitions are forged manually — no DB access, no Jira API calls.
"""

from datetime import datetime, timedelta

import pytest

import pandas as pd

from status_time import (
    average_time_in_status,
    calculate_lead_and_cycle_time,
    lead_time_real,
    time_in_status,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

T0 = datetime(2026, 6, 1, 9, 0, 0)


def dt(hours: float) -> datetime:
    return T0 + timedelta(hours=hours)


def tr(changed_at: datetime, from_status: str, to_status: str) -> dict:
    return {"from_status": from_status, "to_status": to_status, "changed_at": changed_at}


def issue(
    key: str,
    transitions: list[dict],
    *,
    created: datetime = T0,
    resolutiondate: datetime | None = None,
    team: str = "Time Alfa",
    issuetype: str = "História",
) -> dict:
    return {
        "issue_key": key,
        "created": created,
        "resolutiondate": resolutiondate,
        "team": team,
        "issuetype": issuetype,
        "transitions": transitions,
    }


# ===========================================================================
# time_in_status
# ===========================================================================

class TestTimeInStatus:

    def test_three_transitions_computes_correct_durations(self):
        """Standard 3-transition flow: To Do → Doing → Review → Done."""
        transitions = [
            tr(dt(3), "To Do", "Doing"),
            tr(dt(8), "Doing", "Review"),
            tr(dt(10), "Review", "Done"),
        ]
        result = time_in_status("TD-1", T0, transitions, now=dt(11))

        assert result["To Do"]  == timedelta(hours=3)
        assert result["Doing"]  == timedelta(hours=5)
        assert result["Review"] == timedelta(hours=2)
        assert result["Done"]   == timedelta(hours=1)

    def test_no_transitions_uses_initial_status(self):
        """Issue with no changelog uses initial_status from created to now."""
        result = time_in_status("TD-2", T0, [], now=dt(4), initial_status="To Do")

        assert result == {"To Do": timedelta(hours=4)}

    def test_no_transitions_without_initial_status_falls_back_to_unknown(self):
        """When there are no transitions and no initial_status, label is 'Unknown'."""
        result = time_in_status("TD-2", T0, [], now=dt(4))

        assert "Unknown" in result
        assert result["Unknown"] == timedelta(hours=4)

    def test_open_issue_includes_time_in_current_status_until_now(self):
        """Issue still in progress: last status duration runs to `now`."""
        transitions = [tr(dt(2), "To Do", "Doing")]
        result = time_in_status("TD-4", T0, transitions, now=dt(8))

        assert result["To Do"]  == timedelta(hours=2)
        assert result["Doing"]  == timedelta(hours=6)

    def test_status_visited_twice_accumulates_durations(self):
        """Rework: issue returns to a previous status — time should accumulate."""
        transitions = [
            tr(dt(2), "To Do",  "Doing"),
            tr(dt(4), "Doing",  "To Do"),   # returned
            tr(dt(7), "To Do",  "Done"),
        ]
        result = time_in_status("TD-5", T0, transitions, now=dt(7))

        assert result["To Do"] == timedelta(hours=5)  # 2h + 3h
        assert result["Doing"] == timedelta(hours=2)

    def test_transitions_out_of_order_are_sorted_before_calculation(self):
        """Transitions passed in reverse order must produce the same result."""
        transitions_correct = [
            tr(dt(3), "To Do",  "Doing"),
            tr(dt(8), "Doing",  "Done"),
        ]
        transitions_reversed = list(reversed(transitions_correct))

        result_a = time_in_status("TD-6", T0, transitions_correct,  now=dt(10))
        result_b = time_in_status("TD-6", T0, transitions_reversed, now=dt(10))

        assert result_a == result_b

    def test_negative_duration_clamped_to_zero(self):
        """
        If a transition timestamp precedes created (data quality issue), the
        initial-status period is clamped to zero.
        The subsequent period (changed_at → now) is still counted normally:
        dt(-1) → dt(2) = 3h, not 2h.
        """
        transitions = [tr(dt(-1), "To Do", "Done")]  # changed_at before created
        result = time_in_status("TD-7", T0, transitions, now=dt(2))

        assert result.get("To Do", timedelta(0)) == timedelta(0)
        assert result["Done"] == timedelta(hours=3)  # dt(2) - dt(-1) = 3h


# ===========================================================================
# lead_time_real
# ===========================================================================

class TestLeadTimeReal:

    def test_standard_lead_time_start_to_end(self):
        """Measures the gap between entering start_status and entering end_status."""
        transitions = [
            tr(dt(1), "To Do",   "Fazendo"),
            tr(dt(6), "Fazendo", "Feito"),
        ]
        result = lead_time_real("TD-1", transitions, start_status="Fazendo", end_status="Feito")

        assert result == timedelta(hours=5)

    def test_never_entered_start_status_returns_none(self):
        """
        Fallback: issue jumped straight to end_status without going through
        start_status. Returns None — the lead time is unmeasurable.
        """
        transitions = [tr(dt(1), "To Do", "Feito")]
        result = lead_time_real("TD-3", transitions, start_status="Fazendo", end_status="Feito")

        assert result is None

    def test_entered_start_but_never_reached_end_returns_none(self):
        """Issue is still in start_status (or moved elsewhere) — lead time is open."""
        transitions = [tr(dt(1), "To Do", "Fazendo")]
        result = lead_time_real("TD-6", transitions, start_status="Fazendo", end_status="Feito")

        assert result is None

    def test_no_transitions_returns_none(self):
        """Issue with an empty changelog has no measurable lead time."""
        result = lead_time_real("TD-8", [], start_status="Fazendo", end_status="Feito")

        assert result is None

    def test_uses_first_entry_into_start_status(self):
        """When start_status is entered twice, the clock starts on the first entry."""
        transitions = [
            tr(dt(1), "To Do",   "Fazendo"),   # first entry ← start clock here
            tr(dt(3), "Fazendo", "Review"),
            tr(dt(5), "Review",  "Fazendo"),   # second entry — must be ignored
            tr(dt(8), "Fazendo", "Feito"),
        ]
        result = lead_time_real("TD-7", transitions, start_status="Fazendo", end_status="Feito")

        assert result == timedelta(hours=7)  # dt(8) - dt(1)

    def test_parametrised_statuses_work_with_english_names(self):
        """start_status and end_status are not locked to Portuguese names."""
        transitions = [
            tr(dt(2), "Backlog",    "In Progress"),
            tr(dt(9), "In Progress", "Done"),
        ]
        result = lead_time_real("TD-9", transitions, start_status="In Progress", end_status="Done")

        assert result == timedelta(hours=7)


# ===========================================================================
# average_time_in_status
# ===========================================================================

class TestAverageTimeInStatus:

    def test_single_issue(self):
        """Average of one issue equals its own per-status durations."""
        issues = [
            issue("TD-1", [
                tr(dt(3), "To Do",  "Doing"),
                tr(dt(7), "Doing",  "Done"),
            ]),
        ]
        result = average_time_in_status(issues, now=dt(9))

        assert result["To Do"] == timedelta(hours=3)
        assert result["Doing"] == timedelta(hours=4)
        assert result["Done"]  == timedelta(hours=2)

    def test_two_issues_averages_correctly(self):
        """Average across two issues with different durations in the same statuses."""
        issues = [
            issue("TD-1", [tr(dt(2), "To Do", "Done")]),   # To Do=2h, Done=6h
            issue("TD-2", [tr(dt(6), "To Do", "Done")]),   # To Do=6h, Done=2h
        ]
        result = average_time_in_status(issues, now=dt(8))

        assert result["To Do"] == timedelta(hours=4)
        assert result["Done"]  == timedelta(hours=4)

    def test_filter_by_team(self):
        """Only issues matching the team filter contribute to the average."""
        issues = [
            issue("TD-1", [tr(dt(2), "To Do", "Done")], team="Time Alfa"),
            issue("TD-2", [tr(dt(10), "To Do", "Done")], team="Time Beta"),
        ]
        result = average_time_in_status(issues, now=dt(12), team="Time Alfa")

        # Only TD-1: To Do=2h, Done=10h
        assert result["To Do"] == timedelta(hours=2)
        assert result["Done"]  == timedelta(hours=10)

    def test_filter_by_issuetype(self):
        """Only issues matching the issuetype filter contribute to the average."""
        issues = [
            issue("TD-1", [tr(dt(4), "To Do", "Done")], issuetype="História"),
            issue("TD-2", [tr(dt(2), "To Do", "Done")], issuetype="Incidente"),
        ]
        result = average_time_in_status(issues, now=dt(6), issuetype="Incidente")

        # Only TD-2: To Do=2h, Done=4h
        assert result["To Do"] == timedelta(hours=2)

    def test_uses_resolutiondate_for_resolved_issues(self):
        """
        Resolved issues stop accumulating time at resolutiondate, not at `now`.
        This prevents an issue closed months ago from inflating its final status.
        """
        issues = [
            issue("TD-1",
                  [tr(dt(3), "To Do", "Done")],
                  resolutiondate=dt(5)),
        ]
        result = average_time_in_status(issues, now=dt(100))

        assert result["To Do"] == timedelta(hours=3)
        assert result["Done"]  == timedelta(hours=2)  # dt(5) - dt(3), not 97h

    def test_no_matching_issues_returns_empty_dict(self):
        """Filter that matches nothing returns {} without raising."""
        issues = [issue("TD-1", [], team="Time Alfa")]
        result = average_time_in_status(issues, now=dt(4), team="Time Gama")

        assert result == {}

    def test_status_visited_by_only_some_issues_averages_over_visitors(self):
        """
        A status not visited by all issues is averaged over those that did visit it,
        not over the total number of issues.
        """
        issues = [
            issue("TD-1", [
                tr(dt(2), "To Do", "Review"),
                tr(dt(5), "Review", "Done"),
            ]),
            issue("TD-2", [
                tr(dt(4), "To Do", "Done"),  # skipped Review entirely
            ]),
        ]
        result = average_time_in_status(issues, now=dt(6))

        # Review: only TD-1 visited it (3h) → average = 3h (not 1.5h)
        assert result["Review"] == timedelta(hours=3)


# ===========================================================================
# calculate_lead_and_cycle_time
# ===========================================================================

# Base date for these tests (a Monday so bdate arithmetic is predictable)
_BASE = datetime(2026, 6, 1, 0, 0, 0)  # Monday


def _d(days: float) -> datetime:
    return _BASE + timedelta(days=days)


def _issue_df(**kwargs) -> pd.DataFrame:
    defaults = {
        "key": "TD-1",
        "issuetype": "História",
        "team": "Alpha",
        "created": _BASE,
        "resolutiondate": _d(10),
        "is_resolved": True,
        "year_month": "2026-06",
        "status": "Concluído",
    }
    defaults.update(kwargs)
    return pd.DataFrame([defaults])


def _tr_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _tr(issue_key, frm, to, days_offset) -> dict:
    return {
        "issue_key": issue_key,
        "from_status": frm,
        "to_status": to,
        "changed_at": _d(days_offset),
    }


class TestCalculateLeadAndCycleTime:

    def test_lead_time_normal_via_transitions(self):
        """Em desenvolvimento → Concluído via transitions — lead time is that window."""
        df_i = _issue_df(created=_d(0), resolutiondate=_d(10))
        df_t = _tr_df([
            _tr("TD-1", "Sprint Backlog", "Em desenvolvimento", 2),
            _tr("TD-1", "Em desenvolvimento", "Revisão de Produto", 7),
            _tr("TD-1", "Revisão de Produto", "Concluído", 10),
        ])
        result = calculate_lead_and_cycle_time(df_i, df_t)
        assert len(result) == 1
        row = result.iloc[0]
        # Lead: Em desenvolvimento (day 2) → Concluído (day 10)
        assert row["lead_time_days"] > 0
        assert pd.notna(row["lead_time_days"])

    def test_lead_time_fallback_created(self):
        """No Em desenvolvimento transition → fallback_start=created → lead time from created."""
        df_i = _issue_df(created=_d(0), resolutiondate=_d(6))
        df_t = _tr_df([
            _tr("TD-1", "Backlog", "Sprint Backlog", 0.5),   # no Em desenvolvimento
            _tr("TD-1", "Sprint Backlog", "Concluído", 6),
        ])
        result = calculate_lead_and_cycle_time(df_i, df_t)
        assert len(result) == 1
        assert result.iloc[0]["lead_time_days"] > 0

    def test_lead_time_none_when_issue_still_open(self):
        """Open issue (no resolutiondate) → excluded from result."""
        df_i = _issue_df(resolutiondate=None, is_resolved=False)
        df_t = _tr_df([_tr("TD-1", "Backlog", "Sprint Backlog", 0.5)])
        result = calculate_lead_and_cycle_time(df_i, df_t)
        assert result.empty

    def test_cycle_time_normal_dev_to_revisao(self):
        """Em desenvolvimento → Revisão de Produto is the cycle time window."""
        df_i = _issue_df(created=_d(0), resolutiondate=_d(10))
        df_t = _tr_df([
            _tr("TD-1", "Backlog", "Sprint Backlog", 0.5),
            _tr("TD-1", "Sprint Backlog", "Em desenvolvimento", 2),
            _tr("TD-1", "Em desenvolvimento", "Revisão de Produto", 7),
            _tr("TD-1", "Revisão de Produto", "Concluído", 10),
        ])
        result = calculate_lead_and_cycle_time(df_i, df_t)
        # Cycle: Em desenvolvimento (day 2) → Revisão de Produto (day 7)
        assert pd.notna(result.iloc[0]["cycle_time_days"])
        assert result.iloc[0]["cycle_time_days"] > 0

    def test_cycle_time_fallback_concluido(self):
        """No Revisão de Produto transition → cycle time uses fallback Concluído."""
        df_i = _issue_df(created=_d(0), resolutiondate=_d(8))
        df_t = _tr_df([
            _tr("TD-1", "Backlog", "Sprint Backlog", 0.5),
            _tr("TD-1", "Sprint Backlog", "Em desenvolvimento", 2),
            _tr("TD-1", "Em desenvolvimento", "Concluído", 8),
        ])
        result = calculate_lead_and_cycle_time(df_i, df_t)
        assert len(result) == 1
        assert pd.notna(result.iloc[0]["cycle_time_days"])

    def test_cycle_time_nan_when_no_em_desenvolvimento(self):
        """Issue that never entered Em desenvolvimento → cycle_time_days is NaN."""
        df_i = _issue_df(created=_d(0), resolutiondate=_d(5))
        df_t = _tr_df([
            _tr("TD-1", "Backlog", "Sprint Backlog", 0.5),
            _tr("TD-1", "Sprint Backlog", "Concluído", 5),
        ])
        result = calculate_lead_and_cycle_time(df_i, df_t)
        assert len(result) == 1
        assert pd.isna(result.iloc[0]["cycle_time_days"])

    def test_gmud_returns_empty(self):
        """GMUD has lead_time: null → excluded entirely."""
        df_i = _issue_df(issuetype="GMUD")
        df_t = _tr_df([_tr("TD-1", "Sprint Backlog", "Implantado com Sucesso", 5)])
        result = calculate_lead_and_cycle_time(df_i, df_t)
        assert result.empty

    def test_incidente_returns_empty(self):
        """Incidente has lead_time: null → excluded."""
        df_i = _issue_df(issuetype="Incidente")
        df_t = _tr_df([_tr("TD-1", "Sprint Backlog", "Concluído", 3)])
        result = calculate_lead_and_cycle_time(df_i, df_t)
        assert result.empty

    def test_subtask_dev_returns_empty(self):
        """DEV subtask has lead_time: null → excluded."""
        df_i = _issue_df(issuetype="DEV")
        df_t = _tr_df([_tr("TD-1", "Em desenvolvimento", "Concluído", 2)])
        result = calculate_lead_and_cycle_time(df_i, df_t)
        assert result.empty

    def test_team_filter(self):
        """team= filter includes only matching rows."""
        df_i = pd.DataFrame([
            {**_issue_df(key="TD-1", team="Alpha").iloc[0].to_dict(), "key": "TD-1"},
            {**_issue_df(key="TD-2", team="Beta").iloc[0].to_dict(), "key": "TD-2"},
        ])
        df_t = _tr_df([
            _tr("TD-1", "Backlog", "Sprint Backlog", 0.5),
            _tr("TD-1", "Sprint Backlog", "Concluído", 10),
            _tr("TD-2", "Backlog", "Sprint Backlog", 0.5),
            _tr("TD-2", "Sprint Backlog", "Concluído", 10),
        ])
        result = calculate_lead_and_cycle_time(df_i, df_t, team="Alpha")
        assert len(result) == 1
        assert result.iloc[0]["team"] == "Alpha"

    def test_business_days_excludes_weekends(self):
        """Lead time spanning a weekend must count only business days."""
        # _BASE is Monday 2026-06-01
        # Em desenvolvimento on Tuesday (day 1), Concluído on next Monday (day 7)
        # Business days: Tue, Wed, Thu, Fri, Mon = 5 business days (≤ 7)
        monday_start = _BASE
        next_monday = _BASE + timedelta(days=7)
        df_i = _issue_df(created=monday_start, resolutiondate=next_monday)
        df_t = _tr_df([
            _tr("TD-1", "Sprint Backlog", "Em desenvolvimento", 1),  # Tuesday
            _tr("TD-1", "Em desenvolvimento", "Concluído", 7),        # next Monday
        ])
        result = calculate_lead_and_cycle_time(df_i, df_t)
        assert len(result) == 1
        # Tue → Mon: spans weekend (Sat+Sun excluded) → 5 business days
        assert result.iloc[0]["lead_time_days"] <= 7
