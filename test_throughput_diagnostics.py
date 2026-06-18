"""
test_throughput_diagnostics.py — Unit tests for build_throughput_diagnostics().

All tests use forged DataFrames only.  No Streamlit, no DB, no Jira API.
"""

from __future__ import annotations

import datetime

import pandas as pd
import pytest

from core_metrics import build_throughput_diagnostics

# ── Fixed reference date — all age calculations relative to this ──────────────
_TODAY = datetime.date(2026, 6, 18)
_TS = lambda d: datetime.datetime(2026, 6, 18) - datetime.timedelta(days=d)


# ── DataFrame helpers ─────────────────────────────────────────────────────────

def _open(status: str, created_days_ago: int, team: str = "Time Alfa") -> dict:
    """One open issue (no resolutiondate)."""
    return {
        "key": None,          # filled by _df()
        "issuetype": "História",
        "team": team,
        "status": status,
        "created": _TS(created_days_ago),
        "resolutiondate": None,
        "updated": None,
        "data_implantacao": None,
    }


def _closed(status: str = "Feito", created_days_ago: int = 10,
            resolved_days_ago: int = 1, team: str = "Time Alfa") -> dict:
    """One resolved issue."""
    return {
        "key": None,
        "issuetype": "História",
        "team": team,
        "status": status,
        "created": _TS(created_days_ago),
        "resolutiondate": _TS(resolved_days_ago),
        "updated": None,
        "data_implantacao": None,
    }


def _df(rows: list[dict]) -> pd.DataFrame:
    records = [{**r, "key": f"PROJ-{i+1:04d}"} for i, r in enumerate(rows)]
    df = pd.DataFrame(records)
    for col in ("created", "resolutiondate", "updated", "data_implantacao"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def _pred(label: str) -> dict:
    colors = {"Alta": "#15803d", "Média": "#ca8a04", "Baixa": "#dc2626"}
    return {"label": label, "emoji": "🟢", "color": colors[label]}


def _closed_list(*counts) -> list[dict]:
    """Build a closed_list with the given monthly counts (oldest → newest)."""
    months = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]
    return [
        {"month": months[i], "count": c, "label": months[i]}
        for i, c in enumerate(counts)
    ]


# ── Rule 1: Aging × Throughput ────────────────────────────────────────────────

class TestRule1AgingTP:
    def test_fires_good_tp_up_aging_ok(self):
        """TP last > TP prev AND pct_critical < 20% → 'resolvidos mais rápido'."""
        # 2 closed months: 5 → 8 (up)
        cl = _closed_list(5, 8)
        # Open items all very recent → 0% critical
        df = _df([_open("Em Andamento", created_days_ago=2) for _ in range(5)])

        diag, rec = build_throughput_diagnostics(cl, df, None, _pred("Alta"), today=_TODAY)

        assert len(diag) == 1
        assert "resolvidos mais rápido" in diag[0]
        assert "regularidade" in rec[0]

    def test_fires_bad_tp_down_aging_bad(self):
        """TP last < TP prev AND pct_critical > 30% → 'demorando mais'."""
        cl = _closed_list(10, 3)
        # 4 out of 5 open items older than 30 days → pct_crit = 80%
        df = _df(
            [_open("Em Andamento", created_days_ago=45) for _ in range(4)]
            + [_open("Em Andamento", created_days_ago=2)]
        )

        diag, rec = build_throughput_diagnostics(cl, df, None, _pred("Alta"), today=_TODAY)

        assert len(diag) == 1
        assert "demorando mais" in diag[0]
        assert "itens mais antigos" in rec[0]

    def test_does_not_fire_tp_up_but_aging_bad(self):
        """TP went up but aging is bad → rule must NOT fire (AND condition)."""
        cl = _closed_list(3, 8)
        df = _df([_open("Em Andamento", created_days_ago=45) for _ in range(5)])

        diag, _ = build_throughput_diagnostics(cl, df, None, _pred("Alta"), today=_TODAY)

        assert diag == []

    def test_does_not_fire_tp_down_but_aging_ok(self):
        """TP went down but aging is fine → rule must NOT fire (AND condition)."""
        cl = _closed_list(10, 2)
        df = _df([_open("Em Andamento", created_days_ago=2) for _ in range(5)])

        diag, _ = build_throughput_diagnostics(cl, df, None, _pred("Alta"), today=_TODAY)

        assert diag == []

    def test_does_not_fire_with_fewer_than_2_closed_months(self):
        """Only 1 closed month → rule 1 cannot compare, must stay silent."""
        cl = _closed_list(7)
        df = _df([_open("Em Andamento", created_days_ago=2)])

        diag, _ = build_throughput_diagnostics(cl, df, None, _pred("Alta"), today=_TODAY)

        assert diag == []

    def test_respects_team_filter_for_aging(self):
        """When team='Time Beta', aging is computed only for that team's open items."""
        cl = _closed_list(5, 8)
        # Time Beta: 1 fresh open item → pct_crit = 0% → rule fires
        # Time Alfa: 5 old items → would push pct_crit high if included
        df = _df(
            [_open("Em Andamento", created_days_ago=45, team="Time Alfa") for _ in range(5)]
            + [_open("Em Andamento", created_days_ago=2, team="Time Beta")]
        )

        diag, _ = build_throughput_diagnostics(
            cl, df, "Time Beta", _pred("Alta"), today=_TODAY
        )

        assert len(diag) == 1
        assert "resolvidos mais rápido" in diag[0]


