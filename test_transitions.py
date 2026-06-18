"""
Tests for the issue_transitions layer.

All tests run against an in-memory SQLite database — the real metrics.db
is never touched. No Jira API calls are made.
"""
from datetime import datetime

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from db import Base, IssueTransition
from jira_client import extract_status_transitions, normalize_issue
from sync_and_snapshot import sync_transitions


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def session():
    """Fresh in-memory SQLite database for each test."""
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(test_engine)
    with Session(test_engine) as s:
        yield s


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_issue(key: str, team: str = "Alpha", histories: list | None = None) -> dict:
    """Build a minimal Jira issue dict with a changelog, as returned by the API."""
    return {
        "key": key,
        "fields": {
            "issuetype": {"name": "Story"},
            "status": {"name": "Done", "statusCategory": {"key": "done"}},
            "created": "2026-06-01T10:00:00.000+0000",
            "resolutiondate": "2026-06-05T12:00:00.000+0000",
            "updated": "2026-06-05T12:00:00.000+0000",
        },
        "changelog": {
            "histories": histories or [],
        },
    }


def _status_history(changed_at: str, from_s: str, to_s: str, extra_items: list | None = None) -> dict:
    """Build one changelog history entry with a status change (and optional extra items)."""
    items = [{"field": "status", "fromString": from_s, "toString": to_s}]
    if extra_items:
        items.extend(extra_items)
    return {"created": changed_at, "items": items}


# ---------------------------------------------------------------------------
# Helper for normalize_issue tests
# ---------------------------------------------------------------------------

def _make_jira_issue(
    status_name: str,
    status_category_key: str,
    resolutiondate: str | None,
    updated: str = "2026-06-10T08:00:00.000+0000",
    issuetype: str = "História",
) -> dict:
    """Minimal Jira API issue payload for normalize_issue tests."""
    return {
        "key": "TD-99",
        "fields": {
            "issuetype": {"name": issuetype},
            "status": {
                "name": status_name,
                "statusCategory": {"key": status_category_key},
            },
            "created": "2026-06-01T10:00:00.000+0000",
            "resolutiondate": resolutiondate,
            "updated": updated,
        },
    }


# ---------------------------------------------------------------------------
# b. normalize_issue — resolutiondate fallback for migrated workflows
# ---------------------------------------------------------------------------

class TestNormalizeIssueResolutionDateFallback:
    """Verify the terminal-status fallback fires regardless of statusCategory.key.

    Root cause: Jira returns statusCategory.key='indeterminate' for statuses
    that were migrated (e.g. 'Feito (migrated)'), so the old check
    `status_category == 'done'` never triggered for any issue in this project.
    The fix uses TERMINAL_STATUSES (name-based) instead.
    """

    def test_feito_migrated_indeterminate_gets_resolutiondate_from_updated(self):
        """'Feito (migrated)' + statusCategory=indeterminate: fallback must fire."""
        issue = _make_jira_issue(
            status_name="Feito (migrated)",
            status_category_key="indeterminate",
            resolutiondate=None,
            updated="2026-06-10T08:00:00.000+0000",
        )
        result = normalize_issue(issue, custom_fields={})
        assert result["resolutiondate"] == "2026-06-10T08:00:00.000+0000"

    def test_concluido_indeterminate_gets_resolutiondate_from_updated(self):
        """'Concluído' + statusCategory=indeterminate: fallback must also fire."""
        issue = _make_jira_issue(
            status_name="Concluído",
            status_category_key="indeterminate",
            resolutiondate=None,
            updated="2026-06-11T09:00:00.000+0000",
        )
        result = normalize_issue(issue, custom_fields={})
        assert result["resolutiondate"] == "2026-06-11T09:00:00.000+0000"

    def test_native_resolutiondate_is_not_overwritten(self):
        """When Jira provides a native resolutiondate the fallback must not replace it."""
        issue = _make_jira_issue(
            status_name="Feito (migrated)",
            status_category_key="indeterminate",
            resolutiondate="2026-06-08T12:00:00.000+0000",
            updated="2026-06-10T08:00:00.000+0000",
        )
        result = normalize_issue(issue, custom_fields={})
        assert result["resolutiondate"] == "2026-06-08T12:00:00.000+0000"

    def test_non_terminal_status_never_triggers_fallback(self):
        """'Em análise' must never get a resolutiondate from the fallback."""
        issue = _make_jira_issue(
            status_name="Em análise",
            status_category_key="indeterminate",
            resolutiondate=None,
        )
        result = normalize_issue(issue, custom_fields={})
        assert result["resolutiondate"] is None

    def test_legacy_done_category_still_works(self):
        """Issues with statusCategory=done AND terminal name must still get the fallback."""
        issue = _make_jira_issue(
            status_name="Done",
            status_category_key="done",
            resolutiondate=None,
            updated="2026-06-12T10:00:00.000+0000",
        )
        result = normalize_issue(issue, custom_fields={})
        assert result["resolutiondate"] == "2026-06-12T10:00:00.000+0000"

    def test_incidente_feito_migrated_gets_resolutiondate(self):
        """Incidente with 'Feito (migrated)' must get resolutiondate — the MTTR root cause."""
        issue = _make_jira_issue(
            status_name="Feito (migrated)",
            status_category_key="indeterminate",
            resolutiondate=None,
            updated="2026-06-16T17:00:00.000+0000",
            issuetype="Incidente",
        )
        result = normalize_issue(issue, custom_fields={})
        assert result["resolutiondate"] == "2026-06-16T17:00:00.000+0000"


