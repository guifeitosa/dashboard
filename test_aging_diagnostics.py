"""
test_aging_diagnostics.py — Unit tests for diagnose_status_concentration()
and build_aging_diagnostics().

All tests use forged DataFrames only.  No Streamlit, no DB, no Jira API.
"""

from __future__ import annotations

import datetime

import pandas as pd
import pytest

from core_metrics import build_aging_diagnostics, diagnose_status_concentration

# ── Fixed reference date ─────────────────────────────────────────────────────
_TODAY = datetime.date(2026, 6, 18)
_TS = lambda days_ago: datetime.datetime(2026, 6, 18) - datetime.timedelta(days=days_ago)


# ── DataFrame helpers ─────────────────────────────────────────────────────────

def _open(status: str, created_days_ago: int, updated_days_ago: int | None = None,
          team: str = "Time Alfa", issuetype: str = "História") -> dict:
    return {
        "key": None,
        "issuetype": issuetype,
        "team": team,
        "status": status,
        "created": _TS(created_days_ago),
        "resolutiondate": None,
        "updated": _TS(updated_days_ago) if updated_days_ago is not None else None,
        "data_implantacao": None,
    }


def _closed(created_days_ago: int = 10, team: str = "Time Alfa") -> dict:
    return {
        "key": None,
        "issuetype": "História",
        "team": team,
        "status": "Feito",
        "created": _TS(created_days_ago),
        "resolutiondate": _TS(1),
        "updated": _TS(1),
        "data_implantacao": None,
    }