# ── Rule 2: Gargalo ───────────────────────────────────────────────────────────

class TestRule2Bottleneck:
    def _bottleneck_df(self, bottleneck_status: str = "Em Revisão",
                       bottleneck_count: int = 10) -> pd.DataFrame:
        """One dominant status + two smaller ones (all non-terminal)."""
        return _df(
            [_open(bottleneck_status, created_days_ago=5) for _ in range(bottleneck_count)]
            + [_open("Fazendo",  created_days_ago=5)]
            + [_open("A Fazer",  created_days_ago=5)]
        )

    def test_fires_when_one_status_dominates(self):
        """10 items in bottleneck vs 1+1 → ratio = 10/4 = 2.5 > 2.0."""
        df = self._bottleneck_df("Em Revisão", 10)

        diag, rec = build_throughput_diagnostics(
            [], df, None, _pred("Alta"), today=_TODAY
        )

        assert len(diag) == 1
        assert "Em Revisão" in diag[0]
        assert "Em Revisão" in rec[0]

    def test_status_name_appears_correctly(self):
        """The exact bottleneck status name must be present in both diag and rec."""
        df = self._bottleneck_df("Aguardando Aprovação", 12)

        diag, rec = build_throughput_diagnostics(
            [], df, None, _pred("Alta"), today=_TODAY
        )

        assert "Aguardando Aprovação" in diag[0]
        assert "Aguardando Aprovação" in rec[0]

    def test_does_not_fire_when_ratio_below_threshold(self):
        """3 items in each of 3 statuses → ratio = 1.0, no bottleneck."""
        df = _df(
            [_open("Em Revisão", 5) for _ in range(3)]
            + [_open("Fazendo",  5) for _ in range(3)]
            + [_open("A Fazer",  5) for _ in range(3)]
        )

        diag, _ = build_throughput_diagnostics(
            [], df, None, _pred("Alta"), today=_TODAY
        )

        assert diag == []

    def test_does_not_fire_with_only_one_active_status(self):
        """Only 1 non-terminal status → can't compute a meaningful ratio."""
        df = _df([_open("Em Andamento", 5) for _ in range(10)])

        diag, _ = build_throughput_diagnostics(
            [], df, None, _pred("Alta"), today=_TODAY
        )

        assert diag == []

    def test_excludes_terminal_statuses_from_bottleneck(self):
        """'Feito' is terminal — must not be the bottleneck even with high count."""
        df = _df(
            [_open("Feito",    5) for _ in range(20)]   # terminal, should be excluded
            + [_open("Fazendo", 5) for _ in range(3)]
            + [_open("A Fazer", 5) for _ in range(2)]
        )

        diag, _ = build_throughput_diagnostics(
            [], df, None, _pred("Alta"), today=_TODAY
        )

        # "Fazendo" vs "A Fazer": ratio = 3 / mean(3, 2) = 3 / 2.5 = 1.2 < 2.0 → no fire
        assert diag == []

    def test_respects_team_filter_for_bottleneck(self):
        """Team filter must scope the open-item count to the selected team only."""
        df = _df(
            # Time Beta: bottleneck
            [_open("Em Revisão", 5, team="Time Beta") for _ in range(10)]
            + [_open("Fazendo",  5, team="Time Beta")]
            + [_open("A Fazer",  5, team="Time Beta")]
            # Time Alfa: balanced (should be ignored)
            + [_open("Em Revisão", 5, team="Time Alfa") for _ in range(2)]
            + [_open("Fazendo",    5, team="Time Alfa") for _ in range(2)]
        )

        diag, _ = build_throughput_diagnostics(
            [], df, "Time Beta", _pred("Alta"), today=_TODAY
        )

        assert len(diag) == 1
        assert "Em Revisão" in diag[0]