# ---------------------------------------------------------------------------
# c. extract_status_transitions — correct extraction from changelog
# ---------------------------------------------------------------------------

def test_extracts_status_transitions_from_changelog():
    """Two history entries → two transition dicts with correct fields."""
    issue = _make_issue("TD-1", team="Alpha", histories=[
        _status_history("2026-06-01T09:00:00.000+0000", "To Do", "In Progress"),
        _status_history("2026-06-05T14:00:00.000+0000", "In Progress", "Done"),
    ])
    result = extract_status_transitions(issue, team="Alpha")

    assert len(result) == 2

    first = result[0]
    assert first["issue_key"] == "TD-1"
    assert first["from_status"] == "To Do"
    assert first["to_status"] == "In Progress"
    assert first["changed_at"] == "2026-06-01T09:00:00.000+0000"
    assert first["team"] == "Alpha"

    second = result[1]
    assert second["from_status"] == "In Progress"
    assert second["to_status"] == "Done"


def test_ignores_non_status_field_changes():
    """Assignee / priority changes in the same history entry must be ignored."""
    issue = _make_issue("TD-2", histories=[
        {
            "created": "2026-06-02T10:00:00.000+0000",
            "items": [
                {"field": "assignee", "fromString": "Alice", "toString": "Bob"},
                {"field": "status",   "fromString": "To Do", "toString": "In Progress"},
                {"field": "priority", "fromString": "Low",   "toString": "High"},
            ],
        }
    ])
    result = extract_status_transitions(issue)

    assert len(result) == 1
    assert result[0]["from_status"] == "To Do"
    assert result[0]["to_status"] == "In Progress"


def test_issue_with_no_status_transitions_returns_empty_list():
    """An issue whose changelog has no status changes must not raise and returns []."""
    issue = _make_issue("TD-3", histories=[
        {
            "created": "2026-06-02T10:00:00.000+0000",
            "items": [{"field": "assignee", "fromString": "Alice", "toString": "Bob"}],
        }
    ])
    assert extract_status_transitions(issue) == []


def test_issue_with_empty_changelog_returns_empty_list():
    """An issue with changelog.histories == [] must return []."""
    issue = _make_issue("TD-4", histories=[])
    assert extract_status_transitions(issue) == []


def test_issue_with_missing_changelog_key_returns_empty_list():
    """An issue dict without a 'changelog' key must return [] without raising."""
    issue = {"key": "TD-5", "fields": {}}
    assert extract_status_transitions(issue) == []


def test_team_is_propagated_to_every_transition():
    """The team parameter must appear in all extracted transitions."""
    issue = _make_issue("TD-6", histories=[
        _status_history("2026-06-01T09:00:00.000+0000", "To Do", "In Progress"),
        _status_history("2026-06-03T11:00:00.000+0000", "In Progress", "Review"),
        _status_history("2026-06-05T15:00:00.000+0000", "Review", "Done"),
    ])
    result = extract_status_transitions(issue, team="Beta")

    assert all(t["team"] == "Beta" for t in result)
    assert len(result) == 3


