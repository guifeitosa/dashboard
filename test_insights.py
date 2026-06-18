"""Tests for InsightEngine and InsightEvent."""
import os
os.environ.setdefault("DASHBOARD_DB_PATH", "metrics_demo.db")

import datetime
import pytest
import pandas as pd
from insights import InsightEngine, InsightEvent, SEVERITY_ORDER

# ── Helpers for synthetic data ────────────────────────────────────────────────

def _make_issues(n=20, team="Time Alpha") -> pd.DataFrame:
    """Return minimal issues_raw DataFrame."""
    today = datetime.datetime.now()
    rows = []
    for i in range(n):
        created = today - datetime.timedelta(days=i * 3 + 5)
        resolved = created + datetime.timedelta(days=10) if i % 2 == 0 else None
        rows.append({
            "key": f"T-{i}",
            "issuetype": "Story" if i % 3 != 0 else "Bug",
            "team": team,
            "status": "Feito" if resolved else "Em Revisão",
            "created": created,
            "resolutiondate": resolved,
            "data_implantacao": None,
            "updated": today - datetime.timedelta(days=i),
        })
    return pd.DataFrame(rows)


def _make_transitions(issue_keys, team="Time Alpha") -> pd.DataFrame:
    """Return transitions where issues spend > 40% of their time 'Em Revisão'."""
    today = datetime.datetime.now()
    rows = []
    for key in issue_keys[:10]:  # first 10 issues get rich transition history
        base = today - datetime.timedelta(days=20)
        rows.append({"issue_key": key, "from_status": "A Fazer", "to_status": "Em Progresso", "changed_at": base, "team": team})
        rows.append({"issue_key": key, "from_status": "Em Progresso", "to_status": "Em Revisão", "changed_at": base + datetime.timedelta(days=2), "team": team})
        # spend 12 days in Em Revisão (> 40% of 20-day total)
        rows.append({"issue_key": key, "from_status": "Em Revisão", "to_status": "Feito", "changed_at": base + datetime.timedelta(days=14), "team": team})
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["issue_key", "from_status", "to_status", "changed_at", "team"])


def _make_transitions_balanced(n=20, team="Time Alpha") -> pd.DataFrame:
    """Transitions where no status dominates (all statuses get equal time).

    For each of the n issues in _make_issues(n), produces transitions so that
    A Fazer, Em Progresso, and Em Revisão each consume exactly 1/3 of the
    total non-terminal time, keeping the dominant pct at 33% < 40% threshold.
    """
    today = datetime.datetime.now()
    rows = []
    for i in range(n):
        key = f"T-{i}"
        # issue T-i was created today - (i*3+5) days ago
        created = today - datetime.timedelta(days=i * 3 + 5)
        # First transition at exactly created+3d so A Fazer = 3 days
        # Em Progresso = 3 days, Em Revisão = 3 days → each at 33% < 40%
        base = created + datetime.timedelta(days=3)
        rows.append({"issue_key": key, "from_status": "A Fazer", "to_status": "Em Progresso",
                     "changed_at": base, "team": team})
        rows.append({"issue_key": key, "from_status": "Em Progresso", "to_status": "Em Revisão",
                     "changed_at": base + datetime.timedelta(days=3), "team": team})
        rows.append({"issue_key": key, "from_status": "Em Revisão", "to_status": "Feito",
                     "changed_at": base + datetime.timedelta(days=6), "team": team})
    return pd.DataFrame(rows)


@pytest.fixture
def demo_issues():
    return _make_issues()

@pytest.fixture
def demo_transitions(demo_issues):
    return _make_transitions(demo_issues["key"].tolist())

@pytest.fixture
def balanced_transitions():
    return _make_transitions_balanced()

@pytest.fixture
def empty_transitions():
    return pd.DataFrame(columns=["issue_key", "from_status", "to_status", "changed_at", "team"])