# ── Rule 3: Previsibilidade ───────────────────────────────────────────────────

class TestRule3Predictability:
    def test_fires_when_label_is_baixa(self):
        """pred['label'] == 'Baixa' → 'variado bastante' appears in diag."""
        df = _df([_open("Em Andamento", 5)])

        diag, rec = build_throughput_diagnostics(
            [], df, None, _pred("Baixa"), today=_TODAY
        )

        assert any("variado bastante" in d for d in diag)
        assert any("instável" in r for r in rec)

    def test_does_not_fire_when_label_is_alta(self):
        df = _df([_open("Em Andamento", 5)])

        diag, _ = build_throughput_diagnostics(
            [], df, None, _pred("Alta"), today=_TODAY
        )

        assert diag == []

    def test_does_not_fire_when_label_is_media(self):
        df = _df([_open("Em Andamento", 5)])

        diag, _ = build_throughput_diagnostics(
            [], df, None, _pred("Média"), today=_TODAY
        )

        assert diag == []

    def test_compute_predictability_integration(self):
        """Verify that a high-CV series produces pred['label'] == 'Baixa',
        which then causes rule 3 to fire when passed to build_throughput_diagnostics."""
        from core_metrics import compute_predictability
        import numpy as np

        # cv > 30% → "Baixa"
        pred = compute_predictability(cv=0.45)
        assert pred["label"] == "Baixa"

        df = _df([_open("Em Andamento", 5)])
        diag, _ = build_throughput_diagnostics([], df, None, pred, today=_TODAY)

        assert any("variado bastante" in d for d in diag)


# ── No rule fires ─────────────────────────────────────────────────────────────

class TestNoRuleFires:
    def test_empty_when_nothing_applies(self):
        """1 closed month (rule 1 needs ≥ 2), balanced statuses (rule 2 < threshold),
        pred Alta (rule 3 silent) → both lists must be empty."""
        cl = _closed_list(5)
        df = _df(
            [_open("Em Revisão", 5) for _ in range(3)]
            + [_open("Fazendo",  5) for _ in range(3)]
            + [_open("A Fazer",  5) for _ in range(3)]
        )

        diag, rec = build_throughput_diagnostics(
            cl, df, None, _pred("Alta"), today=_TODAY
        )

        assert diag == []
        assert rec == []

    def test_empty_with_no_open_items(self):
        """No open items → rule 2 can't fire. No closed-month pair → rule 1 silent.
        pred Alta → rule 3 silent."""
        df = _df([_closed() for _ in range(5)])

        diag, rec = build_throughput_diagnostics(
            [], df, None, _pred("Alta"), today=_TODAY
        )

        assert diag == []
        assert rec == []

    def test_diag_and_rec_always_same_length(self):
        """Every fired rule appends to both lists → lengths must always be equal."""
        # Fire all three rules at once
        cl = _closed_list(5, 8)
        df = _df(
            [_open("Em Revisão", 5) for _ in range(10)]   # bottleneck
            + [_open("Fazendo",  5)]
            + [_open("A Fazer",  5)]
        )

        diag, rec = build_throughput_diagnostics(
            cl, df, None, _pred("Baixa"), today=_TODAY
        )

        assert len(diag) == len(rec)
        assert len(diag) >= 1