# ---------------------------------------------------------------------------
# b. sync_transitions — idempotency
# ---------------------------------------------------------------------------

def _sample_transitions() -> list[dict]:
    return [
        {
            "issue_key": "TD-1",
            "from_status": "To Do",
            "to_status": "In Progress",
            "changed_at": "2026-06-01T09:00:00.000+0000",
            "team": "Alpha",
        },
        {
            "issue_key": "TD-1",
            "from_status": "In Progress",
            "to_status": "Done",
            "changed_at": "2026-06-05T14:00:00.000+0000",
            "team": "Alpha",
        },
        {
            "issue_key": "TD-2",
            "from_status": "To Do",
            "to_status": "Done",
            "changed_at": "2026-06-03T10:00:00.000+0000",
            "team": "Beta",
        },
    ]


def test_sync_transitions_inserts_correct_count(session):
    """First sync must write exactly N rows."""
    transitions = _sample_transitions()
    count = sync_transitions(session, transitions)
    session.commit()

    assert count == 3
    assert session.query(IssueTransition).count() == 3


def test_sync_transitions_is_idempotent(session):
    """Running sync twice with the same data must not duplicate rows."""
    transitions = _sample_transitions()

    sync_transitions(session, transitions)
    session.commit()
    first_count = session.query(IssueTransition).count()

    sync_transitions(session, transitions)
    session.commit()
    second_count = session.query(IssueTransition).count()

    assert first_count == 3
    assert second_count == 3, "Second sync must not duplicate rows"


def test_sync_transitions_stores_correct_fields(session):
    """Spot-check that field values survive the round-trip through the DB."""
    sync_transitions(session, _sample_transitions())
    session.commit()

    row = (
        session.query(IssueTransition)
        .filter_by(issue_key="TD-1", from_status="To Do")
        .one()
    )
    assert row.to_status == "In Progress"
    assert row.team == "Alpha"
    assert isinstance(row.changed_at, datetime)


def test_sync_overwrites_previous_data_on_rerun(session):
    """A second sync with fewer transitions replaces, not appends, the table."""
    sync_transitions(session, _sample_transitions())  # 3 rows
    session.commit()

    # Simulate a re-sync where one issue was deleted / has fewer transitions
    fewer = [_sample_transitions()[0]]  # only 1 transition
    sync_transitions(session, fewer)
    session.commit()

    assert session.query(IssueTransition).count() == 1


# ---------------------------------------------------------------------------
# c. edge cases that must not break the sync
# ---------------------------------------------------------------------------

def test_sync_with_empty_transitions_list_clears_table(session):
    """Syncing an empty list must delete existing rows without raising."""
    sync_transitions(session, _sample_transitions())
    session.commit()

    sync_transitions(session, [])
    session.commit()

    assert session.query(IssueTransition).count() == 0


def test_sync_skips_entries_with_missing_changed_at(session):
    """Transitions without changed_at must be silently dropped."""
    bad_transitions = [
        {"issue_key": "TD-9", "from_status": "To Do", "to_status": "Done",
         "changed_at": None, "team": "Alpha"},
        {"issue_key": "TD-10", "from_status": "To Do", "to_status": "Done",
         "changed_at": "2026-06-01T09:00:00.000+0000", "team": "Alpha"},
    ]
    count = sync_transitions(session, bad_transitions)
    session.commit()

    # Only the valid one should be written
    assert count == 1
    assert session.query(IssueTransition).count() == 1


def test_issue_with_no_transitions_does_not_break_sync(session):
    """Issues that produce zero transitions (no status changes) must not cause errors."""
    issue_no_transitions = _make_issue("TD-11", histories=[])
    transitions = extract_status_transitions(issue_no_transitions)
    assert transitions == []

    # Syncing an empty list from a no-transition issue must succeed
    count = sync_transitions(session, transitions)
    session.commit()
    assert count == 0
    assert session.query(IssueTransition).count() == 0
