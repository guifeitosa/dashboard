"""
core_metrics.py — Central calculation layer for the Metricas Dashboard.

Design principle — non-duplication:
  - Raw DORA calculations (calculate_metrics_summary, aggregate_metrics_by_month)
    live in metrics.py and are imported here; we do NOT reimplement them.
  - Time-in-status helpers (time_in_status, average_time_in_status, lead_time_real)
    live in status_time.py and are NOT duplicated here.
  - squad_health.py keeps its own scoring copies until a future migration; this
    module provides the authoritative public-API versions of those functions.

All functions in this module:
  - Accept DataFrames (not file paths, not DB connections, not API calls).
  - Return plain Python dicts / DataFrames.
  - Have NO Streamlit imports.
"""

from __future__ import annotations

import datetime
import math
from typing import Optional

import numpy as np
import pandas as pd

from metrics import aggregate_metrics_by_month, calculate_metrics_summary

# ── Constants ────────────────────────────────────────────────────────────────

AGING_OVER_REP_THRESHOLD = 15  # percentage points above which a type/team is flagged

SQUAD_HEALTH_WEIGHTS = {
    "lead_time":  0.25,
    "throughput": 0.20,
    "aging":      0.25,
    "mttr":       0.15,
    "cfr":        0.15,
}

SQUAD_HEALTH_TREND_THRESHOLD = 5.0

_LEVEL_RANK = {"Elite": 0, "High": 1, "Medium": 2, "Low": 3, "N/A": 99}

# Canonical set of terminal/done status names (lowercased for case-insensitive matching).
# Used by jira_client.normalize_issue (resolutiondate fallback) and pages/fluxo.py
# (_is_terminal). Single definition avoids the two lists drifting apart.
# NOTE: entries are POST-normalization names — _normalize_migrated already strips
# the " (migrated)" suffix, so "feito (migrated)" → "feito" before this is checked.
TERMINAL_STATUSES: frozenset[str] = frozenset({
    "feito", "concluído", "concluido", "done", "fechado", "closed",
    "resolvido", "resolved", "completo", "completed",
})


# ────────────────────────────────────────────────────────────────────────────
# 1. Input normalisation
# ────────────────────────────────────────────────────────────────────────────

