"""
squad_health.py — Squad Health Score

Reuses without duplicating:
  - metrics.py   : calculate_metrics_summary, aggregate_metrics_by_month
  - loader.py    : load_jira_issues_from_csv
  - Throughput   : monthly item counts by resolution date (same logic as pages/throughput.py)
  - Aging        : open-issue age from creation date (same logic as pages/aging.py)

Default filter: Time = Todos, window = last 3 available months.
"""

import datetime

import pandas as pd

from loader import load_jira_issues_from_csv
from metrics import aggregate_metrics_by_month, calculate_metrics_summary

DATA_PATH = "data/jira_issues_synthetic.csv"

# Final weighted average
WEIGHTS = {
    "lead_time":  0.25,
    "throughput": 0.20,
    "aging":      0.25,
    "mttr":       0.15,
    "cfr":        0.15,
}

# Points above which a trend change is considered meaningful
TREND_THRESHOLD = 5.0


# ── Generic normalization ────────────────────────────────────────────────────

def _score_lower_better(
    v: float,
    elite_hi: float,
    high_hi: float,
    medium_hi: float,
    elite_inclusive: bool = False,
) -> float:
    """
    Normalize a lower-is-better metric to [0, 100] using DORA-style bands.

    Band mapping:
      Elite  → 90–100  (best value in band = 100, boundary = 90)
      High   → 70–89
      Medium → 50–69
      Low    → 0–49    (boundary = 49, decays to 0 over a range equal to medium_hi)

    elite_inclusive: True when Elite is "≤ elite_hi" (CFR), False for "< elite_hi".
    """
    in_elite = (v <= elite_hi) if elite_inclusive else (v < elite_hi)

    if in_elite:
        # v=0 → 100; v→elite_hi → 90
        base = elite_hi if elite_hi > 0 else 1.0
        return 100.0 - (v / base) * 10.0

    if v <= high_hi:
        # elite_hi → 89; high_hi → 70
        return 89.0 - ((v - elite_hi) / (high_hi - elite_hi)) * 19.0

    if v <= medium_hi:
        # high_hi → 69; medium_hi → 50
        return 69.0 - ((v - high_hi) / (medium_hi - high_hi)) * 19.0

    # Low: medium_hi → 49; medium_hi*2 → 0; floor at 0
    return max(0.0, 49.0 - ((v - medium_hi) / medium_hi) * 49.0)


def _score_lead_time(days: float) -> float:
    # Elite < 1d, High 1–7d, Medium 7–30d, Low > 30d  (DORA definition)
    return _score_lower_better(days, elite_hi=1.0, high_hi=7.0, medium_hi=30.0)


def _score_mttr(hours: float) -> float:
    # Elite < 1h, High < 24h, Medium < 168h, Low ≥ 168h
    return _score_lower_better(hours, elite_hi=1.0, high_hi=24.0, medium_hi=168.0)


def _score_cfr(pct: float) -> float:
    # Elite 0–15% (inclusive), High 15–30%, Medium 30–45%, Low > 45%
    return _score_lower_better(
        pct, elite_hi=15.0, high_hi=30.0, medium_hi=45.0, elite_inclusive=True
    )


