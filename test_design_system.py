"""Tests for the design system components and InsightEvent model changes."""
import datetime

import pandas as pd
import pytest

from components.period_selector import compute_comparison_period
from insights import InsightEvent


# ── InsightEvent model ────────────────────────────────────────────────────────

class TestInsightEventWhyItMatters:

    def test_why_it_matters_defaults_to_empty_list(self):
        event = InsightEvent(
            id="test_1",
            severity="high",
            category="aging",
            layer="insight",
            title="Test",
            description="Desc",
        )
        assert event.why_it_matters == []

    def test_why_it_matters_is_independent_between_instances(self):
        e1 = InsightEvent(id="a", severity="high", category="x", layer="insight", title="A", description="")
        e2 = InsightEvent(id="b", severity="high", category="x", layer="insight", title="B", description="")
        e1.why_it_matters.append("bullet")
        assert e2.why_it_matters == [], "Mutable default should not be shared between instances"

    def test_why_it_matters_can_be_set(self):
        event = InsightEvent(id="x", severity="high", category="wip", layer="insight", title="T", description="D")
        event.why_it_matters = ["bullet 1", "bullet 2"]
        assert len(event.why_it_matters) == 2


# ── why_it_matters bullet counts per rule ────────────────────────────────────

class TestWhyItMattersBulletCounts:

    def _make_aging_df_with_near_done(self) -> pd.DataFrame:
        today = datetime.date.today()
        five_days_ago = pd.Timestamp(today - datetime.timedelta(days=5))
        return pd.DataFrame([
            {
                "key": "H-1",
                "status": "Revisão de Produto",
                "issuetype": "História",
                "team": "Alpha",
                "created": pd.Timestamp("2026-01-01"),
                "resolutiondate": None,
                "updated": five_days_ago,
                "year_month": "2026-01",
                "is_resolved": False,
            }
        ])

    def test_aging_rule_c_near_done_has_2_to_3_bullets(self):
        from core_metrics import build_aging_diagnostics
        df = self._make_aging_df_with_near_done()
        events = build_aging_diagnostics(df, team=None, issuetype=None)
        rule_c = [e for e in events if "quase prontas" in e.title and e.layer == "insight"]
        assert rule_c, "Rule C should fire for near-done items stuck > 3 days"
        bullets = rule_c[0].why_it_matters
        assert 2 <= len(bullets) <= 3, f"Expected 2-3 bullets, got {len(bullets)}: {bullets}"

    def test_wip_rule_1_over_limit_has_2_to_3_bullets(self):
        from core_metrics import build_wip_diagnostics
        wip_data = {
            "wip_current": {"Em desenvolvimento": 10},
            "wip_limit":   {"Em desenvolvimento": 3},
            "over_limit":  ["Em desenvolvimento"],
            "throughput_avg": 5.0,
            "lead_time_avg_days": 5.0,
        }
        empty_hist = pd.DataFrame(columns=["date", "status", "count", "team"])
        events = build_wip_diagnostics(wip_data, empty_hist)
        rule_1 = [e for e in events if "Trabalho acumulando" in e.title]
        assert rule_1, "WIP Rule 1 should fire"
        bullets = rule_1[0].why_it_matters
        assert 2 <= len(bullets) <= 3, f"Expected 2-3 bullets, got {len(bullets)}: {bullets}"

    def test_throughput_queda_has_2_to_3_bullets(self):
        from core_metrics import build_throughput_diagnostics, prepare_df
        today = datetime.date(2026, 6, 18)
        # Two closed months where current < prev
        closed_list = [
            {"month": "2026-04", "count": 10, "label": "Abr/2026"},
            {"month": "2026-05", "count": 5,  "label": "Mai/2026"},
        ]
        # Populate df with > 30% items older than 30 days to trigger aging condition
        thirty_days_ago = pd.Timestamp(today - datetime.timedelta(days=35))
        rows = [
            {
                "key": f"P-{i}", "status": "Em desenvolvimento",
                "issuetype": "História", "team": "Alpha",
                "created": thirty_days_ago, "resolutiondate": None,
                "updated": thirty_days_ago, "year_month": "2026-04",
                "is_resolved": False,
            }
            for i in range(5)
        ]
        df = prepare_df(pd.DataFrame(rows))
        events = build_throughput_diagnostics(closed_list, df, team=None, pred={"label": "Alta"}, today=today)
        queda = [e for e in events if "caíram" in e.title and e.layer == "insight"]
        assert queda, "Throughput queda insight should fire"
        bullets = queda[0].why_it_matters
        assert 2 <= len(bullets) <= 3, f"Expected 2-3 bullets, got {len(bullets)}: {bullets}"

    def test_cfr_worsened_has_2_to_3_bullets(self):
        from core_metrics import build_dora_diagnostics
        current = {"lead_time_days": None, "deploy_freq_interval": None,
                   "mttr_hours": None, "cfr_percent": 45.0}
        prev    = {"lead_time_days": None, "deploy_freq_interval": None,
                   "mttr_hours": None, "cfr_percent": 10.0}
        events = build_dora_diagnostics(current, prev)
        cfr = [e for e in events if e.category == "cfr" and e.layer == "insight" and "causando" in e.title]
        assert cfr, "CFR worsened insight should fire"
        bullets = cfr[0].why_it_matters
        assert 2 <= len(bullets) <= 3, f"Expected 2-3 bullets, got {len(bullets)}: {bullets}"

    def test_aging_rule_b_code_review_has_2_to_3_bullets(self):
        from core_metrics import build_aging_diagnostics
        today = datetime.date.today()
        eight_days_ago = pd.Timestamp(today - datetime.timedelta(days=8))
        df = pd.DataFrame([
            {
                "key": "S-1", "status": "Code Review",
                "issuetype": "Subtask", "team": "Alpha",
                "parent_key": "H-1",
                "created": pd.Timestamp("2026-01-01"),
                "resolutiondate": None,
                "updated": eight_days_ago,
                "year_month": "2026-01",
                "is_resolved": False,
            }
        ])
        events = build_aging_diagnostics(df, team=None, issuetype=None)
        rule_b = [e for e in events if "revisão de código" in e.title and e.layer == "insight"]
        assert rule_b, "Rule B should fire for stuck Code Review items"
        bullets = rule_b[0].why_it_matters
        assert 2 <= len(bullets) <= 3, f"Expected 2-3 bullets, got {len(bullets)}: {bullets}"


