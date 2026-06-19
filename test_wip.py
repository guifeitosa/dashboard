"""Tests for WIP history reconstruction, limit computation, and diagnostics."""
import datetime

import pandas as pd
import pytest

from core_metrics import build_wip_diagnostics, compute_wip_limit, prepare_df
from status_time import reconstruct_wip_history


# ── Helpers ───────────────────────────────────────────────────────────────────

def _issue(
    key: str,
    status: str,
    created: datetime.datetime,
    team: str = "Alpha",
    issuetype: str = "História",
    resolutiondate: datetime.datetime | None = None,
) -> dict:
    return {
        "key": key,
        "status": status,
        "team": team,
        "issuetype": issuetype,
        "created": created,
        "resolutiondate": resolutiondate,
        "updated": resolutiondate or created,
        "year_month": created.strftime("%Y-%m"),
        "is_resolved": resolutiondate is not None,
    }


def _transition(
    issue_key: str,
    from_status: str,
    to_status: str,
    changed_at: datetime.datetime,
) -> dict:
    return {
        "issue_key": issue_key,
        "from_status": from_status,
        "to_status": to_status,
        "changed_at": changed_at,
    }


def _empty_trans() -> pd.DataFrame:
    return pd.DataFrame(columns=["issue_key", "from_status", "to_status", "changed_at"])


# ── reconstruct_wip_history ───────────────────────────────────────────────────

class TestReconstructWipHistory:

    def test_empty_issues_returns_empty(self):
        result = reconstruct_wip_history(pd.DataFrame(), _empty_trans())
        assert set(result.columns) >= {"date", "status", "count", "team"}
        assert result.empty

    def test_output_always_has_required_columns(self):
        result = reconstruct_wip_history(pd.DataFrame(), pd.DataFrame())
        assert set(result.columns) >= {"date", "status", "count", "team"}

    def test_no_transitions_issue_appears_in_current_status_at_all_dates(self):
        now = datetime.datetime(2026, 6, 18)
        three_weeks_ago = datetime.datetime(2026, 5, 28)

        df_issues = pd.DataFrame([
            _issue("P-1", "Em desenvolvimento", three_weeks_ago)
        ])
        result = reconstruct_wip_history(df_issues, _empty_trans(), today=now)

        assert not result.empty
        assert (result["status"] == "Em desenvolvimento").all()
        assert (result["count"] >= 1).all()

    def test_two_transitions_reconstructed_in_three_periods(self):
        t0 = datetime.datetime(2026, 5, 1)   # created
        t1 = datetime.datetime(2026, 5, 15)  # Backlog → Em desenvolvimento
        t2 = datetime.datetime(2026, 6, 1)   # Em desenvolvimento → Em testes
        now = datetime.datetime(2026, 6, 18)

        df_issues = pd.DataFrame([
            _issue("P-1", "Em testes", t0)
        ])
        df_trans = pd.DataFrame([
            _transition("P-1", "Backlog", "Em desenvolvimento", t1),
            _transition("P-1", "Em desenvolvimento", "Em testes", t2),
        ])

        result = reconstruct_wip_history(df_issues, df_trans, today=now)
        assert not result.empty

        # Week of 2026-05-03 (before t1): should be "Backlog"
        early_snap = pd.Timestamp("2026-05-04")
        early_rows = result[result["date"] <= early_snap]
        if not early_rows.empty:
            assert (early_rows["status"] == "Backlog").all(), (
                f"Expected 'Backlog' before first transition, got: {early_rows['status'].tolist()}"
            )

        # Snapshots after t1 (2026-05-15) but before t2 (2026-06-01): should be "Em desenvolvimento"
        mid_rows = result[
            (result["date"] >= pd.Timestamp("2026-05-15")) & (result["date"] < pd.Timestamp("2026-06-01"))
        ]
        if not mid_rows.empty:
            assert (mid_rows["status"] == "Em desenvolvimento").all(), (
                f"Expected 'Em desenvolvimento' between transitions, got: {mid_rows['status'].tolist()}"
            )

        # Week of 2026-06-08 (after t2): should be "Em testes"
        late_snap = pd.Timestamp("2026-06-08")
        late_rows = result[result["date"] >= late_snap]
        if not late_rows.empty:
            assert (late_rows["status"] == "Em testes").all(), (
                f"Expected 'Em testes' after second transition, got: {late_rows['status'].tolist()}"
            )

    def test_resolved_issue_disappears_after_resolutiondate(self):
        t0 = datetime.datetime(2026, 4, 1)
        resolved_at = datetime.datetime(2026, 5, 1)
        now = datetime.datetime(2026, 6, 18)

        df_issues = pd.DataFrame([
            _issue("P-1", "Em desenvolvimento", t0, resolutiondate=resolved_at)
        ])
        result = reconstruct_wip_history(df_issues, _empty_trans(), today=now)

        after_resolution = result[result["date"] > pd.Timestamp(resolved_at)]
        assert after_resolution.empty or after_resolution["count"].sum() == 0, (
            "Resolved issue should not appear in WIP after resolutiondate"
        )

    def test_team_filter_excludes_other_teams(self):
        t0 = datetime.datetime(2026, 5, 1)
        now = datetime.datetime(2026, 6, 18)

        df_issues = pd.DataFrame([
            _issue("P-1", "Em desenvolvimento", t0, team="Alpha"),
            _issue("P-2", "Em testes",          t0, team="Beta"),
        ])
        result = reconstruct_wip_history(df_issues, _empty_trans(), team="Alpha", today=now)

        assert not result.empty
        assert (result["team"] == "Alpha").all()
        assert "Beta" not in result["team"].values


