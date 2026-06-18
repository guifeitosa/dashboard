"""
squad_health.py — Squad Health Score (card + computation)

Source of truth: issues_raw from SQLite (via db.engine).
All scoring logic lives in core_metrics.squad_health_score().

CFR behaviour when no data is available:
  Its 15% weight is redistributed proportionally among the other 4 metrics.
  See core_metrics.squad_health_score() for the full rationale.
"""

import pandas as pd
import streamlit as st

from core_metrics import prepare_df, squad_health_score
from db import engine


@st.cache_data(ttl=300)
def _load_issues() -> pd.DataFrame:
    df = pd.read_sql("SELECT * FROM issues_raw", engine)
    return prepare_df(df)


def compute_squad_health(team: str | None = None) -> dict:
    """Load from SQLite and compute Squad Health Score (last 3 months).

    team: if given, filters issues to that team before scoring.
          Pass None (or omit) for the all-teams aggregate.
    """
    df = _load_issues()
    if team is not None:
        df = df[df["team"] == team]
    return squad_health_score(df)


# ── Streamlit card (shared across pages) ─────────────────────────────────────

def _score_emoji(score: float) -> str:
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
    if "Subindo" in trend:
        return "↗", "Melhorando", "#15803d"
    if "Caindo" in trend:
        return "↘", "Piorando", "#dc2626"
    if "Sem" in trend:
        return "→", "Sem histórico", "#94a3b8"
    return "→", "Estável", "#64748b"


def render_squad_health() -> None:
    """Render the Squad Health card. Reads global_team from session_state automatically."""
    _global = st.session_state.get("global_team", "Todos")
    team_arg = None if _global == "Todos" else _global
    h = compute_squad_health(team=team_arg)

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

        '<div style="border-top:1px solid #f1f5f9;margin:16px 0 14px;"></div>'
        f'{impacts_html}'

        '</div>'
    )

    st.html(card)


def render_context_bar(period: str | None = None) -> None:
    """Slim bar showing the active team filter (and optional period) for every page."""
    team = st.session_state.get("global_team", "Todos")
    parts = [f"<strong>Mostrando:</strong> {team}"]
    if period:
        parts.append(f"<strong>Período:</strong> {period}")
    text = "&nbsp;&nbsp;·&nbsp;&nbsp;".join(parts)
    st.markdown(
        f'<div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;'
        f'padding:7px 16px;margin-bottom:14px;font-size:13px;color:#3730a3;'
        f'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,sans-serif;">'
        f'🔍 {text}</div>',
        unsafe_allow_html=True,
    )