def _score_throughput_window(
    all_counts: list[float],
    window_counts: list[float],
) -> float:
    """
    Compare window_counts avg vs the historical baseline (first half of all_counts).

    0% change   → 70 pts
    +10% change → +5 pts  (cap at 100)
    -10% change → -8 pts  (floor at 0)
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
    return max(0.0, 70.0 + (pct / 10.0) * 8.0)  # pct is negative, so this subtracts


def _score_aging(open_df: pd.DataFrame) -> float:
    """
    Score based on open-issue age distribution.
    score = 100 − (pct_red × 80 + pct_yellow × 30), floor at 0.
    Red   = dias_parado > 30
    Yellow = 7 ≤ dias_parado ≤ 30
    """
    if open_df.empty:
        return 100.0

    total = len(open_df)
    n_red    = int((open_df["dias_parado"] > 30).sum())
    n_yellow = int(((open_df["dias_parado"] >= 7) & (open_df["dias_parado"] <= 30)).sum())

    pct_red    = n_red    / total
    pct_yellow = n_yellow / total

    return max(0.0, 100.0 - (pct_red * 80.0 + pct_yellow * 30.0))


# ── Status helpers ───────────────────────────────────────────────────────────

def _metric_status(score: float) -> tuple[str, str]:
    """(emoji, label): 🟢 ≥70, 🟡 50–69, 🔴 <50."""
    if score >= 70:
        return "🟢", "Boa"
    if score >= 50:
        return "🟡", "Atenção"
    return "🔴", "Crítica"


def _health_status(score: float) -> str:
    if score >= 90:
        return "Excelente"
    if score >= 70:
        return "Boa"
    if score >= 50:
        return "Atenção"
    return "Crítica"


# ── DORA window aggregation ──────────────────────────────────────────────────

def _dora_avg_for_window(summary_df: pd.DataFrame, months: list[str]) -> dict:
    """
    Average Lead Time, MTTR and CFR across a list of months (all teams combined).
    Uses aggregate_metrics_by_month from metrics.py for the 'Todos' aggregation.
    Returns None for a metric when no data exists for any month in the window.
    """
    lt_vals, mttr_vals, cfr_vals = [], [], []

    for m in months:
        agg = aggregate_metrics_by_month(summary_df, m)
        if not agg:
            continue
        lt = agg.get("lead_time_days")
        mt = agg.get("mttr_hours")
        cf = agg.get("cfr_percent")
        if lt is not None and not pd.isna(lt):
            lt_vals.append(float(lt))
        if mt is not None and not pd.isna(mt):
            mttr_vals.append(float(mt))
        if cf is not None and not pd.isna(cf):
            cfr_vals.append(float(cf))

    return {
        "lead_time_days": sum(lt_vals) / len(lt_vals) if lt_vals else None,
        "mttr_hours":     sum(mttr_vals) / len(mttr_vals) if mttr_vals else None,
        "cfr_percent":    sum(cfr_vals) / len(cfr_vals) if cfr_vals else None,
    }


# ── Main computation ─────────────────────────────────────────────────────────

def compute_squad_health(data_path: str = DATA_PATH) -> dict:
    """
    Compute the Squad Health Score (Time = Todos, window = last 3 months).

    Returns:
        score     : float [0, 100]
        status    : str  (Excelente / Boa / Atenção / Crítica)
        trend     : str  (↑ Subindo / → Estável / ↓ Caindo / → Sem histórico)
        metrics   : dict  — per-metric breakdown (score, status, emoji, value, unit)
        window    : list[str]  — DORA months used for the current window
        prev_score: float | None  — health score for the previous window (for reference)
    """
    df = load_jira_issues_from_csv(data_path)
    summary = calculate_metrics_summary(df)

    # ── DORA months (based on creation date, same as metrics.py) ─────────────
    dora_months = sorted(summary["year_month"].unique().tolist())
    if not dora_months:
        return {
            "score": 0.0, "status": "Crítica", "trend": "→ Sem dados",
            "metrics": {}, "window": [], "prev_score": None,
        }

    cur_dora_win  = dora_months[-3:]   # last 3 months
    prev_dora_win = dora_months[-6:-3] # 3 months before (may be empty)

    # ── Throughput months (based on resolution date, same as pages/throughput.py) ─
    resolved = df[df["is_resolved"]].copy()
    resolved["res_month"] = resolved["resolutiondate"].dt.to_period("M").astype(str)
    tp_series = (
        resolved.groupby("res_month").size().sort_index()
        if not resolved.empty else pd.Series(dtype=float)
    )
    tp_dict       = tp_series.to_dict()
    tp_months_all = sorted(tp_dict.keys())
    # Most recent month is WIP — excluded from every baseline (same as pages/throughput.py).
    tp_months_closed = tp_months_all[:-1] if len(tp_months_all) > 1 else tp_months_all
    all_tp_counts = [float(tp_dict[m]) for m in tp_months_closed]

    cur_tp_win  = tp_months_closed[-3:]
    prev_tp_win = tp_months_closed[-6:-3]
    cur_tp_counts  = [float(tp_dict.get(m, 0)) for m in cur_tp_win]
    prev_tp_counts = [float(tp_dict.get(m, 0)) for m in prev_tp_win]

    # ── Aging snapshot (same logic as pages/aging.py) ─────────────────────────
    open_issues = df[~df["is_resolved"]].copy()
    today = pd.Timestamp(datetime.date.today())
    open_issues["dias_parado"] = (today - open_issues["created"]).dt.days
    aging_score = _score_aging(open_issues)

    # ── DORA scores for current window ────────────────────────────────────────
    dora_cur = _dora_avg_for_window(summary, cur_dora_win)
    lt_val   = dora_cur["lead_time_days"]
    mttr_val = dora_cur["mttr_hours"]
    cfr_val  = dora_cur["cfr_percent"]

    # Fall back to neutral 50 when a metric has no data in the window
    lt_score   = _score_lead_time(lt_val)   if lt_val   is not None else 50.0
    mttr_score = _score_mttr(mttr_val)      if mttr_val is not None else 50.0
    cfr_score  = _score_cfr(cfr_val)        if cfr_val  is not None else 50.0
    tp_score   = _score_throughput_window(all_tp_counts, cur_tp_counts)

    # ── Final weighted score ──────────────────────────────────────────────────
    score = (
        lt_score   * WEIGHTS["lead_time"]
        + tp_score * WEIGHTS["throughput"]
        + aging_score * WEIGHTS["aging"]
        + mttr_score  * WEIGHTS["mttr"]
        + cfr_score   * WEIGHTS["cfr"]
    )

    # ── Trend & impacts: compute score for previous window and compare ────────
    # "impacts" = how many ABSOLUTE health-score points each metric pushed the
    # score up/down vs the previous window (weighted score delta, not metric %).
    # Their sum equals (score − prev_score).
    prev_score: float | None = None
    impacts: list[dict] = []
    cur_scores = {
        "lead_time": lt_score, "throughput": tp_score, "aging": aging_score,
        "mttr": mttr_score, "cfr": cfr_score,
    }
    if prev_dora_win:
        dora_prev = _dora_avg_for_window(summary, prev_dora_win)
        lt_prev   = dora_prev["lead_time_days"]
        mttr_prev = dora_prev["mttr_hours"]
        cfr_prev  = dora_prev["cfr_percent"]

        prev_scores = {
            "lead_time":  _score_lead_time(lt_prev) if lt_prev   is not None else lt_score,
            "throughput": _score_throughput_window(all_tp_counts, prev_tp_counts),
            "aging":      aging_score,
            "mttr":       _score_mttr(mttr_prev)    if mttr_prev is not None else mttr_score,
            "cfr":        _score_cfr(cfr_prev)      if cfr_prev  is not None else cfr_score,
        }

        prev_score = sum(prev_scores[k] * WEIGHTS[k] for k in WEIGHTS)

        labels = {"lead_time": "Lead Time", "throughput": "Throughput",
                  "aging": "Aging", "mttr": "MTTR", "cfr": "CFR"}
        for k in WEIGHTS:
            delta = (cur_scores[k] - prev_scores[k]) * WEIGHTS[k]
            if abs(delta) >= 0.5:
                impacts.append({"key": k, "label": labels[k], "delta_points": round(delta, 1)})
        impacts.sort(key=lambda d: abs(d["delta_points"]), reverse=True)
        impacts = impacts[:3]

        diff = score - prev_score
        if diff > TREND_THRESHOLD:
            trend = "↑ Subindo"
        elif diff < -TREND_THRESHOLD:
            trend = "↓ Caindo"
        else:
            trend = "→ Estável"
    else:
        trend = "→ Sem histórico"

    # ── Per-metric breakdown ──────────────────────────────────────────────────
    def _m(label, s, value, unit):
        emoji, status = _metric_status(s)
        return {"label": label, "score": round(s, 1), "status": status,
                "emoji": emoji, "value": value, "unit": unit}

    tp_avg = sum(cur_tp_counts) / len(cur_tp_counts) if cur_tp_counts else None
    n_red  = int((open_issues["dias_parado"] > 30).sum()) if not open_issues.empty else 0

    metrics = {
        "lead_time":  _m("Lead Time",  lt_score,    lt_val,   "dias úteis"),
        "throughput": _m("Throughput", tp_score,    tp_avg,   "itens/mês"),
        "aging":      _m("Aging",      aging_score, float(n_red), "itens >30d"),
        "mttr":       _m("MTTR",       mttr_score,  mttr_val, "horas"),
        "cfr":        _m("CFR",        cfr_score,   cfr_val,  "%"),
    }

    # Current-month DORA: last month with LT or MTTR data — same criterion as
    # dora_executivo.py's _has_key_data filter, so both pages show the same month.
    current_dora_month = dora_months[-1]
    for _m in reversed(dora_months):
        _a = aggregate_metrics_by_month(summary, _m)
        _lt, _mt = _a.get("lead_time_days"), _a.get("mttr_hours")
        _lt_ok = _lt is not None and not (isinstance(_lt, float) and pd.isna(_lt))
        if _lt_ok or _mt is not None:
            current_dora_month = _m
            break
    current_month_dora = aggregate_metrics_by_month(summary, current_dora_month)

    return {
        "score":              round(score, 1),
        "status":             _health_status(score),
        "trend":              trend,
        "metrics":            metrics,
        "impacts":            impacts,
        "window":             cur_dora_win,
        "prev_score":         round(prev_score, 1) if prev_score is not None else None,
        "current_dora_month": current_dora_month,
        "current_month_dora": current_month_dora,
    }


# ── Streamlit card (shared across pages) ─────────────────────────────────────

def _score_emoji(score: float) -> str:
    """🟢 ≥70, 🟡 50–69, 🔴 <50."""
    if score >= 70:
        return "🟢"
    if score >= 50:
        return "🟡"
    return "🔴"


def _score_color(score: float) -> str:
    if score >= 70:
        return "#15803d"
    if score >= 50:
        return "#ca8a04"
    return "#dc2626"


def _trend_visual(trend: str) -> tuple[str, str, str]:
    """Map internal trend string → (arrow, label, color) for display."""
    if "Subindo" in trend:
        return "↗", "Melhorando", "#15803d"
    if "Caindo" in trend:
        return "↘", "Piorando", "#dc2626"
    if "Sem" in trend:
        return "→", "Sem histórico", "#94a3b8"
    return "→", "Estável", "#64748b"


def render_squad_health(data_path: str = DATA_PATH) -> None:
    """
    Render the Squad Health card. Reusable — call at the top of any page's main().
    Reads from compute_squad_health() (the terminal-validated computation).
    """
    import streamlit as st

    h = compute_squad_health(data_path)

    score = h["score"]
    sc_emoji = _score_emoji(score)
    sc_color = _score_color(score)
    status = h["status"]
    tr_arrow, tr_label, tr_color = _trend_visual(h["trend"])

    font = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif"

    # ── 5 colored indicators ──────────────────────────────────────────────────
    order = ["lead_time", "throughput", "aging", "mttr", "cfr"]
    chips = ""
    for k in order:
        m = h["metrics"].get(k)
        if not m:
            continue
        chips += (
            '<div style="display:flex;align-items:center;gap:6px;">'
            f'<span style="font-size:13px;">{m["emoji"]}</span>'
            f'<span style="font-size:13px;font-weight:600;color:#334155;">{m["label"]}</span>'
            '</div>'
        )

    # ── Principais impactos ───────────────────────────────────────────────────
    if h["impacts"]:
        impact_items = ""
        for imp in h["impacts"]:
            pts = imp["delta_points"]
            color = "#15803d" if pts > 0 else "#dc2626"
            bg = "rgba(21,128,61,0.08)" if pts > 0 else "rgba(220,38,38,0.08)"
            impact_items += (
                f'<span style="font-size:12px;font-weight:700;color:{color};'
                f'background:{bg};padding:3px 9px;border-radius:6px;white-space:nowrap;">'
                f'{pts:+.0f} pontos {imp["label"]}</span>'
            )
        impacts_html = (
            '<div style="font-size:10px;font-weight:700;color:#94a3b8;text-transform:uppercase;'
            'letter-spacing:.07em;margin-bottom:8px;">Principais Impactos</div>'
            f'<div style="display:flex;gap:8px;flex-wrap:wrap;">{impact_items}</div>'
        )
    else:
        impacts_html = (
            '<div style="font-size:10px;font-weight:700;color:#94a3b8;text-transform:uppercase;'
            'letter-spacing:.07em;margin-bottom:8px;">Principais Impactos</div>'
            '<div style="font-size:12px;color:#94a3b8;">Sem janela de comparação anterior.</div>'
        )

    card = (
        f'<div style="background:white;border-radius:14px;padding:20px 24px;margin-bottom:20px;'
        f'box-shadow:0 1px 4px rgba(0,0,0,0.07);border:1px solid #f1f5f9;font-family:{font};">'

        # Top row: score + status + trend  |  5 indicators
        '<div style="display:flex;align-items:center;justify-content:space-between;'
        'flex-wrap:wrap;gap:18px;">'

        '<div style="display:flex;align-items:center;gap:20px;">'
        '<div>'
        '<div style="font-size:10px;font-weight:700;color:#94a3b8;text-transform:uppercase;'
        'letter-spacing:.07em;margin-bottom:2px;">Squad Health</div>'
        f'<div style="font-size:34px;font-weight:800;color:{sc_color};line-height:1;'
        'letter-spacing:-0.5px;">'
        f'{score:.0f}<span style="font-size:18px;color:#cbd5e1;font-weight:700;">/100</span></div>'
        '</div>'
        '<div style="display:flex;flex-direction:column;gap:6px;">'
        f'<span style="font-size:14px;font-weight:700;color:{sc_color};">{sc_emoji} {status}</span>'
        f'<span style="font-size:13px;font-weight:600;color:{tr_color};">{tr_arrow} {tr_label}</span>'
        '</div>'
        '</div>'

        f'<div style="display:flex;align-items:center;gap:18px;flex-wrap:wrap;">{chips}</div>'

        '</div>'

        # Divider + impacts
        '<div style="border-top:1px solid #f1f5f9;margin:16px 0 14px;"></div>'
        f'{impacts_html}'

        '</div>'
    )

    st.html(card)


# ── Test main ────────────────────────────────────────────────────────────────

def main() -> None:
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    result = compute_squad_health()

    win_str = " / ".join(result["window"]) if result["window"] else "N/A"
    prev_str = f"{result['prev_score']:.1f}" if result["prev_score"] is not None else "N/A"

    print("=" * 56)
    print("  SQUAD HEALTH SCORE")
    print("=" * 56)
    print(f"  Janela atual : {win_str}")
    print(f"  Score atual  : {result['score']:.1f} / 100  →  {result['status']}")
    print(f"  Score anterior: {prev_str}")
    print(f"  Tendência    : {result['trend']}")
    print()
    print(f"  {'MÉTRICA':<14} {'PESO':>5}  {'SCORE':>6}  {'STATUS':<8}  VALOR")
    print("  " + "-" * 54)

    weight_map = {
        "lead_time":  ("Lead Time",  WEIGHTS["lead_time"]),
        "throughput": ("Throughput", WEIGHTS["throughput"]),
        "aging":      ("Aging",      WEIGHTS["aging"]),
        "mttr":       ("MTTR",       WEIGHTS["mttr"]),
        "cfr":        ("CFR",        WEIGHTS["cfr"]),
    }

    for key, (name, w) in weight_map.items():
        m = result["metrics"][key]
        val_str = (
            f"{m['value']:.2f} {m['unit']}"
            if m["value"] is not None else "N/A"
        )
        print(
            f"  {m['emoji']} {name:<12} {w*100:>4.0f}%"
            f"  {m['score']:>6.1f}  {m['status']:<8}  {val_str}"
        )

    print("=" * 56)


if __name__ == "__main__":
    main()