# ── compute_comparison_period ─────────────────────────────────────────────────

class TestComputeComparisonPeriod:

    def test_returns_tuple_with_two_strings(self):
        result = compute_comparison_period("2026-06", "Mês anterior")
        assert isinstance(result, tuple) and len(result) == 2
        assert all(isinstance(s, str) for s in result)

    def test_mes_anterior(self):
        period, label = compute_comparison_period("2026-06", "Mês anterior")
        assert period == "2026-05"
        assert "MAI" in label or "mai" in label.lower() or "05" in label

    def test_quarter_anterior(self):
        period, label = compute_comparison_period("2026-06", "Quarter anterior")
        assert period == "2026-03"
        assert "Q1" in label

    def test_semestre_anterior(self):
        period, label = compute_comparison_period("2026-06", "Semestre anterior")
        assert period == "2025-12"
        assert "2025" in label

    def test_mesmo_mes_ano_anterior(self):
        period, label = compute_comparison_period("2026-06", "Mesmo mês do ano anterior")
        assert period == "2025-06"
        assert "2025" in label

    def test_january_wraparound(self):
        period, label = compute_comparison_period("2026-01", "Mês anterior")
        assert period == "2025-12"

    def test_quarter_label_format(self):
        period, label = compute_comparison_period("2026-09", "Quarter anterior")
        assert period == "2026-06"
        assert "Q2" in label

    def test_dict_keys_present(self):
        """Simulate what render_period_selector returns (without Streamlit)."""
        base = datetime.date.today().strftime("%Y-%m")
        comparison, label = compute_comparison_period(base, "Quarter anterior")
        result = {"period": base, "comparison": comparison, "comparison_label": label}
        assert set(result.keys()) == {"period", "comparison", "comparison_label"}
        assert result["period"] == base
        assert len(result["comparison"]) == 7  # YYYY-MM