# ── compute_wip_limit ─────────────────────────────────────────────────────────

class TestComputeWipLimit:

    def test_empty_issues_returns_empty_result(self):
        result = compute_wip_limit(pd.DataFrame(), pd.DataFrame())
        assert result["wip_current"] == {}
        assert result["wip_limit"] == {}
        assert result["over_limit"] == []
        assert result["throughput_avg"] == 0.0

    def test_no_open_issues_returns_empty_wip_current(self):
        now = datetime.datetime(2026, 6, 18)
        one_month_ago = datetime.datetime(2026, 5, 18)
        df_issues = pd.DataFrame([
            _issue("P-1", "Concluído", one_month_ago, resolutiondate=now)
        ])
        df_issues = prepare_df(df_issues)
        result = compute_wip_limit(df_issues, _empty_trans())
        assert result["wip_current"] == {}

    def test_limits_are_minimum_one(self):
        now = datetime.datetime(2026, 6, 18)
        two_months_ago = datetime.datetime(2026, 4, 18)
        one_month_ago = datetime.datetime(2026, 5, 18)

        rows = [_issue(f"P-{i}", "Concluído", two_months_ago, resolutiondate=one_month_ago)
                for i in range(1, 3)]
        rows.append(_issue("P-99", "Em desenvolvimento", two_months_ago))
        df_issues = prepare_df(pd.DataFrame(rows))
        result = compute_wip_limit(df_issues, _empty_trans())

        for status, limit in result["wip_limit"].items():
            assert limit >= 1, f"Limit for '{status}' should be at least 1, got {limit}"

    def test_over_limit_detected_when_wip_exceeds_limit(self):
        now = datetime.datetime(2026, 6, 18)
        two_months_ago = datetime.datetime(2026, 4, 18)
        one_month_ago = datetime.datetime(2026, 5, 18)

        # 1 resolved issue → very low throughput → WIP limit will be 1
        rows = [_issue("P-0", "Concluído", two_months_ago, resolutiondate=one_month_ago)]
        # 50 open issues → definitely over limit
        rows += [_issue(f"P-{i}", "Em desenvolvimento", two_months_ago) for i in range(1, 51)]
        df_issues = prepare_df(pd.DataFrame(rows))

        result = compute_wip_limit(df_issues, _empty_trans())

        assert "Em desenvolvimento" in result["wip_current"]
        assert result["wip_current"]["Em desenvolvimento"] == 50
        assert "Em desenvolvimento" in result["over_limit"]

    def test_result_structure_is_complete(self):
        t0 = datetime.datetime(2026, 4, 1)
        res = datetime.datetime(2026, 5, 1)
        rows = [_issue("P-1", "Concluído", t0, resolutiondate=res),
                _issue("P-2", "Em desenvolvimento", t0)]
        df_issues = prepare_df(pd.DataFrame(rows))

        result = compute_wip_limit(df_issues, _empty_trans())
        for key in ("wip_current", "wip_limit", "over_limit", "throughput_avg", "lead_time_avg_days"):
            assert key in result, f"Missing key: {key}"


# ── build_wip_diagnostics ─────────────────────────────────────────────────────

def _empty_history() -> pd.DataFrame:
    return pd.DataFrame(columns=["date", "status", "count", "team"])


def _wip_data(**kw) -> dict:
    base = {
        "wip_current": {},
        "wip_limit": {},
        "over_limit": [],
        "throughput_avg": 5.0,
        "lead_time_avg_days": 5.0,
    }
    base.update(kw)
    return base