def _df(rows: list[dict]) -> pd.DataFrame:
    records = [{**r, "key": f"PROJ-{i+1:04d}"} for i, r in enumerate(rows)]
    df = pd.DataFrame(records)
    for col in ("created", "resolutiondate", "updated", "data_implantacao"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def _prev(avg_age: float, pct_crit: float = 0.0, total: int = 10) -> dict:
    """Forge a prev_aging dict without calling compute_aging()."""
    crit_count = int(total * pct_crit)
    safe_count = total - crit_count
    return {
        "avg_age": avg_age,
        "total_open": total,
        "bands": {
            "0–7d": safe_count, "7–14d": 0, "14–30d": 0,
            "30–60d": crit_count // 2, "60+d": crit_count - crit_count // 2,
        },
        "sem_movimento": 0,
        "diagnosis": [],
    }


# ── diagnose_status_concentration (shared helper) ─────────────────────────────

class TestDiagnoseStatusConcentration:
    def test_returns_bottleneck_name_when_ratio_exceeded(self):
        df = _df(
            [_open("Em Revisão", 5) for _ in range(10)]
            + [_open("Fazendo",  5)]
            + [_open("A Fazer",  5)]
        )
        open_df = df[~df["resolutiondate"].notna()]
        result = diagnose_status_concentration(open_df)
        assert result == "Em Revisão"

    def test_returns_none_when_ratio_below_threshold(self):
        df = _df(
            [_open("Em Revisão", 5) for _ in range(3)]
            + [_open("Fazendo",  5) for _ in range(3)]
        )
        open_df = df[~df["resolutiondate"].notna()]
        assert diagnose_status_concentration(open_df) is None

    def test_returns_none_with_only_one_active_status(self):
        df = _df([_open("Em Andamento", 5) for _ in range(10)])
        open_df = df[~df["resolutiondate"].notna()]
        assert diagnose_status_concentration(open_df) is None

    def test_excludes_terminal_statuses(self):
        # "Feito" is terminal — 20 items there, 3+2 in active statuses
        df = _df(
            [_open("Feito",   5) for _ in range(20)]
            + [_open("Fazendo", 5) for _ in range(3)]
            + [_open("A Fazer", 5) for _ in range(2)]
        )
        open_df = df[~df["resolutiondate"].notna()]
        # Fazendo/A Fazer: ratio = 3 / mean(3, 2) = 1.2 < 2.0 → None
        assert diagnose_status_concentration(open_df) is None

    def test_returns_none_for_empty_dataframe(self):
        assert diagnose_status_concentration(pd.DataFrame()) is None

    def test_custom_ratio_threshold(self):
        # 4 vs 3 → ratio = 4 / mean(4,3) = 1.14 < 2.0 → None with default
        # but with threshold=1.0 → fires
        df = _df(
            [_open("Em Revisão", 5) for _ in range(4)]
            + [_open("Fazendo",  5) for _ in range(3)]
        )
        open_df = df[~df["resolutiondate"].notna()]
        assert diagnose_status_concentration(open_df, ratio_threshold=1.0) == "Em Revisão"
        assert diagnose_status_concentration(open_df, ratio_threshold=2.0) is None


# ── Rule 1: Gargalo ───────────────────────────────────────────────────────────

class TestRule1Gargalo:
    def test_fires_with_bottleneck_status(self):
        df = _df(
            [_open("Em Revisão", 5) for _ in range(10)]
            + [_open("Fazendo",  5)]
            + [_open("A Fazer",  5)]
        )
        diag, rec = build_aging_diagnostics(df, None, None, today=_TODAY)

        assert len(diag) == 1
        assert "Em Revisão" in diag[0]
        assert "Em Revisão" in rec[0]

    def test_diag_text_omits_throughput_phrase(self):
        """Aging version is shorter — no 'represando as entregas'."""
        df = _df(
            [_open("Em Revisão", 5) for _ in range(10)]
            + [_open("Fazendo",  5)]
            + [_open("A Fazer",  5)]
        )
        diag, _ = build_aging_diagnostics(df, None, None, today=_TODAY)
        assert "represando as entregas" not in diag[0]

    def test_respects_team_filter(self):
        df = _df(
            [_open("Em Revisão", 5, team="Time Beta") for _ in range(10)]
            + [_open("Fazendo",  5, team="Time Beta")]
            + [_open("A Fazer",  5, team="Time Beta")]
            + [_open("Em Revisão", 5, team="Time Alfa") for _ in range(2)]
            + [_open("A Fazer",    5, team="Time Alfa") for _ in range(2)]
        )
        # With team="Time Beta" → bottleneck fires
        diag_b, _ = build_aging_diagnostics(df, "Time Beta", None, today=_TODAY)
        assert any("Em Revisão" in d for d in diag_b)

        # With team="Time Alfa" → balanced, no bottleneck
        diag_a, _ = build_aging_diagnostics(df, "Time Alfa", None, today=_TODAY)
        assert not any("Em Revisão" in d for d in diag_a)

    def test_respects_issuetype_filter(self):
        df = _df(
            [_open("Em Revisão", 5, issuetype="Bug") for _ in range(10)]
            + [_open("Fazendo",  5, issuetype="Bug")]
            + [_open("A Fazer",  5, issuetype="Bug")]
            + [_open("Em Revisão",   5, issuetype="História") for _ in range(2)]
            + [_open("Em Andamento", 5, issuetype="História") for _ in range(2)]
        )
        diag_bug, _ = build_aging_diagnostics(df, None, "Bug", today=_TODAY)
        assert any("Em Revisão" in d for d in diag_bug)

        diag_hist, _ = build_aging_diagnostics(df, None, "História", today=_TODAY)
        assert not any("Em Revisão" in d for d in diag_hist)


# ── Rule 2: Tendência de Aging ────────────────────────────────────────────────

class TestRule2Tendencia:
    def _df_with_age(self, avg_days: int, total: int = 10) -> pd.DataFrame:
        """All open items created `avg_days` ago, no critical items."""
        return _df([_open("Em Andamento", avg_days) for _ in range(total)])

    def test_fires_worsened_when_avg_age_increased(self):
        """avg_age went from 5 to 15 days (+10) → 'demorando mais'."""
        df = self._df_with_age(15)
        prev = _prev(avg_age=5.0, pct_crit=0.0)

        diag, rec = build_aging_diagnostics(
            df, None, None, today=_TODAY, prev_aging=prev
        )

        assert any("demorando mais" in d for d in diag)
        assert any("mais antigos" in r for r in rec)

    def test_fires_worsened_when_pct_crit_increased(self):
        """pct_crit jumped from 5% to 40% → 'demorando mais'."""
        df = _df(
            [_open("Em Andamento", created_days_ago=45) for _ in range(4)]
            + [_open("Em Andamento", created_days_ago=2)  for _ in range(6)]
        )
        prev = _prev(avg_age=2.0, pct_crit=0.05)

        diag, rec = build_aging_diagnostics(
            df, None, None, today=_TODAY, prev_aging=prev
        )

        assert any("demorando mais" in d for d in diag)

    def test_fires_improved_when_avg_age_decreased(self):
        """avg_age went from 20 to 5 days (-15) → 'mais rápido'."""
        df = self._df_with_age(5)
        prev = _prev(avg_age=20.0, pct_crit=0.0)

        diag, rec = build_aging_diagnostics(
            df, None, None, today=_TODAY, prev_aging=prev
        )

        assert any("mais rápido" in d for d in diag)
        assert any("priorizando" in r for r in rec)

    def test_does_not_fire_without_prev_aging(self):
        """No prev_aging → rule must be silently skipped."""
        df = self._df_with_age(15)

        diag, _ = build_aging_diagnostics(df, None, None, today=_TODAY, prev_aging=None)

        assert not any("demorando mais" in d for d in diag)
        assert not any("mais rápido" in d for d in diag)

    def test_skips_when_prev_avg_age_is_negative(self):
        """Migration artifact: prev_avg_age < 0 → rule must not fire."""
        df = self._df_with_age(5)
        bad_prev = _prev(avg_age=-28.0)

        diag, _ = build_aging_diagnostics(
            df, None, None, today=_TODAY, prev_aging=bad_prev
        )

        assert not any("demorando mais" in d or "mais rápido" in d for d in diag)

    def test_neutral_zone_does_not_fire(self):
        """Delta of 0.5 day is below _AGING_TREND_AGE_DELTA=1.0 → no rule."""
        df = self._df_with_age(6)
        prev = _prev(avg_age=5.5, pct_crit=0.0)

        diag, _ = build_aging_diagnostics(
            df, None, None, today=_TODAY, prev_aging=prev
        )

        assert not any("demorando mais" in d or "mais rápido" in d for d in diag)


# ── Rule 3: Sem Movimentação ──────────────────────────────────────────────────

class TestRule3SemMovimentacao:
    def test_fires_when_above_20_pct(self):
        """3 out of 5 open items with no update in 20 days = 60% → fires."""
        df = _df(
            [_open("Em Andamento", 5, updated_days_ago=20) for _ in range(3)]
            + [_open("Em Andamento", 5, updated_days_ago=3)  for _ in range(2)]
        )
        diag, rec = build_aging_diagnostics(df, None, None, today=_TODAY)

        assert any("atualização recente" in d for d in diag)
        assert any("replanejados" in r for r in rec)

    def test_does_not_fire_when_below_threshold(self):
        """1 out of 10 items without update = 10% < 20% → no fire."""
        df = _df(
            [_open("Em Andamento", 5, updated_days_ago=20)]
            + [_open("Em Andamento", 5, updated_days_ago=3) for _ in range(9)]
        )
        diag, _ = build_aging_diagnostics(df, None, None, today=_TODAY)

        assert not any("atualização recente" in d for d in diag)

    def test_does_not_fire_when_updated_column_missing(self):
        """No 'updated' column → sem_movimento=None → rule skipped."""
        rows = [{"key": f"PROJ-{i}", "issuetype": "História", "team": "Time Alfa",
                 "status": "Em Andamento", "created": _TS(5), "resolutiondate": None,
                 "data_implantacao": None}
                for i in range(5)]
        df = pd.DataFrame(rows)
        df["created"] = pd.to_datetime(df["created"])

        diag, _ = build_aging_diagnostics(df, None, None, today=_TODAY)

        assert not any("atualização recente" in d for d in diag)

    def test_exactly_at_threshold_does_not_fire(self):
        """Exactly 20% (2 out of 10) → condition is > 0.20, so does not fire."""
        df = _df(
            [_open("Em Andamento", 5, updated_days_ago=20) for _ in range(2)]
            + [_open("Em Andamento", 5, updated_days_ago=3)  for _ in range(8)]
        )
        diag, _ = build_aging_diagnostics(df, None, None, today=_TODAY)

        assert not any("atualização recente" in d for d in diag)


# ── Rule 2: enriched text (status context + critical sanity check) ────────────

class TestRule2Enriched:
    """Verify the two new Rule 2 enhancements:
    1. When Rule 1 also fires, the bottleneck status appears in the Rule 2 text.
    2. When "improved" but pct_crit > 50%, the message is qualified instead of
       plain "mais rápido".
    """

    def _bottleneck_df(self, avg_days: int) -> pd.DataFrame:
        """10 items stuck in Em Revisão + 1 in each other status → Rule 1 fires."""
        return _df(
            [_open("Em Revisão", avg_days) for _ in range(10)]
            + [_open("Fazendo",  avg_days)]
            + [_open("A Fazer",  avg_days)]
        )

    def test_worsened_with_bottleneck_includes_status_name(self):
        """Rule 2 worsened AND Rule 1 fired → bottleneck status in Rule 2 text."""
        df = self._bottleneck_df(15)
        prev = _prev(avg_age=3.0, pct_crit=0.0)

        diag, rec = build_aging_diagnostics(
            df, None, None, today=_TODAY, prev_aging=prev
        )

        rule2_diag = [d for d in diag if "demorando mais" in d]
        assert len(rule2_diag) == 1
        assert "Em Revisão" in rule2_diag[0], (
            f"Expected bottleneck status in Rule 2 text, got: {rule2_diag[0]!r}"
        )

    def test_worsened_without_bottleneck_uses_generic_text(self):
        """Rule 2 worsened, Rule 1 NOT fired (balanced statuses) → generic text, no status."""
        df = _df(
            [_open("Em Revisão", 15) for _ in range(3)]
            + [_open("Fazendo",  15) for _ in range(3)]
            + [_open("A Fazer",  15) for _ in range(3)]
        )
        prev = _prev(avg_age=3.0, pct_crit=0.0)

        diag, _ = build_aging_diagnostics(
            df, None, None, today=_TODAY, prev_aging=prev
        )

        rule2_diag = [d for d in diag if "demorando mais" in d]
        assert len(rule2_diag) == 1
        assert "Em Revisão" not in rule2_diag[0]
        assert "do que no período anterior" in rule2_diag[0]

    def test_improved_with_high_pct_crit_is_qualified(self):
        """Rule 2 improved BUT pct_crit > 50% → message is qualified ('ainda é crítica')."""
        # 8 of 10 items are critical (> 30 days) → pct_crit = 80% > 50%
        df = _df(
            [_open("Em Andamento", created_days_ago=45) for _ in range(8)]
            + [_open("Em Andamento", created_days_ago=2)  for _ in range(2)]
        )
        # prev avg_age was worse (higher) and pct_crit was higher → "improved"
        prev = _prev(avg_age=50.0, pct_crit=0.90)

        diag, rec = build_aging_diagnostics(
            df, None, None, today=_TODAY, prev_aging=prev
        )

        rule2_diag = [d for d in diag if "mais rápido" in d]
        assert len(rule2_diag) == 1
        assert "ainda é crítica" in rule2_diag[0]
        assert "mais da metade" in rec[diag.index(rule2_diag[0])]

    def test_improved_with_low_pct_crit_is_plain(self):
        """Rule 2 improved and pct_crit ≤ 50% → plain 'mais rápido' without qualifier."""
        # 2 of 10 items are critical → pct_crit = 20% < 50%
        df = _df(
            [_open("Em Andamento", created_days_ago=45) for _ in range(2)]
            + [_open("Em Andamento", created_days_ago=2)  for _ in range(8)]
        )
        prev = _prev(avg_age=20.0, pct_crit=0.80)

        diag, rec = build_aging_diagnostics(
            df, None, None, today=_TODAY, prev_aging=prev
        )

        rule2_diag = [d for d in diag if "mais rápido" in d]
        assert len(rule2_diag) == 1
        assert "ainda é crítica" not in rule2_diag[0]
        assert "priorizando" in rec[diag.index(rule2_diag[0])]


# ── Rule 2: snapshot-format prev_aging (pct_critical key, no bands) ───────────

class TestRule2SnapshotFormat:
    """Verify that build_aging_diagnostics accepts the slim dict produced by
    metric_snapshots (avg_age + pct_critical + total_open, no 'bands' key).
    This is the code path used when a real historical snapshot exists."""

    def test_fires_worsened_with_snapshot_dict(self):
        """Snapshot dict (no bands) → worsened trend still detected."""
        df = _df([_open("Em Andamento", 15) for _ in range(10)])
        snap_prev = {"avg_age": 3.0, "pct_critical": 0.0, "total_open": 10}

        diag, rec = build_aging_diagnostics(
            df, None, None, today=_TODAY, prev_aging=snap_prev
        )

        assert any("demorando mais" in d for d in diag)
        assert any("mais antigos" in r for r in rec)

    def test_fires_improved_with_snapshot_dict(self):
        """Snapshot dict (no bands) → improved trend still detected."""
        df = _df([_open("Em Andamento", 2) for _ in range(10)])
        snap_prev = {"avg_age": 20.0, "pct_critical": 0.30, "total_open": 10}

        diag, rec = build_aging_diagnostics(
            df, None, None, today=_TODAY, prev_aging=snap_prev
        )

        assert any("mais rápido" in d for d in diag)


# ── No rule fires ─────────────────────────────────────────────────────────────

class TestNoRuleFires:
    def test_empty_when_nothing_applies(self):
        """Balanced statuses, no prev_aging, all items recently updated."""
        df = _df(
            [_open("Em Revisão",  5, updated_days_ago=3) for _ in range(3)]
            + [_open("Fazendo",   5, updated_days_ago=3) for _ in range(3)]
            + [_open("A Fazer",   5, updated_days_ago=3) for _ in range(3)]
        )
        diag, rec = build_aging_diagnostics(df, None, None, today=_TODAY)

        assert diag == []
        assert rec == []

    def test_empty_with_no_open_items(self):
        df = _df([_closed() for _ in range(5)])

        diag, rec = build_aging_diagnostics(df, None, None, today=_TODAY)

        assert diag == []
        assert rec == []

    def test_all_three_rules_fire_simultaneously(self):
        """Verify multiple rules can fire in one call and lists stay parallel."""
        # Rule 1: bottleneck
        # Rule 2: aging worsened
        # Rule 3: > 20% without recent update
        df = _df(
            [_open("Em Revisão", 5, updated_days_ago=20) for _ in range(10)]
            + [_open("Fazendo",  5, updated_days_ago=20)]
            + [_open("A Fazer",  5, updated_days_ago=3)]
        )
        prev = _prev(avg_age=1.0, pct_crit=0.0)

        diag, rec = build_aging_diagnostics(
            df, None, None, today=_TODAY, prev_aging=prev
        )

        assert len(diag) == len(rec)
        assert len(diag) >= 2  # at least bottleneck + sem_movimento

    def test_diag_and_rec_always_same_length(self):
        """Invariant: lists are always parallel regardless of which rules fire."""
        df = _df([_open("Em Andamento", 5, updated_days_ago=20) for _ in range(10)])
        prev = _prev(avg_age=1.0)

        diag, rec = build_aging_diagnostics(
            df, None, None, today=_TODAY, prev_aging=prev
        )

        assert len(diag) == len(rec)
