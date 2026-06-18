"""
test_core_metrics.py — Unit tests for core_metrics.py

All tests use forged DataFrames only (no CSV files, no DB connections, no Jira API).
"""

from __future__ import annotations

import datetime
import math

import numpy as np
import pandas as pd
import pytest

from core_metrics import (
    AGING_OVER_REP_THRESHOLD,
    _score_lower_better,
    compute_aging,
    compute_predictability,
    compute_throughput,
    compute_throughput_health,
    compute_trend,
    diagnose_throughput_drop,
    dora_band,
    health_status_label,
    metric_status,
    prepare_df,
    score_aging,
    score_cfr,
    score_lead_time,
    score_mttr,
    score_throughput,
    squad_health_score,
    worst_dora_band,
)

# ── Reference date used for all age calculations ─────────────────────────────
_REF_DATE = datetime.datetime(2026, 6, 1)


# ── Test helpers ──────────────────────────────────────────────────────────────

def _d(days_ago: int) -> datetime.datetime:
    """Return a timezone-naive datetime N days before the fixed reference date 2026-06-01."""
    return _REF_DATE - datetime.timedelta(days=days_ago)


def _issues(rows: list[dict]) -> pd.DataFrame:
    """Build an issues_df with sensible defaults per row.

    Defaults:
      issuetype       = "História"
      team            = "Time Alfa"
      status          = "Feito"
      data_implantacao = None  (→ NaT)
      updated         = None  (→ NaT)
      resolutiondate  = None  (→ NaT)
    """
    defaults = {
        "issuetype": "História",
        "team": "Time Alfa",
        "status": "Feito",
        "data_implantacao": None,
        "updated": None,
        "resolutiondate": None,
    }
    records = []
    for i, row in enumerate(rows):
        r = {**defaults, **row}
        r.setdefault("key", f"PROJ-{i + 1:04d}")
        records.append(r)
    df = pd.DataFrame(records)
    # Coerce date columns
    for col in ("created", "resolutiondate", "updated", "data_implantacao"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


# ═══════════════════════════════════════════════════════════════════════════════
# TestPrepareDF
# ═══════════════════════════════════════════════════════════════════════════════

class TestPrepareDF:
    def test_adds_year_month_from_created(self):
        df = _issues([{"created": _d(10)}])
        result = prepare_df(df)
        assert "year_month" in result.columns
        assert result["year_month"].iloc[0] == "2026-05"

    def test_adds_is_resolved_from_resolutiondate(self):
        df = _issues([
            {"created": _d(20), "resolutiondate": _d(5)},
            {"created": _d(30), "resolutiondate": None},
        ])
        result = prepare_df(df)
        assert "is_resolved" in result.columns
        assert result["is_resolved"].iloc[0] is True or result["is_resolved"].iloc[0] == True
        assert result["is_resolved"].iloc[1] is False or result["is_resolved"].iloc[1] == False

    def test_idempotent(self):
        df = _issues([{"created": _d(10), "resolutiondate": _d(2)}])
        once = prepare_df(df)
        twice = prepare_df(once)
        pd.testing.assert_frame_equal(once.reset_index(drop=True), twice.reset_index(drop=True))


# ═══════════════════════════════════════════════════════════════════════════════
# TestDoraBand
# ═══════════════════════════════════════════════════════════════════════════════

class TestDoraBand:
    # lead_time_days boundaries
    def test_lead_time_half_day_is_elite(self):
        assert dora_band("lead_time_days", 0.5) == "Elite"

    def test_lead_time_exactly_1_is_high(self):
        # boundary: Elite is STRICTLY < 1 day
        assert dora_band("lead_time_days", 1.0) == "High"

    def test_lead_time_5d_is_high(self):
        assert dora_band("lead_time_days", 5.0) == "High"

    def test_lead_time_7d_is_high(self):
        assert dora_band("lead_time_days", 7.0) == "High"

    def test_lead_time_7pt1_is_medium(self):
        assert dora_band("lead_time_days", 7.1) == "Medium"

    def test_lead_time_30d_is_medium(self):
        assert dora_band("lead_time_days", 30.0) == "Medium"

    def test_lead_time_30pt1_is_low(self):
        assert dora_band("lead_time_days", 30.1) == "Low"

    # mttr_hours
    def test_mttr_half_hour_is_elite(self):
        assert dora_band("mttr_hours", 0.5) == "Elite"

    def test_mttr_exactly_1h_is_high(self):
        # Elite is STRICTLY < 1h
        assert dora_band("mttr_hours", 1.0) == "High"

    def test_mttr_167h_is_medium(self):
        assert dora_band("mttr_hours", 167.0) == "Medium"

    def test_mttr_168h_is_low(self):
        # boundary: ≥ 168h → Low (not strictly greater)
        assert dora_band("mttr_hours", 168.0) == "Low"

    # cfr_percent
    def test_cfr_0_is_elite(self):
        assert dora_band("cfr_percent", 0.0) == "Elite"

    def test_cfr_15_is_elite_inclusive(self):
        # boundary: Elite is ≤ 15% (inclusive)
        assert dora_band("cfr_percent", 15.0) == "Elite"

    def test_cfr_15pt1_is_high(self):
        assert dora_band("cfr_percent", 15.1) == "High"

    def test_cfr_45_is_medium(self):
        assert dora_band("cfr_percent", 45.0) == "Medium"

    def test_cfr_45pt1_is_low(self):
        assert dora_band("cfr_percent", 45.1) == "Low"

    # Edge cases
    def test_none_value_is_na(self):
        assert dora_band("lead_time_days", None) == "N/A"

    def test_nan_value_is_na(self):
        assert dora_band("lead_time_days", float("nan")) == "N/A"

    def test_unknown_key_is_na(self):
        assert dora_band("not_a_real_key", 5.0) == "N/A"


# ═══════════════════════════════════════════════════════════════════════════════
# TestComputeTrend
# ═══════════════════════════════════════════════════════════════════════════════

class TestComputeTrend:
    def test_last_3_above_avg_is_crescimento(self):
        counts = [5.0, 4.0, 6.0, 7.0, 8.0]
        avg = sum(counts) / len(counts)  # 6.0
        # last 3: 6, 7, 8 — all > 6.0? No. Use a clear case:
        counts = [3.0, 3.0, 7.0, 8.0, 9.0]
        avg = 6.0
        result = compute_trend(counts, avg)
        assert result["label"] == "Crescimento"

    def test_last_2_below_avg_is_queda(self):
        counts = [10.0, 9.0, 8.0, 3.0, 4.0]
        avg = sum(counts) / len(counts)  # 6.8
        result = compute_trend(counts, avg)
        assert result["label"] == "Queda"

    def test_mixed_pattern_is_estavel(self):
        counts = [5.0, 8.0, 4.0, 7.0, 5.0]
        avg = sum(counts) / len(counts)  # 5.8
        result = compute_trend(counts, avg)
        assert result["label"] == "Estável"

    def test_single_data_point_is_estavel(self):
        # Can't satisfy 2-month Queda nor 3-month Crescimento criteria
        result = compute_trend([5.0], 5.0)
        assert result["label"] == "Estável"


# ═══════════════════════════════════════════════════════════════════════════════
# TestComputeThroughputHealth
# ═══════════════════════════════════════════════════════════════════════════════

class TestComputeThroughputHealth:
    def test_last_40pct_of_avg_is_critica(self):
        # ratio = 0.40 < 0.50 → Crítica
        result = compute_throughput_health("Estável", last_count=4.0, avg_val=10.0, cv=0.10)
        assert result["label"] == "Crítica"

    def test_queda_and_last_65pct_is_critica(self):
        # ratio = 0.65 < 0.70 while trend=Queda → Crítica
        result = compute_throughput_health("Queda", last_count=6.5, avg_val=10.0, cv=0.20)
        assert result["label"] == "Crítica"

    def test_crescimento_and_low_cv_is_boa(self):
        result = compute_throughput_health("Crescimento", last_count=12.0, avg_val=10.0, cv=0.10)
        assert result["label"] == "Boa"

    def test_queda_and_high_cv_is_atencao(self):
        # ratio = 0.80 ≥ 0.70 (not Crítica), trend=Queda → not Boa → Atenção
        result = compute_throughput_health("Queda", last_count=8.0, avg_val=10.0, cv=0.50)
        assert result["label"] == "Atenção"


# ═══════════════════════════════════════════════════════════════════════════════
# TestComputePredictability
# ═══════════════════════════════════════════════════════════════════════════════

class TestComputePredictability:
    def test_cv_10pct_is_alta(self):
        assert compute_predictability(0.10)["label"] == "Alta"

    def test_cv_22pct_is_media(self):
        assert compute_predictability(0.22)["label"] == "Média"

    def test_cv_45pct_is_baixa(self):
        assert compute_predictability(0.45)["label"] == "Baixa"


# ═══════════════════════════════════════════════════════════════════════════════
# TestComputeThroughput
# ═══════════════════════════════════════════════════════════════════════════════

class TestComputeThroughput:
    def _make_resolved_df(self, month_counts: dict[str, int]) -> pd.DataFrame:
        """Build issues_df with `resolutiondate` distributed across months."""
        rows = []
        for ym, count in month_counts.items():
            year, month = int(ym[:4]), int(ym[5:7])
            for day in range(1, count + 1):
                rows.append({
                    "created": datetime.datetime(year, month, 1),
                    "resolutiondate": datetime.datetime(year, month, min(day, 28)),
                    "issuetype": "História",
                    "team": "Time Alfa",
                    "status": "Feito",
                })
        return pd.DataFrame(rows)

    def test_6_months_last_is_wip(self):
        df = self._make_resolved_df({
            "2025-12": 5, "2026-01": 6, "2026-02": 7,
            "2026-03": 8, "2026-04": 9, "2026-05": 3,
        })
        result = compute_throughput(df)
        assert result["wip"] is not None
        assert result["wip"]["month"] == "2026-05"
        # avg computed over 5 closed months
        assert result["n_months"] == 5

    def test_best_worst_identification(self):
        df = self._make_resolved_df({
            "2026-01": 3, "2026-02": 10, "2026-03": 5,
            "2026-04": 8, "2026-05": 2,
        })
        result = compute_throughput(df)
        # 4 closed months: Jan=3, Feb=10, Mar=5, Apr=8 (May is WIP)
        assert result["best"]["count"] == 10
        assert result["worst"]["count"] == 3

    def test_wip_excluded_from_avg(self):
        df = self._make_resolved_df({
            "2026-01": 10, "2026-02": 10,
            "2026-03": 10, "2026-04": 1,  # WIP — should not affect avg
        })
        result = compute_throughput(df)
        # closed: Jan, Feb, Mar → avg = 10.0
        assert result["avg"] == pytest.approx(10.0)

    def test_empty_df_returns_empty_dict(self):
        result = compute_throughput(pd.DataFrame())
        assert result == {}

    def test_single_month_treated_as_closed(self):
        df = self._make_resolved_df({"2026-05": 7})
        result = compute_throughput(df)
        assert result["wip"] is None
        assert result["n_months"] == 1
        assert result["avg"] == pytest.approx(7.0)

    def test_wip_excluded_from_trend_and_health(self):
        """trend and health must be computed on closed months only, ignoring the WIP month."""
        # Closed months Dec–Apr: counts [3,3,7,8,9], avg=6 → last3=[7,8,9] all>6 → Crescimento
        # WIP (May): 20 items.  Without exclusion: avg≈8.3, last3=[8,9,20], 8<8.3 → Estável.
        df = self._make_resolved_df({
            "2025-12": 3, "2026-01": 3, "2026-02": 7,
            "2026-03": 8, "2026-04": 9, "2026-05": 20,
        })
        result = compute_throughput(df)
        assert result["wip"]["month"] == "2026-05"
        assert result["trend"]["label"] == "Crescimento"


# ═══════════════════════════════════════════════════════════════════════════════
# TestComputeAging
# ═══════════════════════════════════════════════════════════════════════════════

class TestComputeAging:
    _TODAY = datetime.date(2026, 6, 1)

    def test_correct_band_distribution(self):
        df = _issues([
            {"created": _d(3),  "resolutiondate": None},   # 0–7d
            {"created": _d(10), "resolutiondate": None},   # 7–14d
            {"created": _d(20), "resolutiondate": None},   # 14–30d
            {"created": _d(45), "resolutiondate": None},   # 30–60d
            {"created": _d(70), "resolutiondate": None},   # 60+d
        ])
        result = compute_aging(df, today=self._TODAY)
        assert result["bands"]["0–7d"]   == 1
        assert result["bands"]["7–14d"]  == 1
        assert result["bands"]["14–30d"] == 1
        assert result["bands"]["30–60d"] == 1
        assert result["bands"]["60+d"]   == 1

    def test_avg_age_computed_correctly(self):
        df = _issues([
            {"created": _d(10), "resolutiondate": None},
            {"created": _d(20), "resolutiondate": None},
            {"created": _d(30), "resolutiondate": None},
        ])
        result = compute_aging(df, today=self._TODAY)
        assert result["avg_age"] == pytest.approx(20.0)

    def test_team_filter_applies(self):
        df = _issues([
            {"created": _d(5),  "team": "Time Alfa", "resolutiondate": None},
            {"created": _d(10), "team": "Time Beta", "resolutiondate": None},
            {"created": _d(15), "team": "Time Alfa", "resolutiondate": None},
        ])
        result = compute_aging(df, today=self._TODAY, team="Time Alfa")
        assert result["total_open"] == 2

    def test_over_representation_diagnosis_flagged(self):
        """A type with 100% of red issues but only 30% of total → flagged."""
        # 10 open issues: 3 of type "Bug" (all > 30d), 7 of type "História" (< 30d)
        rows = (
            [{"created": _d(40), "issuetype": "Bug",     "resolutiondate": None}] * 3
            + [{"created": _d(5),  "issuetype": "História", "resolutiondate": None}] * 7
        )
        df = _issues(rows)
        result = compute_aging(df, today=self._TODAY)
        # Bug: pct_red = 100%, pct_total = 30%, over_rep = 70pp ≥ 15pp
        diag_vals = [d["val"] for d in result["diagnosis"]]
        assert "Bug" in diag_vals

    def test_no_over_representation_when_uniform(self):
        """When all types are equally represented in the red band, nothing is flagged."""
        rows = (
            [{"created": _d(40), "issuetype": "Bug",     "resolutiondate": None}] * 3
            + [{"created": _d(40), "issuetype": "História", "resolutiondate": None}] * 3
        )
        df = _issues(rows)
        result = compute_aging(df, today=self._TODAY)
        # Both 50% of red and 50% of total → over_rep = 0 < 15
        assert result["diagnosis"] == []

    def test_sem_movimento_uses_updated_not_created(self):
        """sem_movimento must count issues with `updated` > 14 days ago, not `created`."""
        df = _issues([
            # Old by creation (60d) but recently updated (5d) → must NOT be counted
            {"created": _d(60), "updated": _d(5),  "resolutiondate": None},
            # Recent creation (5d) but stale update (20d) → must BE counted
            {"created": _d(5),  "updated": _d(20), "resolutiondate": None},
        ])
        result = compute_aging(df, today=self._TODAY)
        assert result["sem_movimento"] == 1

    def test_over_rep_below_15pp_not_flagged(self):
        """over_rep strictly below 15pp must not produce a diagnosis entry."""
        # 10 total: 2 Bug (1 > 30d), 8 História (3 > 30d)
        # Bug: pct_red=1/4=25%, pct_total=2/10=20%, over_rep=5pp < 15pp → NOT flagged
        rows = (
            [{"created": _d(40), "issuetype": "Bug",     "resolutiondate": None}] * 1
            + [{"created": _d(40), "issuetype": "História", "resolutiondate": None}] * 3
            + [{"created": _d(5),  "issuetype": "Bug",     "resolutiondate": None}] * 1
            + [{"created": _d(5),  "issuetype": "História", "resolutiondate": None}] * 5
        )
        df = _issues(rows)
        result = compute_aging(df, today=self._TODAY)
        assert all(d["val"] != "Bug" for d in result["diagnosis"])


# ═══════════════════════════════════════════════════════════════════════════════
# TestDiagnoseThroughputDrop
# ═══════════════════════════════════════════════════════════════════════════════

class TestDiagnoseThroughputDrop:
    def _empty_df(self) -> pd.DataFrame:
        return pd.DataFrame(columns=["created", "resolutiondate", "issuetype"])

    def test_fewer_than_3_months_returns_empty(self):
        result = diagnose_throughput_drop(
            ["2026-04", "2026-05"],
            {"2026-04": 10.0, "2026-05": 5.0},
            self._empty_df(),
        )
        assert result == []

    def test_no_drop_returns_empty(self):
        # tp_last (10) >= tp_mean (8) → delta_tp ≤ 0 → no drop
        result = diagnose_throughput_drop(
            ["2026-03", "2026-04", "2026-05"],
            {"2026-03": 8.0, "2026-04": 6.0, "2026-05": 10.0},
            self._empty_df(),
        )
        assert result == []

    def test_no_issue_signal_returns_variacao_normal(self):
        # Drop exists (mean=10, last=5) but no bugs or incidents to explain it
        result = diagnose_throughput_drop(
            ["2026-03", "2026-04", "2026-05"],
            {"2026-03": 10.0, "2026-04": 10.0, "2026-05": 5.0},
            self._empty_df(),
        )
        assert len(result) == 1
        assert result[0]["label"] == "Variação normal"
        assert result[0]["pct"] == pytest.approx(100.0)

    def test_bug_spike_in_last_month_appears_in_diagnosis(self):
        """A bug spike in the last month must be identified as a contributing factor."""
        months = ["2026-03", "2026-04", "2026-05"]
        tp_by_month = {"2026-03": 10.0, "2026-04": 10.0, "2026-05": 4.0}
        # 1 bug/month in Mar and Apr, then spike to 5 bugs in May
        rows = (
            [{"created": datetime.datetime(2026, 3, 15), "issuetype": "Bug", "resolutiondate": None}]
            + [{"created": datetime.datetime(2026, 4, 15), "issuetype": "Bug", "resolutiondate": None}]
            + [{"created": datetime.datetime(2026, 5, d), "issuetype": "Bug", "resolutiondate": None}
               for d in range(1, 6)]
        )
        df = pd.DataFrame(rows)
        result = diagnose_throughput_drop(months, tp_by_month, df)
        labels = [d["label"] for d in result]
        assert "Bugs" in labels


# ═══════════════════════════════════════════════════════════════════════════════
# TestScoreLowerBetter
# ═══════════════════════════════════════════════════════════════════════════════

class TestScoreLowerBetter:
    # Using lead_time thresholds: Elite <1, High ≤7, Medium ≤30
    _KWARGS = dict(elite_hi=1.0, high_hi=7.0, medium_hi=30.0)

    def test_elite_range(self):
        score = _score_lower_better(0.5, **self._KWARGS)
        assert 90.0 <= score <= 100.0

    def test_high_range(self):
        score = _score_lower_better(4.0, **self._KWARGS)
        assert 70.0 <= score <= 89.0

    def test_medium_range(self):
        score = _score_lower_better(15.0, **self._KWARGS)
        assert 50.0 <= score <= 69.0

    def test_low_range(self):
        score = _score_lower_better(60.0, **self._KWARGS)
        assert 0.0 <= score <= 49.0


# ═══════════════════════════════════════════════════════════════════════════════
# TestScoreLeadTime
# ═══════════════════════════════════════════════════════════════════════════════

class TestScoreLeadTime:
    def test_elite_boundary(self):
        # 0.5d → should be in Elite range (90–100)
        assert 90.0 <= score_lead_time(0.5) <= 100.0

    def test_high_boundary(self):
        # 3d → High (70–89)
        assert 70.0 <= score_lead_time(3.0) <= 89.0

    def test_low_boundary(self):
        # 60d → Low (0–49)
        assert 0.0 <= score_lead_time(60.0) <= 49.0


# ═══════════════════════════════════════════════════════════════════════════════
# TestScoreMttr
# ═══════════════════════════════════════════════════════════════════════════════

class TestScoreMttr:
    def test_elite_boundary(self):
        assert 90.0 <= score_mttr(0.5) <= 100.0

    def test_high_boundary(self):
        # 12h → High (70–89)
        assert 70.0 <= score_mttr(12.0) <= 89.0

    def test_low_boundary(self):
        # 500h → Low (0–49)
        assert 0.0 <= score_mttr(500.0) <= 49.0


# ═══════════════════════════════════════════════════════════════════════════════
# TestScoreCfr
# ═══════════════════════════════════════════════════════════════════════════════

class TestScoreCfr:
    def test_elite_boundary_inclusive(self):
        # 15% is inclusive Elite
        assert 90.0 <= score_cfr(15.0) <= 100.0

    def test_high_boundary(self):
        # 22% → High
        assert 70.0 <= score_cfr(22.0) <= 89.0

    def test_low_boundary(self):
        # 60% → Low
        assert 0.0 <= score_cfr(60.0) <= 49.0


# ═══════════════════════════════════════════════════════════════════════════════
# TestScoreAging
# ═══════════════════════════════════════════════════════════════════════════════

class TestScoreAging:
    def _open_df(self, dias_list: list[float]) -> pd.DataFrame:
        return pd.DataFrame({"dias_parado": dias_list})

    def test_all_issues_under_7d_scores_100(self):
        df = self._open_df([1.0, 2.0, 3.0, 5.0])
        assert score_aging(df) == pytest.approx(100.0)

    def test_half_over_30d_scores_60(self):
        # 2 red out of 4: pct_red = 0.5, pct_yellow = 0
        # score = 100 - 0.5*80 = 60
        df = self._open_df([40.0, 50.0, 1.0, 2.0])
        assert score_aging(df) == pytest.approx(60.0)

    def test_all_over_30d_scores_20(self):
        # pct_red = 1.0: score = 100 - 80 = 20
        df = self._open_df([31.0, 45.0, 60.0, 90.0])
        assert score_aging(df) == pytest.approx(20.0)


# ═══════════════════════════════════════════════════════════════════════════════
# TestMetricStatus
# ═══════════════════════════════════════════════════════════════════════════════

class TestMetricStatus:
    def test_75_is_boa(self):
        emoji, label = metric_status(75)
        assert emoji == "🟢"
        assert label == "Boa"

    def test_55_is_atencao(self):
        emoji, label = metric_status(55)
        assert emoji == "🟡"
        assert label == "Atenção"

    def test_30_is_critica(self):
        emoji, label = metric_status(30)
        assert emoji == "🔴"
        assert label == "Crítica"


# ═══════════════════════════════════════════════════════════════════════════════
# TestSquadHealthScore
# ═══════════════════════════════════════════════════════════════════════════════

class TestSquadHealthScore:
    def _base_issues(self) -> pd.DataFrame:
        """Minimal issues DataFrame with a few months of data."""
        rows = []
        # 3 months of história/bug resolved items to give the DORA summary something
        for month_offset, count in [(5, 4), (4, 5), (3, 6), (2, 5), (1, 4)]:
            for _ in range(count):
                rows.append({
                    "key": f"PROJ-{len(rows):04d}",
                    "issuetype": "História",
                    "team": "Time Alfa",
                    "status": "Feito",
                    "created": _d(month_offset * 30 + 5),
                    "resolutiondate": _d(month_offset * 30 - 2),
                    "data_implantacao": None,
                    "updated": None,
                })
        return pd.DataFrame(rows)

    def test_returns_correct_keys(self):
        df = self._base_issues()
        result = squad_health_score(df)
        expected_keys = {
            "score", "status", "trend", "metrics",
            "impacts", "window", "prev_score",
            "current_dora_month", "current_month_dora",
        }
        assert expected_keys.issubset(result.keys())

    def test_score_is_between_0_and_100(self):
        df = self._base_issues()
        result = squad_health_score(df)
        assert 0.0 <= result["score"] <= 100.0

    def test_no_historical_data_trend_is_sem_historico(self):
        """With only 3 or fewer DORA months (no prev window), trend = Sem histórico."""
        # Use a single month of data — no previous window possible
        rows = []
        for _ in range(5):
            rows.append({
                "key": f"PROJ-{len(rows):04d}",
                "issuetype": "História",
                "team": "Time Alfa",
                "status": "Feito",
                "created": _d(10),
                "resolutiondate": _d(3),
                "data_implantacao": None,
                "updated": None,
            })
        df = pd.DataFrame(rows)
        result = squad_health_score(df)
        assert result["trend"] == "→ Sem histórico"

    def test_metrics_dict_has_5_keys_with_resolved_issues(self):
        df = self._base_issues()
        result = squad_health_score(df)
        assert set(result["metrics"].keys()) == {
            "lead_time", "throughput", "aging", "mttr", "cfr"
        }

    def test_throughput_window_excludes_wip_month(self):
        """Latest resolved month (WIP) must not influence the throughput window."""
        rows = []
        # 4 closed months Jan–Apr 2026: 10 items each
        for mo in range(1, 5):
            for day in range(1, 11):
                rows.append({
                    "key": f"PROJ-{len(rows):04d}",
                    "issuetype": "História",
                    "team": "Time Alfa",
                    "status": "Feito",
                    "created": datetime.datetime(2026, mo, 1),
                    "resolutiondate": datetime.datetime(2026, mo, min(day, 28)),
                    "data_implantacao": None,
                    "updated": None,
                })
        # WIP month (May 2026): 1 item — must be excluded
        rows.append({
            "key": "PROJ-9999",
            "issuetype": "História",
            "team": "Time Alfa",
            "status": "Feito",
            "created": datetime.datetime(2026, 5, 1),
            "resolutiondate": datetime.datetime(2026, 5, 5),
            "data_implantacao": None,
            "updated": None,
        })
        df = pd.DataFrame(rows)
        result = squad_health_score(df)
        # With WIP excluded: cur_tp_win=[Feb,Mar,Apr], tp_avg=10.0
        # Without exclusion: cur_tp_win=[Mar,Apr,May], tp_avg≈7.0
        assert result["metrics"]["throughput"]["value"] == pytest.approx(10.0)

    def test_current_month_dora_skips_month_without_lt_or_mttr(self):
        """current_month_dora must be the latest month that has LT or MTTR data."""
        rows = [
            # May 2026: resolved história → produces lead_time data
            {
                "key": "PROJ-0001",
                "issuetype": "História",
                "team": "Time Alfa",
                "status": "Feito",
                "created": datetime.datetime(2026, 5, 1),
                "resolutiondate": datetime.datetime(2026, 5, 10),
                "data_implantacao": None,
                "updated": None,
            },
            # Jun 2026: GMUD only → produces deployment data but no LT or MTTR
            {
                "key": "PROJ-0002",
                "issuetype": "GMUD",
                "team": "Time Alfa",
                "status": "Feito",
                "created": datetime.datetime(2026, 6, 1),
                "resolutiondate": datetime.datetime(2026, 6, 5),
                "data_implantacao": datetime.datetime(2026, 6, 5),
                "updated": None,
            },
        ]
        df = pd.DataFrame(rows)
        result = squad_health_score(df)
        # Jun has no LT or MTTR → must be skipped; current_dora_month must fall back to May
        assert result["current_dora_month"] == "2026-05"