class TestBuildWipDiagnostics:

    def test_rule1_fires_when_status_over_limit(self):
        data = _wip_data(
            wip_current={"Em desenvolvimento": 10},
            wip_limit={"Em desenvolvimento": 3},
            over_limit=["Em desenvolvimento"],
        )
        events = build_wip_diagnostics(data, _empty_history())
        diag = [e for e in events if e.layer in ("insight", "diagnostic")]
        assert len(diag) >= 1
        assert any("Trabalho acumulando" in e.title for e in diag)
        assert any("Em desenvolvimento" in e.title for e in diag)

    def test_rule1_silent_when_within_limit(self):
        data = _wip_data(
            wip_current={"Em desenvolvimento": 3},
            wip_limit={"Em desenvolvimento": 5},
            over_limit=[],
        )
        events = build_wip_diagnostics(data, _empty_history())
        rule1 = [e for e in events if "Trabalho acumulando" in e.title]
        assert len(rule1) == 0

    def test_rule2_fires_when_near_done_empty_but_early_has_items(self):
        data = _wip_data(
            wip_current={"Em desenvolvimento": 5, "Em testes": 3},
            wip_limit={"Em desenvolvimento": 5, "Em testes": 5},
        )
        events = build_wip_diagnostics(data, _empty_history())
        rule2 = [e for e in events if "Nada chegando perto" in e.title]
        assert len(rule2) >= 1

    def test_rule2_silent_when_near_done_has_items(self):
        data = _wip_data(
            wip_current={"Em desenvolvimento": 5, "Revisão de Produto": 2},
            wip_limit={"Em desenvolvimento": 5, "Revisão de Produto": 3},
        )
        events = build_wip_diagnostics(data, _empty_history())
        rule2 = [e for e in events if "Nada chegando perto" in e.title]
        assert len(rule2) == 0

    def test_rule2_silent_when_pronto_pra_producao_has_items(self):
        data = _wip_data(
            wip_current={"Em desenvolvimento": 4, "Pronto pra produção": 1},
            wip_limit={"Em desenvolvimento": 4, "Pronto pra produção": 2},
        )
        events = build_wip_diagnostics(data, _empty_history())
        rule2 = [e for e in events if "Nada chegando perto" in e.title]
        assert len(rule2) == 0

    def test_rule3_fires_when_wip_far_above_historical(self):
        # 5 snapshots: first 4 have 5 items each, last (current) is excluded from history
        dates = [pd.Timestamp(f"2026-05-{d:02d}") for d in [1, 8, 15, 22, 29]]
        wip_history = pd.DataFrame([
            {"date": d, "status": "Em desenvolvimento", "count": 5, "team": "Alpha"}
            for d in dates
        ])
        # Current WIP = 12, well above 5 * 1.5 = 7.5
        data = _wip_data(
            wip_current={"Em desenvolvimento": 12},
            wip_limit={"Em desenvolvimento": 5},
            over_limit=["Em desenvolvimento"],
        )
        events = build_wip_diagnostics(data, wip_history)
        rule3 = [e for e in events if "mais trabalho" in e.title]
        assert len(rule3) >= 1

    def test_rule3_silent_when_wip_within_normal_range(self):
        dates = [pd.Timestamp(f"2026-05-{d:02d}") for d in [1, 8, 15, 22, 29]]
        wip_history = pd.DataFrame([
            {"date": d, "status": "Em desenvolvimento", "count": 10, "team": "Alpha"}
            for d in dates
        ])
        # Current WIP = 10, equal to historical avg (not > 10 * 1.5 = 15)
        data = _wip_data(
            wip_current={"Em desenvolvimento": 10},
            wip_limit={"Em desenvolvimento": 12},
        )
        events = build_wip_diagnostics(data, wip_history)
        rule3 = [e for e in events if "mais trabalho" in e.title]
        assert len(rule3) == 0

    def test_invariant_diag_and_rec_counts_match(self):
        # Rule 1 fires (1 status over limit) + Rule 2 fires (no near-done) = 2 pairs
        data = _wip_data(
            wip_current={"Em desenvolvimento": 10},
            wip_limit={"Em desenvolvimento": 3},
            over_limit=["Em desenvolvimento"],
        )
        events = build_wip_diagnostics(data, _empty_history())
        diag = [e for e in events if e.layer in ("insight", "diagnostic")]
        rec  = [e for e in events if e.layer == "recommendation"]
        assert len(diag) == len(rec), (
            f"Expected equal diag ({len(diag)}) and rec ({len(rec)}) counts"
        )

    def test_all_rules_fire_together(self):
        # Rule 1: over limit, Rule 2: no near-done, Rule 3: above historical
        dates = [pd.Timestamp(f"2026-05-{d:02d}") for d in [1, 8, 15, 22, 29]]
        wip_history = pd.DataFrame([
            {"date": d, "status": "Em desenvolvimento", "count": 3, "team": "Alpha"}
            for d in dates
        ])
        data = _wip_data(
            wip_current={"Em desenvolvimento": 15},
            wip_limit={"Em desenvolvimento": 3},
            over_limit=["Em desenvolvimento"],
        )
        events = build_wip_diagnostics(data, wip_history)
        diag = [e for e in events if e.layer in ("insight", "diagnostic")]
        rec  = [e for e in events if e.layer == "recommendation"]
        # All 3 rules fire: Rule1 (1 pair) + Rule2 (1 pair) + Rule3 (1 pair) = 3 pairs
        assert len(diag) == 3
        assert len(rec) == 3
        assert len(diag) == len(rec)

    def test_no_events_when_everything_is_healthy(self):
        data = _wip_data(
            wip_current={"Revisão de Produto": 2, "Em desenvolvimento": 3},
            wip_limit={"Revisão de Produto": 3, "Em desenvolvimento": 5},
        )
        events = build_wip_diagnostics(data, _empty_history())
        # Rule 1: no over-limit. Rule 2: near-done has items. Rule 3: no history.
        rule1 = [e for e in events if "Trabalho acumulando" in e.title]
        rule2 = [e for e in events if "Nada chegando" in e.title]
        assert len(rule1) == 0
        assert len(rule2) == 0