@pytest.fixture
def prev_snapshots():
    period = (datetime.date.today().replace(day=1) - datetime.timedelta(days=1)).strftime("%Y-%m")
    return [
        {"period": period, "team": "Time Alpha", "aging_avg_age": 8.0, "aging_pct_critical": 0.15, "aging_total_open": 10.0},
    ]


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestInsightEngineRun:
    def test_returns_list(self, demo_issues, demo_transitions, prev_snapshots):
        engine = InsightEngine()
        events = engine.run("Time Alpha", "2026-05", demo_issues, demo_transitions, prev_snapshots)
        assert isinstance(events, list)
        assert all(isinstance(e, InsightEvent) for e in events)

    def test_returns_nonempty_with_real_data(self, demo_issues, demo_transitions, prev_snapshots):
        engine = InsightEngine()
        events = engine.run("Time Alpha", "2026-05", demo_issues, demo_transitions, prev_snapshots)
        assert len(events) > 0

    def test_sorted_by_severity(self, demo_issues, demo_transitions, prev_snapshots):
        engine = InsightEngine()
        events = engine.run("Time Alpha", "2026-05", demo_issues, demo_transitions, prev_snapshots)
        ranks = [SEVERITY_ORDER.get(e.severity, 99) for e in events]
        assert ranks == sorted(ranks)

    def test_no_duplicate_ids(self, demo_issues, demo_transitions, prev_snapshots):
        engine = InsightEngine()
        events = engine.run("Time Alpha", "2026-05", demo_issues, demo_transitions, prev_snapshots)
        ids = [e.id for e in events]
        assert len(ids) == len(set(ids)), f"Duplicate IDs found: {[x for x in ids if ids.count(x) > 1]}"


class TestAnalyzeFlow:
    def test_identifies_dominant_status(self, demo_issues, demo_transitions, prev_snapshots):
        engine = InsightEngine()
        events = engine.run("Time Alpha", "2026-05", demo_issues, demo_transitions, prev_snapshots)
        flow_diags = [e for e in events if e.category == "flow" and e.layer == "diagnostic"]
        assert len(flow_diags) >= 1
        assert flow_diags[0].evidence.get("pct_lead_time", 0) > 0.40

    def test_silent_when_no_dominant_status(self, demo_issues, balanced_transitions, prev_snapshots):
        engine = InsightEngine()
        events = engine.run("Time Alpha", "2026-05", demo_issues, balanced_transitions, prev_snapshots)
        flow_diags = [e for e in events if e.category == "flow" and e.layer == "diagnostic"]
        assert len(flow_diags) == 0

    def test_silent_when_no_transitions(self, demo_issues, empty_transitions, prev_snapshots):
        engine = InsightEngine()
        events = engine.run("Time Alpha", "2026-05", demo_issues, empty_transitions, prev_snapshots)
        flow_diags = [e for e in events if e.category == "flow" and e.layer == "diagnostic"]
        assert len(flow_diags) == 0

    def test_links_to_lead_time_insight_when_present(self, demo_issues, demo_transitions, prev_snapshots):
        engine = InsightEngine()
        events = engine.run("Time Alpha", "2026-05", demo_issues, demo_transitions, prev_snapshots)
        flow_diags = [e for e in events if e.category == "flow" and e.layer == "diagnostic"]
        if not flow_diags:
            pytest.skip("Flow diagnostic did not fire with this data")
        lt_insights = [e for e in events if e.category == "lead_time" and e.layer == "insight"]
        if not lt_insights:
            pytest.skip("No lead_time insight to link to")
        # If both exist, the flow diagnostic should reference the LT insight
        lt_ids = {e.id for e in lt_insights}
        assert any(rel in lt_ids for rel in flow_diags[0].related_ids)


class TestRelatedIdsChain:
    def test_recommendation_points_to_existing_event(self, demo_issues, demo_transitions, prev_snapshots):
        engine = InsightEngine()
        events = engine.run("Time Alpha", "2026-05", demo_issues, demo_transitions, prev_snapshots)
        by_id = {e.id: e for e in events}
        for e in events:
            if e.layer == "recommendation":
                for rel_id in e.related_ids:
                    assert rel_id in by_id, f"Recommendation {e.id} points to non-existent {rel_id}"

    def test_diagnostic_points_to_existing_event(self, demo_issues, demo_transitions, prev_snapshots):
        engine = InsightEngine()
        events = engine.run("Time Alpha", "2026-05", demo_issues, demo_transitions, prev_snapshots)
        by_id = {e.id: e for e in events}
        for e in events:
            if e.layer == "diagnostic":
                for rel_id in e.related_ids:
                    assert rel_id in by_id, f"Diagnostic {e.id} points to non-existent {rel_id}"