def prepare_df(issues_df: pd.DataFrame) -> pd.DataFrame:
    """Add derived columns used throughout the module.

    Added columns (idempotent — safe to call on an already-prepared df):
      year_month      : str  "YYYY-MM" derived from `created`
      is_resolved     : bool derived from resolutiondate.notna()
      data_implantacao: column ensured to exist (pd.NaT where missing)

    The original DataFrame is not modified; a copy is returned.
    """
    df = issues_df.copy()

    # Ensure datetime types
    for col in ("created", "resolutiondate", "updated", "data_implantacao"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce", utc=False)

    if "year_month" not in df.columns:
        df["year_month"] = df["created"].dt.to_period("M").astype(str)

    if "is_resolved" not in df.columns:
        df["is_resolved"] = df["resolutiondate"].notna()

    if "data_implantacao" not in df.columns:
        df["data_implantacao"] = pd.NaT

    return df


# ────────────────────────────────────────────────────────────────────────────
# 2. DORA classification
# ────────────────────────────────────────────────────────────────────────────

def dora_band(key: str, value) -> str:
    """Return Elite / High / Medium / Low / N/A for a DORA metric value.

    Thresholds:
      lead_time_days:       Elite < 1d,  High 1–7d,   Medium 7–30d,  Low > 30d
      deploy_freq_interval: Elite ≤ 1d,  High ≤ 5d,   Medium ≤ 20d,  Low > 20d
      mttr_hours:           Elite < 1h,  High < 24h,  Medium < 168h, Low ≥ 168h
      cfr_percent:          Elite 0–15% (inclusive), High 16–30%,
                            Medium 31–45%, Low > 45%
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "N/A"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if key == "lead_time_days":
        return "Elite" if v < 1 else "High" if v <= 7 else "Medium" if v <= 30 else "Low"
    if key == "deploy_freq_interval":
        return "Elite" if v <= 1 else "High" if v <= 5 else "Medium" if v <= 20 else "Low"
    if key == "mttr_hours":
        return "Elite" if v < 1 else "High" if v < 24 else "Medium" if v < 168 else "Low"
    if key == "cfr_percent":
        return "Elite" if v <= 15 else "High" if v <= 30 else "Medium" if v <= 45 else "Low"
    return "N/A"


def worst_dora_band(dora_values: dict) -> tuple[str, str]:
    """Return (worst_band, key_of_worst_metric), ignoring N/A values."""
    worst_band, worst_key = "N/A", ""
    for key, val in dora_values.items():
        band = dora_band(key, val)
        if band == "N/A":
            continue
        if worst_band == "N/A" or _LEVEL_RANK[band] > _LEVEL_RANK[worst_band]:
            worst_band, worst_key = band, key
    return worst_band, worst_key


# ────────────────────────────────────────────────────────────────────────────
# 3. Throughput
# ────────────────────────────────────────────────────────────────────────────

def compute_trend(counts: list[float], avg_val: float) -> dict:
    """Classify a throughput trend from a list of monthly counts.

    Rules (applied in order):
      Crescimento: last 3 months all strictly above avg_val
      Queda:       last 2 months both strictly below avg_val
      Estável:     all other cases (including fewer than 2 data points)

    Returns dict with keys: label, icon, color, desc.
    """
    if len(counts) >= 3 and all(c > avg_val for c in counts[-3:]):
        return {
            "label": "Crescimento",
            "icon": "↗",
            "color": "#15803d",
            "desc": "Últimos 3 meses acima da média",
        }
    if len(counts) >= 2 and all(c < avg_val for c in counts[-2:]):
        return {
            "label": "Queda",
            "icon": "↘",
            "color": "#dc2626",
            "desc": "Últimos 2 meses abaixo da média",
        }
    return {
        "label": "Estável",
        "icon": "→",
        "color": "#94a3b8",
        "desc": "Sem tendência definida",
    }


def compute_throughput_health(
    trend_label: str,
    last_count: float,
    avg_val: float,
    cv: float,
) -> dict:
    """Classify throughput health.

    Rules (applied in order):
      Crítica: last < 50% avg  OR  (Queda AND last < 70% avg)
      Boa:     (Crescimento or Estável) AND cv < 0.40
      Atenção: all other cases

    Returns dict with keys: label, emoji, color, desc.
    """
    ratio = (last_count / avg_val) if avg_val > 0 else 1.0
    if ratio < 0.50 or (trend_label == "Queda" and ratio < 0.70):
        return {
            "label": "Crítica",
            "emoji": "🔴",
            "color": "#dc2626",
            "desc": "Último mês muito abaixo da média",
        }
    if trend_label in ("Crescimento", "Estável") and cv < 0.40:
        return {
            "label": "Boa",
            "emoji": "🟢",
            "color": "#15803d",
            "desc": "Volume estável ou crescente",
        }
    return {
        "label": "Atenção",
        "emoji": "🟡",
        "color": "#ca8a04",
        "desc": "Queda moderada ou alta variabilidade",
    }


def compute_predictability(cv: float) -> dict:
    """Classify throughput predictability by coefficient of variation.

    Alta:  cv < 15%
    Média: 15% ≤ cv ≤ 30%
    Baixa: cv > 30%

    Returns dict with keys: label, emoji, color.
    """
    pct = cv * 100
    if pct < 15:
        return {"label": "Alta",  "emoji": "🟢", "color": "#15803d"}
    if pct <= 30:
        return {"label": "Média", "emoji": "🟡", "color": "#ca8a04"}
    return {"label": "Baixa", "emoji": "🔴", "color": "#dc2626"}


def diagnose_throughput_drop(
    period_months: list[str],
    tp_by_month: dict[str, float],
    issues_df: pd.DataFrame,
) -> list[dict]:
    """Heuristic decomposition of a throughput drop (identical algorithm to
    pages/throughput.py _diagnose_drop).

    Requires at least 3 months in period_months.
    Returns [] when there is no drop (delta_tp ≤ 0) or fewer than 3 months.

    Algorithm:
      1. For each candidate factor f (Aging, Bugs, Incidentes), compute the
         relative deviation of the *last* month vs. the series mean:
             desvio_f = max(0, (f_last - f_mean) / f_mean)
         (clipped at 0 so factors moving in the "good" direction don't explain
         a throughput drop)
      2. total_signal = Σ desvio_f
      3. delta_tp = (tp_mean - tp_last) / tp_mean  (relative drop)
         fração_explicada = min(1, total_signal / delta_tp)
      4. Each factor receives: fração_explicada * (desvio_f / total_signal) * 100 %
         Remainder → "Variação normal"

    Returns list[dict] with keys {label, pct}, sorted descending by pct.
    """
    months = list(period_months)
    if len(months) < 3:
        return []

    tp_s = pd.Series([float(tp_by_month.get(m, 0)) for m in months])
    tp_mean = tp_s.mean()
    tp_last = tp_s.iloc[-1]
    delta_throughput = (tp_mean - tp_last) / tp_mean if tp_mean > 0 else 0.0
    if delta_throughput <= 0:
        return []

    # Guard: issues_df may have no 'created' column
    if "created" not in issues_df.columns:
        return [{"label": "Variação normal", "pct": 100.0}]

    df = issues_df.copy()
    df["created"] = pd.to_datetime(df["created"], errors="coerce")
    if "resolutiondate" not in df.columns:
        df["resolutiondate"] = pd.NaT
    df["resolutiondate"] = pd.to_datetime(df["resolutiondate"], errors="coerce")

    lower_type = df["issuetype"].astype(str).str.lower() if "issuetype" in df.columns else pd.Series(
        [""] * len(df), index=df.index
    )

    def _created_counts(sub: pd.DataFrame) -> pd.Series:
        cm = sub["created"].dt.to_period("M").astype(str)
        vc = cm.value_counts().to_dict()
        return pd.Series([float(vc.get(m, 0)) for m in months])

    bugs = _created_counts(df[lower_type == "bug"])
    incidents = _created_counts(df[lower_type == "incidente"])

    # Aging: issues open > 30 days at the end of each month
    aging_vals = []
    for m in months:
        t_end = pd.Period(m, freq="M").end_time
        cutoff = t_end - pd.Timedelta(days=30)
        aged = df[
            (df["created"] <= cutoff)
            & (df["resolutiondate"].isna() | (df["resolutiondate"] > t_end))
        ]
        aging_vals.append(float(len(aged)))
    aging = pd.Series(aging_vals)

    factors = {"Aging": aging, "Bugs": bugs, "Incidentes": incidents}

    deviations: dict[str, float] = {}
    for label, series in factors.items():
        mean = series.mean()
        last = series.iloc[-1]
        deviations[label] = max(0.0, (last - mean) / mean) if mean > 0 else 0.0

    total_signal = sum(deviations.values())
    if total_signal <= 0:
        return [{"label": "Variação normal", "pct": 100.0}]

    explained = min(1.0, total_signal / delta_throughput)

    parts: list[dict] = []
    shown_pct = 0.0
    for label, dev in deviations.items():
        pct = explained * (dev / total_signal) * 100
        if pct >= 1:
            parts.append({"label": label, "pct": pct})
            shown_pct += pct
    parts.append({"label": "Variação normal", "pct": 100.0 - shown_pct})
    parts.sort(key=lambda d: d["pct"], reverse=True)
    return parts


def compute_throughput(
    issues_df: pd.DataFrame,
    team: Optional[str] = None,
    start_month: Optional[str] = None,
    end_month: Optional[str] = None,
) -> dict:
    """Full throughput analysis over resolved issues.

    The most-recent month is always treated as WIP (in-progress) and excluded
    from avg / trend / health calculations.  When only 1 month exists the single
    month is treated as closed (no WIP split).

    Parameters
    ----------
    issues_df   : DataFrame with at least `resolutiondate` and `is_resolved`
                  columns (run prepare_df first, or it is called internally).
    team        : Optional team filter (exact match on the `team` column).
    start_month : Optional inclusive lower bound "YYYY-MM".
    end_month   : Optional inclusive upper bound "YYYY-MM".

    Returns
    -------
    dict with:
      monthly        : list[dict] — all months {month, label, count, is_wip}
      closed         : list[dict] — monthly without the WIP month
      avg            : float — mean count across closed months
      cv             : float — coefficient of variation (std/avg) of closed months
      n_months       : int   — number of closed months
      total          : int   — total items across closed months
      best           : dict  — {month, label, count} of best closed month
      worst          : dict  — {month, label, count} of worst closed month
      trend          : dict  — from compute_trend
      health         : dict  — from compute_throughput_health
      predictability : dict  — from compute_predictability
      drop_pct       : float — (avg - last_closed_count) / avg * 100
      wip            : dict|None — {month, label, count} of WIP month or None
    Returns an empty dict when issues_df is empty or has no resolved items.
    """
    if issues_df is None or issues_df.empty:
        return {}

    df = prepare_df(issues_df)

    resolved = df[df["is_resolved"]].copy()
    if resolved.empty:
        return {}

    resolved["res_month"] = resolved["resolutiondate"].dt.to_period("M").astype(str)

    # Team filter
    if team is not None and "team" in resolved.columns:
        resolved = resolved[resolved["team"] == team]

    # Month range filter
    if start_month:
        resolved = resolved[resolved["res_month"] >= start_month]
    if end_month:
        resolved = resolved[resolved["res_month"] <= end_month]

    if resolved.empty:
        return {}

    monthly_counts = (
        resolved.groupby("res_month").size()
        .reset_index(name="count")
        .sort_values("res_month")
    )

    def _fmt_month(ym: str) -> str:
        _PT = {
            "01": "JAN", "02": "FEV", "03": "MAR", "04": "ABR",
            "05": "MAI", "06": "JUN", "07": "JUL", "08": "AGO",
            "09": "SET", "10": "OUT", "11": "NOV", "12": "DEZ",
        }
        try:
            y, m = ym.split("-")
            return f"{_PT.get(m, m)}/{y[-2:]}"
        except Exception:
            return ym

    # Build monthly list
    monthly: list[dict] = []
    for _, row in monthly_counts.iterrows():
        monthly.append({
            "month": row["res_month"],
            "label": _fmt_month(row["res_month"]),
            "count": int(row["count"]),
            "is_wip": False,
        })

    # WIP split: last month is WIP unless only 1 month exists
    wip: Optional[dict] = None
    if len(monthly) > 1:
        monthly[-1]["is_wip"] = True
        wip = {k: v for k, v in monthly[-1].items()}
        closed = monthly[:-1]
    else:
        closed = monthly

    if not closed:
        return {}

    closed_counts = [m["count"] for m in closed]
    n_months = len(closed_counts)
    total = int(sum(closed_counts))
    avg = sum(closed_counts) / n_months if n_months else 0.0

    # CV: standard deviation / mean (requires at least 2 months)
    cv = 0.0
    if n_months > 1 and avg > 0:
        variance = sum((c - avg) ** 2 for c in closed_counts) / n_months
        cv = math.sqrt(variance) / avg

    best_m = max(closed, key=lambda m: m["count"])
    worst_m = min(closed, key=lambda m: m["count"])

    last_count = float(closed_counts[-1])
    trend = compute_trend(closed_counts, avg)
    health = compute_throughput_health(trend["label"], last_count, avg, cv)
    predictability = compute_predictability(cv)

    drop_pct = (avg - last_count) / avg * 100 if avg > 0 else 0.0

    return {
        "monthly": monthly,
        "closed": closed,
        "avg": avg,
        "cv": cv,
        "n_months": n_months,
        "total": total,
        "best": {"month": best_m["month"], "label": best_m["label"], "count": best_m["count"]},
        "worst": {"month": worst_m["month"], "label": worst_m["label"], "count": worst_m["count"]},
        "trend": trend,
        "health": health,
        "predictability": predictability,
        "drop_pct": drop_pct,
        "wip": wip,
    }


# ────────────────────────────────────────────────────────────────────────────
# 4. Aging
# ────────────────────────────────────────────────────────────────────────────

def compute_aging(
    issues_df: pd.DataFrame,
    today: Optional[datetime.date] = None,
    team: Optional[str] = None,
    issuetype: Optional[str] = None,
) -> dict:
    """Aging analysis for open issues.

    Parameters
    ----------
    issues_df : DataFrame — will be passed through prepare_df internally.
    today     : Reference date for age computation. Defaults to datetime.date.today().
    team      : Optional exact-match filter on the `team` column.
    issuetype : Optional exact-match filter on the `issuetype` column.

    Returns
    -------
    dict with:
      total_open  : int
      avg_age     : float  — mean days since created (over open issues)
      bands       : dict   — {"0–7d": n, "7–14d": n, "14–30d": n, "30–60d": n, "60+d": n}
      sem_movimento : int|None — issues with no update in > 14 days;
                                  None when the `updated` column is absent/all-NaT
      diagnosis   : list[dict] — over-represented types/teams in critical band (> 30d)
                     Each entry: {dim, val, n_red, pct_red, pct_total, over_rep}
                     Only included when over_rep >= AGING_OVER_REP_THRESHOLD and n_red >= 1.
    """
    if today is None:
        today = datetime.date.today()
    ts_today = pd.Timestamp(today)

    df = prepare_df(issues_df)

    # Only open issues
    open_df = df[~df["is_resolved"]].copy()

    # Filters
    if team is not None and "team" in open_df.columns:
        open_df = open_df[open_df["team"] == team]
    if issuetype is not None and "issuetype" in open_df.columns:
        open_df = open_df[open_df["issuetype"] == issuetype]

    total_open = len(open_df)
    if total_open == 0:
        return {
            "total_open": 0,
            "avg_age": 0.0,
            "bands": {"0–7d": 0, "7–14d": 0, "14–30d": 0, "30–60d": 0, "60+d": 0},
            "sem_movimento": None,
            "diagnosis": [],
        }

    open_df = open_df.copy()
    open_df["dias_parado"] = (ts_today - open_df["created"]).dt.days.astype(float)

    avg_age = float(open_df["dias_parado"].mean())

    d = open_df["dias_parado"]
    bands = {
        "0–7d":   int((d < 7).sum()),
        "7–14d":  int(((d >= 7) & (d < 14)).sum()),
        "14–30d": int(((d >= 14) & (d <= 30)).sum()),
        "30–60d": int(((d > 30) & (d <= 60)).sum()),
        "60+d":   int((d > 60).sum()),
    }

    # sem_movimento: issues with no update in > 14 days
    sem_movimento: Optional[int] = None
    has_updated = (
        "updated" in open_df.columns
        and open_df["updated"].notna().any()
    )
    if has_updated:
        open_df["updated"] = pd.to_datetime(open_df["updated"], errors="coerce")
        dias_sem_update = (ts_today - open_df["updated"]).dt.days
        sem_movimento = int((dias_sem_update > 14).sum())

    # Diagnosis: over-representation in critical band (> 30d)
    red = open_df[open_df["dias_parado"] > 30]
    diagnosis: list[dict] = []
    if not red.empty and total_open > 0:
        dims = []
        if "issuetype" in open_df.columns:
            dims.append(("Tipo", "issuetype"))
        if "team" in open_df.columns:
            dims.append(("Time", "team"))

        for dim, col in dims:
            for val in open_df[col].dropna().unique():
                n_total_val = int((open_df[col] == val).sum())
                n_red_val = int((red[col] == val).sum())
                pct_total = n_total_val / total_open * 100
                pct_red_val = n_red_val / len(red) * 100 if len(red) > 0 else 0.0
                over_rep = pct_red_val - pct_total
                if over_rep >= AGING_OVER_REP_THRESHOLD and n_red_val >= 1:
                    diagnosis.append({
                        "dim": dim,
                        "val": val,
                        "n_red": n_red_val,
                        "pct_red": pct_red_val,
                        "pct_total": pct_total,
                        "over_rep": over_rep,
                    })

    return {
        "total_open": total_open,
        "avg_age": avg_age,
        "bands": bands,
        "sem_movimento": sem_movimento,
        "diagnosis": diagnosis,
    }


# ────────────────────────────────────────────────────────────────────────────
# 5. Scoring helpers
# ────────────────────────────────────────────────────────────────────────────

def _score_lower_better(
    v: float,
    elite_hi: float,
    high_hi: float,
    medium_hi: float,
    elite_inclusive: bool = False,
) -> float:
    """Normalise a lower-is-better metric to [0, 100] using DORA-style bands.

    Band mapping:
      Elite  → 90–100  (v = 0 → 100; v at boundary → 90)
      High   → 70–89
      Medium → 50–69
      Low    → 0–49    (decays to 0 over a range equal to medium_hi; floor 0)

    elite_inclusive: True when the Elite upper boundary is inclusive (e.g. CFR ≤ 15%).
    """
    in_elite = (v <= elite_hi) if elite_inclusive else (v < elite_hi)

    if in_elite:
        base = elite_hi if elite_hi > 0 else 1.0
        return 100.0 - (v / base) * 10.0

    if v <= high_hi:
        return 89.0 - ((v - elite_hi) / (high_hi - elite_hi)) * 19.0

    if v <= medium_hi:
        return 69.0 - ((v - high_hi) / (medium_hi - high_hi)) * 19.0

    # Low band — decays from 49 at medium_hi to 0 at 2 × medium_hi; floor 0
    return max(0.0, 49.0 - ((v - medium_hi) / medium_hi) * 49.0)


def score_lead_time(days: float) -> float:
    """Score lead-time-for-changes (days). Elite < 1d, High 1–7d, Medium 7–30d."""
    return _score_lower_better(days, elite_hi=1.0, high_hi=7.0, medium_hi=30.0)


def score_mttr(hours: float) -> float:
    """Score MTTR (hours). Elite < 1h, High < 24h, Medium < 168h."""
    return _score_lower_better(hours, elite_hi=1.0, high_hi=24.0, medium_hi=168.0)


def score_cfr(pct: float) -> float:
    """Score Change Failure Rate (%). Elite 0–15% (inclusive), High ≤ 30%, Medium ≤ 45%."""
    return _score_lower_better(
        pct, elite_hi=15.0, high_hi=30.0, medium_hi=45.0, elite_inclusive=True
    )


def score_throughput(all_counts: list[float], window_counts: list[float]) -> float:
    """Score throughput trend vs historical baseline.

    Baseline score = 70.  Historical baseline = first half of all_counts.
    +10% vs baseline → +5 pts (cap 100).
    -10% vs baseline → -8 pts (floor 0).

    Identical logic to squad_health.py _score_throughput_window.
    """
    if not all_counts or not window_counts:
        return 70.0

    split = max(1, len(all_counts) // 2)
    hist_avg = sum(all_counts[:split]) / split
    window_avg = sum(window_counts) / len(window_counts)

    if hist_avg == 0:
        return 70.0

    pct = (window_avg - hist_avg) / hist_avg * 100.0
    if pct >= 0:
        return min(100.0, 70.0 + (pct / 10.0) * 5.0)
    return max(0.0, 70.0 + (pct / 10.0) * 8.0)


def score_aging(open_df: pd.DataFrame) -> float:
    """Score open-issue aging.

    Caller MUST ensure open_df contains a `dias_parado` column (days since
    created for each open issue).  compute_aging creates this internally;
    callers that pre-compute it can pass the slice directly.

    score = 100 − (pct_red × 80 + pct_yellow × 30), floor 0.
    Red    = dias_parado > 30
    Yellow = 7 ≤ dias_parado ≤ 30

    Identical logic to squad_health.py _score_aging.
    """
    if open_df.empty:
        return 100.0

    total = len(open_df)
    n_red = int((open_df["dias_parado"] > 30).sum())
    n_yellow = int(((open_df["dias_parado"] >= 7) & (open_df["dias_parado"] <= 30)).sum())

    pct_red = n_red / total
    pct_yellow = n_yellow / total

    return max(0.0, 100.0 - (pct_red * 80.0 + pct_yellow * 30.0))


def metric_status(score: float) -> tuple[str, str]:
    """Return (emoji, label) for a metric score.

    🟢 Boa    : score ≥ 70
    🟡 Atenção: 50 ≤ score < 70
    🔴 Crítica : score < 50
    """
    if score >= 70:
        return "🟢", "Boa"
    if score >= 50:
        return "🟡", "Atenção"
    return "🔴", "Crítica"


def health_status_label(score: float) -> str:
    """Return a health label string.

    Excelente : score ≥ 90
    Boa       : score ≥ 70
    Atenção   : score ≥ 50
    Crítica   : score < 50
    """
    if score >= 90:
        return "Excelente"
    if score >= 70:
        return "Boa"
    if score >= 50:
        return "Atenção"
    return "Crítica"


# ────────────────────────────────────────────────────────────────────────────
# 6. Squad Health Score (DataFrame-accepting version)
# ────────────────────────────────────────────────────────────────────────────

def squad_health_score(
    issues_df: pd.DataFrame,
    transitions_df: Optional[pd.DataFrame] = None,
) -> dict:
    """Compute Squad Health Score (all teams, window = last 3 available months).

    transitions_df is accepted for future extensibility; it is not yet used in
    the scoring calculation.

    Missing-data policy per metric:
      Lead Time, MTTR : fall back to 50 (neutral) when the window has no data.
      CFR             : EXCLUDED from scoring when cfr_val is None (no GMUDs
                        with data_implantacao in the window).  Its 15% weight is
                        redistributed proportionally among the other 4 metrics.
                        See the inline comment in the implementation for the full
                        rationale.  cfr_excluded=True is returned in the dict so
                        the UI can label the CFR chip as "Sem dados".

    Returns
    -------
    dict with:
      score              : float 0–100
      status             : str (Excelente / Boa / Atenção / Crítica)
      trend              : str (↑ Subindo / → Estável / ↓ Caindo / → Sem histórico)
      metrics            : dict — per-metric breakdown
                            keys: lead_time, throughput, aging, mttr, cfr
                            each: {label, score, status, emoji, value, unit}
      impacts            : list[dict] — top-3 score drivers
                            each: {key, label, delta_points}
      window             : list[str] — DORA months used for the current window
      prev_score         : float|None — health score for the previous window
      current_dora_month : str — latest month with LT or MTTR data
      current_month_dora : dict — aggregate_metrics_by_month result for that month
    """
    df = prepare_df(issues_df)

    summary = calculate_metrics_summary(df)

    # DORA month windows
    dora_months = sorted(summary["year_month"].unique().tolist()) if not summary.empty else []
    if not dora_months:
        return {
            "score": 0.0,
            "status": "Crítica",
            "trend": "→ Sem dados",
            "metrics": {},
            "impacts": [],
            "window": [],
            "prev_score": None,
            "current_dora_month": "",
            "current_month_dora": {},
        }

    cur_dora_win = dora_months[-3:]
    prev_dora_win = dora_months[-6:-3]

    # Throughput windows (based on resolutiondate, not created)
    resolved = df[df["is_resolved"]].copy()
    if not resolved.empty:
        resolved["res_month"] = resolved["resolutiondate"].dt.to_period("M").astype(str)
        tp_series = resolved.groupby("res_month").size().sort_index()
    else:
        tp_series = pd.Series(dtype=float)

    tp_dict = tp_series.to_dict()
    tp_months_all = sorted(tp_dict.keys())
    tp_months_closed = tp_months_all[:-1] if len(tp_months_all) > 1 else tp_months_all
    all_tp_counts = [float(tp_dict[m]) for m in tp_months_closed]

    cur_tp_win = tp_months_closed[-3:]
    prev_tp_win = tp_months_closed[-6:-3]
    cur_tp_counts = [float(tp_dict.get(m, 0)) for m in cur_tp_win]
    prev_tp_counts = [float(tp_dict.get(m, 0)) for m in prev_tp_win]

    # Aging snapshot
    open_issues = df[~df["is_resolved"]].copy()
    today_ts = pd.Timestamp(datetime.date.today())
    if not open_issues.empty:
        open_issues["dias_parado"] = (today_ts - open_issues["created"]).dt.days.astype(float)
    aging_sc = score_aging(open_issues)

    # DORA averages for current window
    def _dora_avg(months: list[str]) -> dict:
        lt_vals, mttr_vals, cfr_vals = [], [], []
        for m in months:
            agg = aggregate_metrics_by_month(summary, m)
            if not agg:
                continue
            lt = agg.get("lead_time_days")
            mt = agg.get("mttr_hours")
            cf = agg.get("cfr_percent")
            if lt is not None and not (isinstance(lt, float) and math.isnan(lt)):
                lt_vals.append(float(lt))
            if mt is not None and not (isinstance(mt, float) and math.isnan(mt)):
                mttr_vals.append(float(mt))
            if cf is not None and not (isinstance(cf, float) and math.isnan(cf)):
                cfr_vals.append(float(cf))
        return {
            "lead_time_days": sum(lt_vals) / len(lt_vals) if lt_vals else None,
            "mttr_hours":     sum(mttr_vals) / len(mttr_vals) if mttr_vals else None,
            "cfr_percent":    sum(cfr_vals) / len(cfr_vals) if cfr_vals else None,
        }

    dora_cur = _dora_avg(cur_dora_win)
    lt_val   = dora_cur["lead_time_days"]
    mttr_val = dora_cur["mttr_hours"]
    cfr_val  = dora_cur["cfr_percent"]

    lt_sc   = score_lead_time(lt_val)   if lt_val   is not None else 50.0
    mttr_sc = score_mttr(mttr_val)      if mttr_val is not None else 50.0
    tp_sc   = score_throughput(all_tp_counts, cur_tp_counts)

    # CFR: when there is no data (no GMUDs with data_implantacao), redistribute
    # its 15% weight proportionally among the other 4 metrics rather than
    # assigning a silent 50 (neutral).  A silent 50 makes the wrong claim that
    # the team is "average" at CFR; the truth is we simply cannot measure it yet.
    # Redistribution means "score the team on what we can measure, at the right
    # relative weights."  cfr_excluded=True is surfaced in the returned dict so
    # the UI can label the CFR chip as "Sem dados" instead of a coloured score.
    cfr_sc = score_cfr(cfr_val) if cfr_val is not None else None
    cfr_excluded = cfr_sc is None

    w = SQUAD_HEALTH_WEIGHTS
    active_keys = [k for k in w if not (k == "cfr" and cfr_excluded)]
    total_w = sum(w[k] for k in active_keys)
    active_scores = {
        "lead_time": lt_sc, "throughput": tp_sc, "aging": aging_sc, "mttr": mttr_sc,
    }
    if not cfr_excluded:
        active_scores["cfr"] = cfr_sc
    score = sum(active_scores[k] * w[k] for k in active_keys) / total_w

    # Trend & impacts
    prev_score: Optional[float] = None
    impacts: list[dict] = []
    cur_scores = {k: active_scores[k] for k in active_keys}

    if prev_dora_win:
        dora_prev  = _dora_avg(prev_dora_win)
        lt_prev    = dora_prev["lead_time_days"]
        mttr_prev  = dora_prev["mttr_hours"]
        cfr_prev   = dora_prev["cfr_percent"]

        prev_scores: dict[str, float] = {
            "lead_time":  score_lead_time(lt_prev)   if lt_prev   is not None else lt_sc,
            "throughput": score_throughput(all_tp_counts, prev_tp_counts),
            "aging":      aging_sc,
            "mttr":       score_mttr(mttr_prev)       if mttr_prev is not None else mttr_sc,
        }
        if not cfr_excluded:
            prev_scores["cfr"] = score_cfr(cfr_prev) if cfr_prev is not None else cfr_sc

        prev_score = sum(prev_scores[k] * w[k] for k in active_keys) / total_w

        labels = {
            "lead_time": "Lead Time", "throughput": "Throughput",
            "aging": "Aging", "mttr": "MTTR", "cfr": "CFR",
        }
        for k in active_keys:
            delta = (cur_scores[k] - prev_scores[k]) * w[k]
            if abs(delta) >= 0.5:
                impacts.append({"key": k, "label": labels[k], "delta_points": round(delta, 1)})
        impacts.sort(key=lambda d: abs(d["delta_points"]), reverse=True)
        impacts = impacts[:3]

        diff = score - prev_score
        if diff > SQUAD_HEALTH_TREND_THRESHOLD:
            trend = "↑ Subindo"
        elif diff < -SQUAD_HEALTH_TREND_THRESHOLD:
            trend = "↓ Caindo"
        else:
            trend = "→ Estável"
    else:
        trend = "→ Sem histórico"

    # Per-metric breakdown
    def _m(label, s, value, unit):
        emoji, status = metric_status(s)
        return {
            "label": label,
            "score": round(s, 1),
            "status": status,
            "emoji": emoji,
            "value": value,
            "unit": unit,
        }

    tp_avg = sum(cur_tp_counts) / len(cur_tp_counts) if cur_tp_counts else None
    n_red = int((open_issues["dias_parado"] > 30).sum()) if not open_issues.empty and "dias_parado" in open_issues.columns else 0

    metrics = {
        "lead_time":  _m("Lead Time",  lt_sc,    lt_val,   "dias úteis"),
        "throughput": _m("Throughput", tp_sc,    tp_avg,   "itens/mês"),
        "aging":      _m("Aging",      aging_sc, float(n_red), "itens >30d"),
        "mttr":       _m("MTTR",       mttr_sc,  mttr_val, "horas"),
        "cfr": (
            _m("CFR", cfr_sc, cfr_val, "%") if not cfr_excluded
            else {"label": "CFR", "score": None, "status": "Sem dados",
                  "emoji": "⚪", "value": None, "unit": "%"}
        ),
    }

    # Current-month DORA: latest month with LT or MTTR data
    current_dora_month = dora_months[-1]
    for _mo in reversed(dora_months):
        _a = aggregate_metrics_by_month(summary, _mo)
        _lt = _a.get("lead_time_days")
        _mt = _a.get("mttr_hours")
        _lt_ok = _lt is not None and not (isinstance(_lt, float) and math.isnan(_lt))
        if _lt_ok or _mt is not None:
            current_dora_month = _mo
            break
    current_month_dora = aggregate_metrics_by_month(summary, current_dora_month)

    return {
        "score":              round(score, 1),
        "status":             health_status_label(score),
        "trend":              trend,
        "metrics":            metrics,
        "impacts":            impacts,
        "window":             cur_dora_win,
        "prev_score":         round(prev_score, 1) if prev_score is not None else None,
        "current_dora_month": current_dora_month,
        "current_month_dora": current_month_dora,
        "cfr_excluded":       cfr_excluded,
    }


# ────────────────────────────────────────────────────────────────────────────
# Shared diagnostic helpers
# ────────────────────────────────────────────────────────────────────────────

def diagnose_status_concentration(
    open_df: pd.DataFrame,
    ratio_threshold: float = 2.0,
) -> Optional[str]:
    """Return the name of the non-terminal status that holds a disproportionate
    share of open items, or None when no bottleneck is detected.

    A status is a bottleneck when its item count exceeds
    ratio_threshold × the mean count across all non-terminal statuses.

    Parameters
    ----------
    open_df         : DataFrame of open issues with a 'status' column.
                      Must already be filtered to the desired team/type.
    ratio_threshold : multiplier against the mean (default 2.0 = >2×).
    """
    if open_df.empty:
        return None
    sc = open_df.groupby("status").size()
    active = [s for s in sc.index if s.strip().lower() not in TERMINAL_STATUSES]
    if len(active) < 2:
        return None
    bk = max(active, key=lambda s: sc.get(s, 0))
    mean_sc = sum(sc.get(s, 0) for s in active) / len(active)
    if mean_sc > 0 and sc.get(bk, 0) / mean_sc > ratio_threshold:
        return bk
    return None


# ────────────────────────────────────────────────────────────────────────────
# Throughput diagnostic rules
# ────────────────────────────────────────────────────────────────────────────

def build_throughput_diagnostics(
    closed_list: list[dict],
    df: pd.DataFrame,
    team: Optional[str],
    pred: dict,
    *,
    today: Optional[datetime.date] = None,
    team_label: str = "Todos",
    period: str = "",
) -> list:
    """Interpret throughput data and return list[InsightEvent].

    Three rules, evaluated independently — each fires at most once:
      Rule 1 (Aging × TP):    compares last two closed months and current aging state.
      Rule 2 (Gargalo):       non-terminal status with > 2× mean volume of open items.
      Rule 3 (Predictability): fires when pred["label"] == "Baixa".

    Parameters
    ----------
    closed_list : list[dict]  — tp["closed"] from compute_throughput(); each dict
                                 has {"month": "YYYY-MM", "count": int, "label": str}
    df          : DataFrame   — issues_raw (raw or already prepared; idempotent)
    team        : str|None    — active team filter; None = all teams
    pred        : dict        — from compute_predictability(); requires key "label"
    today       : date|None   — reference date for aging (defaults to date.today())
    team_label  : str         — display label for the team (for event metadata)
    period      : str         — YYYY-MM period string (for event IDs and metadata)

    Returns
    -------
    list[InsightEvent] — one insight+recommendation pair per fired rule.
    An empty list means no rule fired.
    """
    from insights import InsightEvent

    df = prepare_df(df)
    events: list[InsightEvent] = []
    _ctr: dict[str, int] = {}

    def _nid(cat: str, sev: str) -> str:
        base = f"{cat}_{sev}_{period}"
        n = _ctr.get(base, 0)
        _ctr[base] = n + 1
        return base if n == 0 else f"{base}_{n}"

    def _mk(cat, sev, layer, title, desc, evidence, related=None):
        return InsightEvent(
            id=_nid(cat, sev),
            severity=sev,
            category=cat,
            layer=layer,
            title=title,
            description=desc,
            evidence=evidence or {},
            related_ids=related or [],
            team=team_label,
            period=period,
        )

    # Rule 1: TP direction vs. previous closed month + current aging state.
    # "Aging OK"  = <20% of open items older than 30 days.
    # "Aging bad" = >30% of open items older than 30 days.
    if len(closed_list) >= 2:
        tp_cur  = closed_list[-1]["count"]
        tp_prev = closed_list[-2]["count"]
        aging = compute_aging(df, team=team, today=today)
        pct_crit = (
            (aging["bands"]["30–60d"] + aging["bands"]["60+d"]) / aging["total_open"]
            if aging["total_open"] > 0 else 0.0
        )
        if tp_cur > tp_prev and pct_crit < 0.20:
            ins = _mk(
                "throughput", "info", "insight",
                "As entregas aceleraram e o backlog está saudável",
                f"O time entregou {tp_cur} itens — mais que os {tp_prev} do mês anterior — "
                "e o backlog continua enxuto. Bom sinal de ritmo sustentável.",
                {"tp_cur": tp_cur, "tp_prev": tp_prev, "pct_crit": pct_crit},
            )
            rec = _mk(
                "throughput", "info", "recommendation",
                "Manter cadência de revisão",
                "Continue revisando os itens parados com regularidade — está funcionando.",
                {},
                related=[ins.id],
            )
            events.extend([ins, rec])
        elif tp_cur < tp_prev and pct_crit > 0.30:
            ins = _mk(
                "throughput", "high", "insight",
                "As entregas caíram e o backlog está envelhecendo",
                f"O time entregou {tp_cur} itens — menos que os {tp_prev} do mês anterior. "
                "Ao mesmo tempo, mais de 30% dos itens abertos já está parado há mais de 30 dias. "
                "Um pode estar causando o outro.",
                {"tp_cur": tp_cur, "tp_prev": tp_prev, "pct_crit": pct_crit},
            )
            rec = _mk(
                "throughput", "info", "recommendation",
                "Priorizar itens mais antigos",
                "Atacar os itens mais antigos primeiro pode ajudar a destravar o fluxo "
                "e voltar ao ritmo de antes.",
                {},
                related=[ins.id],
            )
            events.extend([ins, rec])

    # Rule 2: bottleneck — delegates to shared diagnose_status_concentration().
    open_now = df if team is None else df[df["team"] == team]
    open_now = open_now[~open_now["is_resolved"]]
    bk = diagnose_status_concentration(open_now)
    if bk is not None:
        ins = _mk(
            "throughput", "high", "insight",
            f"Muitos itens estão travados em '{bk}'",
            f"A maioria dos itens em aberto está concentrada em '{bk}', "
            "o que está represando as entregas do time.",
            {"bottleneck_status": bk},
        )
        rec = _mk(
            "throughput", "info", "recommendation",
            f"Investigar o que está acumulando em '{bk}'",
            f"Vale conversar com o time sobre o que está acumulando em '{bk}'. "
            "Uma sessão rápida pode destravar vários itens de uma vez.",
            {},
            related=[ins.id],
        )
        events.extend([ins, rec])

    # Rule 3: low predictability.
    if pred.get("label") == "Baixa":
        ins = _mk(
            "throughput", "medium", "insight",
            "Volume de entregas muito irregular",
            "As entregas mensais estão variando muito — uns meses são ótimos, outros caem bastante. "
            "Isso torna difícil planejar prazos com confiança.",
            {"pred_label": "Baixa"},
        )
        rec = _mk(
            "throughput", "info", "recommendation",
            "Entender a causa da instabilidade antes de fechar prazos",
            "Antes de assumir compromissos com outras áreas, vale investigar "
            "por que o volume oscila tanto. Pode ser sazonalidade, gargalos pontuais ou mudança de prioridade.",
            {},
            related=[ins.id],
        )
        events.extend([ins, rec])

    return events


# ────────────────────────────────────────────────────────────────────────────
# Aging diagnostic rules
# ────────────────────────────────────────────────────────────────────────────

# Minimum change needed to trigger the trend rule (avoids noise on tiny deltas).
_AGING_TREND_AGE_DELTA        = 1.0   # days
_AGING_TREND_CRIT_DELTA       = 0.02  # fraction (2 pp)
_AGING_STILL_CRITICAL_THRESHOLD = 0.50  # pct_crit above which "improved" gets a caveat


def build_aging_diagnostics(
    df: pd.DataFrame,
    team: Optional[str],
    issuetype: Optional[str],
    *,
    today: Optional[datetime.date] = None,
    prev_aging: Optional[dict] = None,
    team_label: str = "Todos",
    period: str = "",
) -> list:
    """Interpret aging data and return list[InsightEvent].

    Three rules, evaluated independently — each fires at most once:
      Rule 1 (Gargalo):         non-terminal status with > 2× mean open-item count.
      Rule 2 (Tendência):       compares current avg_age / pct_critical vs. prev_aging.
      Rule 3 (Sem Movimentação): > 20% of open items had no update in the last 14 days.

    Parameters
    ----------
    df         : issues_raw DataFrame (raw or already prepared; idempotent).
    team       : team filter; None = all teams.
    issuetype  : issue-type filter; None = all types.
    today      : reference date for age calculations (defaults to date.today()).
    prev_aging : optional dict from a previous compute_aging() call used for Rule 2.
                 When None, the trend rule is silently skipped.
                 When avg_age < 0 (migration artifact), the rule is also skipped.
    team_label : str — display label for the team (for event metadata).
    period     : str — YYYY-MM period string (for event IDs and metadata).

    Returns
    -------
    list[InsightEvent] — one insight+recommendation pair per fired rule.
    An empty list means no rule fired.
    """
    from insights import InsightEvent

    df = prepare_df(df)
    events: list[InsightEvent] = []
    _ctr: dict[str, int] = {}

    def _nid(cat: str, sev: str) -> str:
        base = f"{cat}_{sev}_{period}"
        n = _ctr.get(base, 0)
        _ctr[base] = n + 1
        return base if n == 0 else f"{base}_{n}"

    def _mk(cat, sev, layer, title, desc, evidence, related=None):
        return InsightEvent(
            id=_nid(cat, sev),
            severity=sev,
            category=cat,
            layer=layer,
            title=title,
            description=desc,
            evidence=evidence or {},
            related_ids=related or [],
            team=team_label,
            period=period,
        )

    cur = compute_aging(df, team=team, issuetype=issuetype, today=today)
    pct_crit = (
        (cur["bands"]["30–60d"] + cur["bands"]["60+d"]) / cur["total_open"]
        if cur["total_open"] > 0 else 0.0
    )

    # Rule 1: bottleneck — delegates to shared diagnose_status_concentration().
    open_now = df if team is None else df[df["team"] == team]
    if issuetype is not None:
        open_now = open_now[open_now["issuetype"] == issuetype]
    open_now = open_now[~open_now["is_resolved"]]
    bk = diagnose_status_concentration(open_now)
    if bk is not None:
        ins = _mk(
            "aging", "high", "insight",
            f"Itens parados em '{bk}' há muito tempo",
            f"A maioria dos itens abertos está acumulada em '{bk}' — "
            "isso sugere um ponto de bloqueio no processo.",
            {"bottleneck_status": bk},
        )
        rec = _mk(
            "aging", "info", "recommendation",
            f"Olhar o que está prendendo itens em '{bk}'",
            f"Vale investigar por que os itens estão acumulando em '{bk}'. "
            "Pode ser falta de revisor, dependência externa ou item sem dono claro.",
            {},
            related=[ins.id],
        )
        events.extend([ins, rec])

    # Rule 2: aging trend vs. previous snapshot.
    # Guard: skip when prev_aging is absent or has a negative avg_age (migration
    # artifact — created date is newer than the snapshot reference date).
    if (
        prev_aging is not None
        and cur["total_open"] > 0
        and prev_aging.get("avg_age", -1) >= 0
    ):
        if "pct_critical" in prev_aging:
            prev_pct_crit = prev_aging["pct_critical"]
        else:
            prev_pct_crit = (
                (prev_aging["bands"]["30–60d"] + prev_aging["bands"]["60+d"])
                / prev_aging["total_open"]
                if prev_aging.get("total_open", 0) > 0 else 0.0
            )
        age_delta  = cur["avg_age"]  - prev_aging["avg_age"]
        crit_delta = pct_crit - prev_pct_crit

        worsened = (
            age_delta  >  _AGING_TREND_AGE_DELTA
            or crit_delta > _AGING_TREND_CRIT_DELTA
        )
        improved = (
            age_delta  < -_AGING_TREND_AGE_DELTA
            and crit_delta < _AGING_TREND_CRIT_DELTA
        )

        if worsened:
            if bk is not None:
                desc = (
                    f"Os itens estão levando mais tempo que no mês passado pra progredir — "
                    f"e boa parte está acumulada em '{bk}'."
                )
            else:
                desc = (
                    "Os itens estão levando mais tempo que no mês passado pra progredir. "
                    "Se isso continuar, vai impactar as entregas."
                )
            ins = _mk(
                "aging", "high", "insight",
                "Itens abertos demorando mais pra avançar",
                desc,
                {"age_delta": age_delta, "crit_delta": crit_delta, "bottleneck_status": bk},
            )
            rec = _mk(
                "aging", "info", "recommendation",
                "Atacar os itens mais antigos agora",
                "Olhar os itens mais antigos e entender por que estão parados "
                "pode evitar que o problema se agrave no próximo mês.",
                {},
                related=[ins.id],
            )
            events.extend([ins, rec])
        elif improved:
            if pct_crit > _AGING_STILL_CRITICAL_THRESHOLD:
                ins = _mk(
                    "aging", "medium", "insight",
                    "Backlog melhorou, mas ainda preocupa",
                    "O tempo médio dos itens em aberto caiu em relação ao mês passado — bom sinal. "
                    "Mas mais da metade dos itens ainda está acumulada há muito tempo.",
                    {"age_delta": age_delta, "pct_crit": pct_crit},
                )
                rec = _mk(
                    "aging", "info", "recommendation",
                    "Manter o foco nos itens mais antigos",
                    "Continue priorizando os itens mais antigos. "
                    "O ritmo melhorou, mas ainda tem trabalho pra limpar o backlog.",
                    {},
                    related=[ins.id],
                )
            else:
                ins = _mk(
                    "aging", "info", "insight",
                    "Backlog está melhorando",
                    "Os itens estão avançando mais rápido que no mês passado — boa tendência.",
                    {"age_delta": age_delta, "pct_crit": pct_crit},
                )
                rec = _mk(
                    "aging", "info", "recommendation",
                    "Manter ritmo de revisão",
                    "Continue priorizando a revisão de itens parados — está funcionando.",
                    {},
                    related=[ins.id],
                )
            events.extend([ins, rec])

    # Rule 3: sem movimentação — > 20% of open items with no update in 14 days.
    sem_mov = cur["sem_movimento"]
    if sem_mov is not None and cur["total_open"] > 0:
        if sem_mov / cur["total_open"] > 0.20:
            ins = _mk(
                "aging", "medium", "insight",
                "Vários itens sem nenhuma atualização recente",
                f"Mais de 20% dos itens abertos ({sem_mov} de {cur['total_open']}) "
                "não receberam nenhuma atualização nos últimos 14 dias. "
                "Podem estar esquecidos ou bloqueados.",
                {"sem_movimento": sem_mov, "total_open": cur["total_open"]},
            )
            rec = _mk(
                "aging", "info", "recommendation",
                "Verificar um por um: ainda são prioridade?",
                "Vale checar cada item parado: ainda é prioridade? "
                "Quem é o responsável? Há algum bloqueio que precisa ser resolvido?",
                {},
                related=[ins.id],
            )
            events.extend([ins, rec])

    return events


# ────────────────────────────────────────────────────────────────────────────
# DORA diagnostic rules
# ────────────────────────────────────────────────────────────────────────────

_DORA_TITLES_WORSENED: dict[str, str] = {
    "lead_time_days":       "Entregas estão demorando mais pra chegar",
    "deploy_freq_interval": "Deploys acontecendo com menos frequência",
    "mttr_hours":           "Incidentes demorando mais pra ser resolvidos",
    "cfr_percent":          "Mais deploys estão causando problemas",
}

_DORA_TITLES_IMPROVED: dict[str, str] = {
    "lead_time_days":       "Entregas chegando mais rápido",
    "deploy_freq_interval": "Deploy ficando mais frequente",
    "mttr_hours":           "Incidentes sendo resolvidos mais rápido",
    "cfr_percent":          "Deploys com menos falhas",
}

_DORA_RECS_WORSENED: dict[str, str] = {
    "lead_time_days": (
        "Vale olhar onde o processo está travando — aprovações lentas, "
        "filas de revisão ou retrabalho costumam ser os culpados."
    ),
    "deploy_freq_interval": (
        "Vale entender se os deploys ficaram menos frequentes por falta de prioridade "
        "ou se o time está sendo mais cauteloso depois de incidentes."
    ),
    "mttr_hours": (
        "Vale revisar o processo de resposta a incidentes: o time está sendo acionado rápido? "
        "Existe runbook claro? As causas raiz estão sendo endereçadas?"
    ),
    "cfr_percent": (
        "Vale olhar as últimas mudanças que falharam em busca de padrão — "
        "tipo de código, área do sistema, ou falta de testes."
    ),
}


def build_dora_diagnostics(
    current: dict,
    prev: Optional[dict],
    *,
    team_label: str = "Todos",
    period: str = "",
) -> list:
    """Interpret DORA metric band changes and return list[InsightEvent].

    Three rules, evaluated independently:
      Rule 1 (Faixa em deterioração): any metric's DORA band worsened vs. prev.
      Rule 2 (Faixa em melhoria):     any metric's DORA band improved vs. prev.
      Rule 3 (CFR × Deploy Freq):     CFR raw value went up AND deploy_freq_interval
                                       went up (fewer deploys) in the same period.

    Parameters
    ----------
    current    : dict with keys lead_time_days, deploy_freq_interval, mttr_hours,
                 cfr_percent. None values mean no data for that metric — the rule
                 for that metric is silently skipped.
    prev       : same shape for the previous closed month, or None to skip all rules.
    team_label : str — display label for the team (for event metadata).
    period     : str — YYYY-MM period string (for event IDs and metadata).

    Returns
    -------
    list[InsightEvent] — one insight+recommendation pair per fired rule.
    """
    from insights import InsightEvent

    events: list[InsightEvent] = []
    _ctr: dict[str, int] = {}

    if prev is None:
        return events

    # Key → category mapping
    _KEY_CATEGORY = {
        "lead_time_days":       "lead_time",
        "deploy_freq_interval": "deployment",
        "mttr_hours":           "mttr",
        "cfr_percent":          "cfr",
    }

    def _nid(cat: str, sev: str) -> str:
        base = f"{cat}_{sev}_{period}"
        n = _ctr.get(base, 0)
        _ctr[base] = n + 1
        return base if n == 0 else f"{base}_{n}"

    def _mk(cat, sev, layer, title, desc, evidence, related=None):
        return InsightEvent(
            id=_nid(cat, sev),
            severity=sev,
            category=cat,
            layer=layer,
            title=title,
            description=desc,
            evidence=evidence or {},
            related_ids=related or [],
            team=team_label,
            period=period,
        )

    # Rules 1 & 2: one entry per metric whose band changed.
    for key in ("lead_time_days", "deploy_freq_interval", "mttr_hours", "cfr_percent"):
        cur_v = current.get(key)
        prv_v = prev.get(key)
        if cur_v is None or prv_v is None:
            continue
        cur_band = dora_band(key, cur_v)
        prv_band = dora_band(key, prv_v)
        if cur_band == "N/A" or prv_band == "N/A":
            continue
        cur_rank = _LEVEL_RANK[cur_band]
        prv_rank = _LEVEL_RANK[prv_band]
        cat = _KEY_CATEGORY[key]
        if cur_rank > prv_rank:
            # Severity based on current band
            if cur_band == "Low":
                sev = "critical"
            elif cur_band == "Medium":
                sev = "high"
            else:
                sev = "medium"
            _desc_worsened: dict[str, str] = {
                "lead_time_days": (
                    f"Cada entrega está levando mais tempo do início ao fim — "
                    f"o Lead Time passou de {prv_band} para {cur_band}."
                ),
                "deploy_freq_interval": (
                    f"O time está deployando com menos frequência — "
                    f"a frequência passou de {prv_band} para {cur_band}."
                ),
                "mttr_hours": (
                    f"Quando algo quebra, está demorando mais pra voltar ao normal — "
                    f"o MTTR passou de {prv_band} para {cur_band}."
                ),
                "cfr_percent": (
                    f"Mais deploys estão causando problemas — "
                    f"a taxa de falha passou de {prv_band} para {cur_band}."
                ),
            }
            ins = _mk(
                cat, sev, "insight",
                _DORA_TITLES_WORSENED[key],
                _desc_worsened[key],
                {"key": key, "cur_band": cur_band, "prv_band": prv_band,
                 "cur_v": cur_v, "prv_v": prv_v},
            )
            rec = _mk(
                cat, "info", "recommendation",
                f"Investigar {_DORA_TITLES_WORSENED[key].lower()}",
                _DORA_RECS_WORSENED[key],
                {},
                related=[ins.id],
            )
            events.extend([ins, rec])
        elif cur_rank < prv_rank:
            _desc_improved: dict[str, str] = {
                "lead_time_days": (
                    f"Lead Time melhorou — estava {prv_band} e agora está {cur_band}. "
                    "As entregas estão chegando mais rápido."
                ),
                "deploy_freq_interval": (
                    f"Frequência de deploy melhorou de {prv_band} para {cur_band}. "
                    "O time está entregando com mais regularidade."
                ),
                "mttr_hours": (
                    f"MTTR melhorou de {prv_band} para {cur_band}. "
                    "Incidentes estão sendo resolvidos mais rápido."
                ),
                "cfr_percent": (
                    f"CFR melhorou de {prv_band} para {cur_band} — "
                    "menos deploys estão causando problemas."
                ),
            }
            ins = _mk(
                cat, "info", "insight",
                _DORA_TITLES_IMPROVED[key],
                _desc_improved[key],
                {"key": key, "cur_band": cur_band, "prv_band": prv_band,
                 "cur_v": cur_v, "prv_v": prv_v},
            )
            rec = _mk(
                cat, "info", "recommendation",
                "Entender o que mudou e manter essa prática",
                "Vale registrar o que contribuiu pra essa melhora e garantir que continue.",
                {},
                related=[ins.id],
            )
            events.extend([ins, rec])

    # Rule 3: CFR up AND deploy interval up (fewer deploys) simultaneously.
    # Uses raw values — fires even when neither metric crossed a band boundary.
    cur_cfr  = current.get("cfr_percent")
    prv_cfr  = prev.get("cfr_percent")
    cur_freq = current.get("deploy_freq_interval")
    prv_freq = prev.get("deploy_freq_interval")
    if (cur_cfr is not None and prv_cfr is not None
            and cur_freq is not None and prv_freq is not None
            and cur_cfr > prv_cfr and cur_freq > prv_freq):
        ins = _mk(
            "cfr", "medium", "insight",
            "Time deployando menos depois de um período com mais falhas",
            "A frequência de deploys caiu no mesmo período em que a taxa de falhas subiu. "
            "O time pode estar com pé no freio depois de incidentes recentes.",
            {"cur_cfr": cur_cfr, "prv_cfr": prv_cfr, "cur_freq": cur_freq, "prv_freq": prv_freq},
        )
        rec = _mk(
            "cfr", "info", "recommendation",
            "Cautela vs. velocidade: vale conversar com o time",
            "Vale conversar com o time: a cautela está ajudando ou só atrasando entregas? "
            "Às vezes investir em mais testes automáticos é melhor que reduzir a frequência de deploy.",
            {},
            related=[ins.id],
        )
        events.extend([ins, rec])

    return events
