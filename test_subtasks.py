"""
Unit tests for subtask support (Etapa 1).

Covers:
  - normalize_issue: parent_key captured when present, None when absent
  - assign_teams_round_robin: subtask inherits parent's team; orphan falls back
"""
import datetime
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

# Set dummy Jira credentials before importing jira_client, which raises
# EnvironmentError at module level when credentials are missing.
os.environ.setdefault("JIRA_BASE_URL", "https://test.atlassian.net")
os.environ.setdefault("JIRA_EMAIL", "test@test.com")
os.environ.setdefault("JIRA_API_TOKEN", "dummy_token")

from jira_client import normalize_issue
from db import Base, IssueRaw, IssueTransition
from sync_and_snapshot import assign_teams_round_robin, _ROUND_ROBIN_TEAMS


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def session():
    """Fresh in-memory SQLite DB per test — never touches metrics.db."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def _minimal_issue(extra_fields: dict | None = None, key: str = "DEMO-001") -> dict:
    """Build the minimal Jira API response dict that normalize_issue expects."""
    fields: dict = {
        "issuetype": {"name": "Subtask"},
        "status": {"name": "Em desenvolvimento"},
        "created": "2026-01-15T10:00:00.000+0000",
        "resolutiondate": None,
        "updated": "2026-01-15T12:00:00.000+0000",
    }
    if extra_fields:
        fields.update(extra_fields)
    return {"key": key, "fields": fields}


# ── normalize_issue: parent_key capture ──────────────────────────────────────

class TestNormalizeIssueParentKey:
    def test_parent_key_captured_when_present(self):
        issue = _minimal_issue({"parent": {"key": "DEMO-100", "fields": {"summary": "Parent"}}})
        result = normalize_issue(issue, {})
        assert result["parent_key"] == "DEMO-100"

    def test_parent_key_is_none_when_field_absent(self):
        issue = _minimal_issue()  # no "parent" field
        result = normalize_issue(issue, {})
        assert result["parent_key"] is None

    def test_parent_key_is_none_when_field_is_null(self):
        issue = _minimal_issue({"parent": None})
        result = normalize_issue(issue, {})
        assert result["parent_key"] is None

    def test_parent_key_is_none_for_regular_historia(self):
        issue = {
            "key": "DEMO-200",
            "fields": {
                "issuetype": {"name": "História"},
                "status": {"name": "Em desenvolvimento"},
                "created": "2026-01-10T10:00:00.000+0000",
                "resolutiondate": None,
                "updated": "2026-01-10T12:00:00.000+0000",
            },
        }
        result = normalize_issue(issue, {})
        assert result["parent_key"] is None

    def test_result_contains_parent_key_field(self):
        """parent_key must always be present in the returned dict."""
        result = normalize_issue(_minimal_issue(), {})
        assert "parent_key" in result

    def test_parent_key_from_nested_key_field_only(self):
        """Only fields.parent.key is used; other parent sub-fields are ignored."""
        issue = _minimal_issue({
            "parent": {"key": "DEMO-999", "id": "12345", "self": "https://..."},
        })
        result = normalize_issue(issue, {})
        assert result["parent_key"] == "DEMO-999"


# ── assign_teams_round_robin: subtask team inheritance ───────────────────────

def _add_issues(session: Session, issues: list[dict]) -> None:
    now = datetime.datetime(2026, 1, 1)
    for d in issues:
        session.add(IssueRaw(
            key=d["key"],
            issuetype=d.get("issuetype", "História"),
            team=d.get("team", "Unknown"),
            parent_key=d.get("parent_key"),
            status=d.get("status", "Em desenvolvimento"),
            created=d.get("created", now),
            synced_at=now,
        ))
    session.commit()


class TestAssignTeamsSubtaskInheritance:
    def test_subtask_inherits_parent_team(self, session):
        _add_issues(session, [
            {"key": "DEMO-0010", "issuetype": "História"},
            {"key": "DEMO-0011", "issuetype": "Subtask", "parent_key": "DEMO-0010"},
        ])

        count = assign_teams_round_robin(session)
        assert count == 2

        parent = session.query(IssueRaw).filter_by(key="DEMO-0010").one()
        subtask = session.query(IssueRaw).filter_by(key="DEMO-0011").one()

        assert parent.team in _ROUND_ROBIN_TEAMS
        assert subtask.team == parent.team

    def test_subtask_with_parent_key_inherits_even_without_subtask_issuetype(self, session):
        """parent_key alone (any issuetype) triggers inheritance."""
        _add_issues(session, [
            {"key": "DEMO-0020", "issuetype": "História"},
            {"key": "DEMO-0021", "issuetype": "História", "parent_key": "DEMO-0020"},
        ])

        assign_teams_round_robin(session)

        parent = session.query(IssueRaw).filter_by(key="DEMO-0020").one()
        child = session.query(IssueRaw).filter_by(key="DEMO-0021").one()
        assert child.team == parent.team

    def test_orphan_subtask_gets_fallback_team(self, session):
        """Subtask whose parent_key doesn't exist in DB still gets a valid team."""
        _add_issues(session, [
            {"key": "DEMO-0030", "issuetype": "Subtask", "parent_key": "DEMO-NONEXISTENT"},
        ])

        count = assign_teams_round_robin(session)
        assert count == 1

        row = session.query(IssueRaw).filter_by(key="DEMO-0030").one()
        assert row.team in _ROUND_ROBIN_TEAMS

    def test_multiple_subtasks_same_parent_same_team(self, session):
        """All subtasks of the same parent get the same team."""
        _add_issues(session, [
            {"key": "DEMO-0040", "issuetype": "História"},
            {"key": "DEMO-0041", "issuetype": "Subtask", "parent_key": "DEMO-0040"},
            {"key": "DEMO-0042", "issuetype": "Subtask", "parent_key": "DEMO-0040"},
            {"key": "DEMO-0043", "issuetype": "Subtask", "parent_key": "DEMO-0040"},
        ])

        assign_teams_round_robin(session)

        parent = session.query(IssueRaw).filter_by(key="DEMO-0040").one()
        for sub_key in ("DEMO-0041", "DEMO-0042", "DEMO-0043"):
            sub = session.query(IssueRaw).filter_by(key=sub_key).one()
            assert sub.team == parent.team

    def test_regular_issues_keep_round_robin_order(self, session):
        """Non-subtask issues continue to get round-robin assignment."""
        _add_issues(session, [
            {"key": "DEMO-0050", "issuetype": "História"},
            {"key": "DEMO-0051", "issuetype": "História"},
            {"key": "DEMO-0052", "issuetype": "História"},
        ])

        assign_teams_round_robin(session)

        teams_assigned = [
            session.query(IssueRaw).filter_by(key=k).one().team
            for k in ("DEMO-0050", "DEMO-0051", "DEMO-0052")
        ]
        for t in teams_assigned:
            assert t in _ROUND_ROBIN_TEAMS
        # Three teams → all three round-robin slots used
        assert set(teams_assigned) == set(_ROUND_ROBIN_TEAMS)

    def test_skipped_when_real_teams_present(self, session):
        """Function returns 0 and leaves data unchanged if real team data exists."""
        _add_issues(session, [
            {"key": "DEMO-0060", "issuetype": "História", "team": "Time Real"},
            {"key": "DEMO-0061", "issuetype": "Subtask", "parent_key": "DEMO-0060"},
        ])

        count = assign_teams_round_robin(session)
        assert count == 0

        row = session.query(IssueRaw).filter_by(key="DEMO-0060").one()
        assert row.team == "Time Real"
