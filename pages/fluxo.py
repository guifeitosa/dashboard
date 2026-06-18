"""
Fluxo — tempo médio/mediano por status do workflow e diagnóstico de gargalos.

Lê de issues_raw + issue_transitions (SQLite).
Delega todo cálculo a status_time.average_time_in_status / time_in_status.
"""

import datetime
import os
import sqlite3
from collections import defaultdict
from datetime import timedelta

import altair as alt
import pandas as pd
import streamlit as st

from core_metrics import TERMINAL_STATUSES
from squad_health import render_squad_health
from status_time import average_time_in_status, time_in_status

_HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, "..", "metrics.db")

FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif"

# Statuses considered "done" — excluded from bottleneck analysis.
# Defined once in core_metrics.TERMINAL_STATUSES; imported here to avoid drift.
_TERMINAL = TERMINAL_STATUSES


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_terminal(status: str) -> bool:
    return (status or "").strip().lower() in _TERMINAL


def _fmt_dur(td: timedelta) -> str:
    h = td.total_seconds() / 3600
    if h < 24:
        return f"{h:.1f}h"
    return f"{h / 24:.1f}d"


def _card(label: str, value: str, color: str = "#0f172a", sub: str = "") -> str:
    sub_el = (
        f'<div style="font-size:11px;color:#64748b;margin-top:5px;line-height:1.4;">{sub}</div>'
        if sub else ""
    )
    return (
        f'<div style="background:white;border-radius:12px;padding:16px 18px;'
        f'box-shadow:0 1px 4px rgba(0,0,0,0.07);border:1px solid #f1f5f9;'
        f'font-family:{FONT};">'
        f'<div style="font-size:10px;font-weight:700;color:#94a3b8;text-transform:uppercase;'
        f'letter-spacing:.07em;margin-bottom:6px;">{label}</div>'
        f'<div style="font-size:26px;font-weight:800;color:{color};line-height:1.1;">{value}</div>'
        f'{sub_el}'
        f'</div>'
    )


def _section_label(text: str) -> None:
    st.markdown(
        f'<div style="font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;'
        f'letter-spacing:.07em;margin:24px 0 10px;">{text}</div>',
        unsafe_allow_html=True,
    )


# ── Data loading ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def _load_db() -> tuple[pd.DataFrame, pd.DataFrame]:
    con = sqlite3.connect(DB_PATH)
    issues = pd.read_sql(
        "SELECT key, issuetype, team, status, created, resolutiondate FROM issues_raw",
        con,
        parse_dates=["created", "resolutiondate"],
    )
    transitions = pd.read_sql(
        "SELECT issue_key, from_status, to_status, changed_at FROM issue_transitions",
        con,
        parse_dates=["changed_at"],
    )
    con.close()
    return issues, transitions


def _build_records(issues: pd.DataFrame, transitions: pd.DataFrame) -> list[dict]:
    """Convert DB rows into the dict format expected by status_time functions."""
    tr_by_key: dict[str, list[dict]] = defaultdict(list)
    for _, tr in transitions.iterrows():
        if pd.notna(tr["changed_at"]):
            tr_by_key[tr["issue_key"]].append({
                "from_status": tr["from_status"],
                "to_status":   tr["to_status"],
                "changed_at":  tr["changed_at"].to_pydatetime(),
            })

    records = []
    for _, row in issues.iterrows():
        if pd.isna(row["created"]):
            continue
        records.append({
            "issue_key":     row["key"],
            "created":       row["created"].to_pydatetime(),
            "resolutiondate": row["resolutiondate"].to_pydatetime() if pd.notna(row["resolutiondate"]) else None,
            "team":          row["team"],
            "issuetype":     row["issuetype"],
            "status":        row["status"],   # passed as initial_status when no transitions
            "transitions":   tr_by_key.get(row["key"], []),
        })
    return records


# ── Flow metric computation ───────────────────────────────────────────────────

def _flow_metrics(
    records: list[dict],
    now: datetime.datetime,
    team: str | None = None,
    issuetype: str | None = None,
) -> tuple[dict[str, timedelta], dict[str, timedelta]]:
    """
    Return (mean_by_status, median_by_status) for the given filters.
    average_time_in_status handles the mean; median is computed locally.
    """
    filtered = [
        r for r in records
        if (team is None or r.get("team") == team)
        and (issuetype is None or r.get("issuetype") == issuetype)
    ]

    # mean — reuses average_time_in_status (already filters nothing extra since we
    # pre-filtered; pass team=None/issuetype=None to avoid double-filtering)
    mean_map = average_time_in_status(filtered, now)

    # median — collect per-issue seconds per status, then take median
    secs_by_status: dict[str, list[float]] = defaultdict(list)
    for r in filtered:
        end = r.get("resolutiondate") or now
        durs = time_in_status(
            r["issue_key"], r["created"], r.get("transitions", []), end,
            initial_status=r.get("status"),
        )
        for status, dur in durs.items():
            secs_by_status[status].append(dur.total_seconds())

    median_map = {
        s: timedelta(seconds=float(pd.Series(vals).median()))
        for s, vals in secs_by_status.items()
    }
    return mean_map, median_map


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    render_squad_health()

    issues_df, transitions_df = _load_db()
    all_records = _build_records(issues_df, transitions_df)

    if not all_records:
        st.error("Nenhum dado encontrado em metrics.db.")
        return

    # ── Filters ──────────────────────────────────────────────────────────────
    teams = ["Todos"] + sorted({r["team"] for r in all_records if r.get("team")})
    types = ["Todos"] + sorted({r["issuetype"] for r in all_records if r.get("issuetype")})

    fc1, fc2, _ = st.columns([1.4, 1.6, 7])
    with fc1:
        sel_team = st.selectbox("Time", teams)
    with fc2:
        sel_type = st.selectbox("Tipo", types)

    team_f = None if sel_team == "Todos" else sel_team
    type_f = None if sel_type == "Todos" else sel_type

    records = [
        r for r in all_records
        if (team_f is None or r.get("team") == team_f)
        and (type_f is None or r.get("issuetype") == type_f)
    ]

    if not records:
        st.warning("Sem dados para o filtro selecionado.")
        return

    # ── Page header ───────────────────────────────────────────────────────────
    n_with_tr = sum(1 for r in records if r["transitions"])
    tr_pct = n_with_tr / len(records) * 100 if records else 0

    st.markdown(
        f'<div style="font-family:{FONT};margin-bottom:16px;">'
        f'<div style="font-size:28px;font-weight:800;color:#0f172a;'
        f'letter-spacing:-0.5px;margin-bottom:2px;">Fluxo</div>'
        f'<div style="font-size:13px;color:#64748b;">Tempo por status · '
        f'{len(records)} itens · {n_with_tr} com histórico de transições ({tr_pct:.0f}%)'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    st.divider()

    now = datetime.datetime.utcnow()
    mean_map, median_map = _flow_metrics(records, now)

    # Active (non-terminal) statuses with data
    active_statuses = {s for s in mean_map if not _is_terminal(s) and s != "Unknown"}
    terminal_statuses = {s for s in mean_map if _is_terminal(s)}

    # ── Section 1: Tempo por Status ───────────────────────────────────────────
    _section_label("Tempo por Status — Workflow Ativo")

    if not active_statuses:
        st.info(
            "Nenhum status de workflow ativo encontrado. "
            "Todos os itens estão em status finais ou sem status definido."
        )
    else:
        chart_rows = []
        for s in sorted(active_statuses, key=lambda x: mean_map[x].total_seconds(), reverse=True):
            mean_h = mean_map[s].total_seconds() / 3600
            med_h  = median_map.get(s, timedelta(0)).total_seconds() / 3600
            chart_rows.append({
                "status":    s,
                "mean_h":    round(mean_h, 2),
                "med_h":     round(med_h, 2),
                "mean_fmt":  _fmt_dur(mean_map[s]),
                "med_fmt":   _fmt_dur(median_map.get(s, timedelta(0))),
            })
        chart_df = pd.DataFrame(chart_rows)
        status_order = chart_df["status"].tolist()  # already sorted desc by mean

        # KPI cards: top 3 statuses by mean time
        cols = st.columns(min(len(chart_rows), 4))
        for i, row in chart_df.iterrows():
            if i >= 4:
                break
            with cols[i]:
                st.html(_card(
                    row["status"],
                    row["mean_fmt"],
                    sub=f"Mediana: {row['med_fmt']}",
                ))

        st.markdown("<div style='margin-top:12px;'></div>", unsafe_allow_html=True)

        # Horizontal bar chart
        bars = (
            alt.Chart(chart_df)
            .mark_bar(cornerRadiusEnd=4, color="#6366f1")
            .encode(
                y=alt.Y("status:N", sort=status_order, title=None,
                        axis=alt.Axis(labelFontSize=13)),
                x=alt.X("mean_h:Q", title="Horas (média)",
                        axis=alt.Axis(grid=True, gridColor="#f1f5f9")),
                tooltip=[
                    alt.Tooltip("status:N",   title="Status"),
                    alt.Tooltip("mean_fmt:N", title="Média"),
                    alt.Tooltip("med_fmt:N",  title="Mediana"),
                ],
            )
        )
        med_ticks = (
            alt.Chart(chart_df)
            .mark_tick(color="#f97316", thickness=3, size=20)
            .encode(
                y=alt.Y("status:N", sort=status_order),
                x=alt.X("med_h:Q"),
                tooltip=[alt.Tooltip("med_fmt:N", title="Mediana")],
            )
        )
        mean_labels = (
            alt.Chart(chart_df)
            .mark_text(align="left", dx=6, fontSize=12, fontWeight=700, color="#334155")
            .encode(
                y=alt.Y("status:N", sort=status_order),
                x=alt.X("mean_h:Q"),
                text="mean_fmt:N",
            )
        )
        st.altair_chart(
            (bars + med_ticks + mean_labels)
            .properties(height=max(120, len(chart_rows) * 56))
            .configure_view(strokeWidth=0),
            use_container_width=True,
        )
        st.caption(
            "Barras = tempo médio · traço laranja ▏= mediana · "
            "Para itens sem histórico de transições, o tempo é calculado desde a criação até agora."
        )

    # ── Section 2: Volume Atual por Status ────────────────────────────────────
    _section_label("Itens em Aberto Agora")

    open_issues = issues_df[issues_df["resolutiondate"].isna()].copy()
    if team_f:
        open_issues = open_issues[open_issues["team"] == team_f]
    if type_f:
        open_issues = open_issues[open_issues["issuetype"] == type_f]

    if open_issues.empty:
        st.info("Nenhum item em aberto para o filtro selecionado.")
    else:
        status_counts = (
            open_issues.groupby("status").size()
            .reset_index(name="n")
            .sort_values("n", ascending=False)
        )
        # Non-terminal first, terminal last
        status_counts["is_terminal"] = status_counts["status"].apply(_is_terminal)
        status_counts = pd.concat([
            status_counts[~status_counts["is_terminal"]].sort_values("n", ascending=False),
            status_counts[status_counts["is_terminal"]].sort_values("n", ascending=False),
        ])

        cols = st.columns(min(len(status_counts), 5))
        for i, (_, row) in enumerate(status_counts.iterrows()):
            if i >= 5:
                break
            terminal = _is_terminal(row["status"])
            color = "#94a3b8" if terminal else "#0f172a"
            sub = "status final" if terminal else ""
            with cols[i]:
                st.html(_card(row["status"], str(int(row["n"])), color, sub=sub))

    # ── Section 3: Diagnóstico — Gargalo ────────────────────────────────────
    _section_label("Diagnóstico — Gargalo")

    if not active_statuses:
        st.info("Sem dados de workflow ativo para identificar gargalo.")
        return

    # Gargalo = non-terminal status with highest MEAN time
    bottleneck = max(active_statuses, key=lambda s: mean_map[s].total_seconds())
    b_mean   = mean_map[bottleneck]
    b_median = median_map.get(bottleneck, timedelta(0))

    # Count open issues currently IN the bottleneck status
    b_open = int(
        (open_issues["status"] == bottleneck).sum()
        if not open_issues.empty else 0
    )
    b_pct = b_open / len(open_issues) * 100 if not open_issues.empty and len(open_issues) > 0 else 0

    # Relative severity: compare bottleneck against the mean of ALL active status times.
    # Using the overall mean (not just "others") avoids inflating the reference
    # when one status is massively dominant.
    #   Crítico  = ratio > 2.0  (mais que o dobro da média geral)
    #   Atenção  = ratio > 1.5  (50% acima da média)
    #   Normal   = ratio ≤ 1.5  (comparável aos demais — ou único status ativo)
    all_secs = [mean_map[s].total_seconds() for s in active_statuses]
    overall_mean_secs = sum(all_secs) / len(all_secs)
    ratio = b_mean.total_seconds() / overall_mean_secs if overall_mean_secs > 0 else 1.0
    ratio_label = f"{ratio:.1f}× a média"

    if ratio > 2.0:
        sev_color, sev_label = "#dc2626", "Crítico"
    elif ratio > 1.5:
        sev_color, sev_label = "#ca8a04", "Atenção"
    else:
        sev_color, sev_label = "#15803d", "Normal"

    st.html(
        f'<div style="background:white;border-radius:12px;padding:20px 24px;'
        f'box-shadow:0 1px 4px rgba(0,0,0,0.07);border:2px solid {sev_color}20;'
        f'font-family:{FONT};max-width:600px;">'
        f'<div style="font-size:10px;font-weight:700;color:#94a3b8;text-transform:uppercase;'
        f'letter-spacing:.07em;margin-bottom:8px;">Gargalo Identificado</div>'
        f'<div style="display:flex;align-items:baseline;gap:12px;margin-bottom:12px;">'
        f'<span style="font-size:22px;font-weight:800;color:#0f172a;">{bottleneck}</span>'
        f'<span style="font-size:12px;font-weight:700;color:{sev_color};'
        f'background:{sev_color}15;padding:2px 8px;border-radius:4px;">{sev_label}</span>'
        f'<span style="font-size:12px;color:#94a3b8;">{ratio_label}</span>'
        f'</div>'
        f'<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;">'
        f'<div><div style="font-size:10px;color:#94a3b8;font-weight:700;text-transform:uppercase;'
        f'letter-spacing:.06em;margin-bottom:2px;">Tempo médio</div>'
        f'<div style="font-size:20px;font-weight:800;color:{sev_color};">{_fmt_dur(b_mean)}</div></div>'
        f'<div><div style="font-size:10px;color:#94a3b8;font-weight:700;text-transform:uppercase;'
        f'letter-spacing:.06em;margin-bottom:2px;">Mediana</div>'
        f'<div style="font-size:20px;font-weight:800;color:#334155;">{_fmt_dur(b_median)}</div></div>'
        f'<div><div style="font-size:10px;color:#94a3b8;font-weight:700;text-transform:uppercase;'
        f'letter-spacing:.06em;margin-bottom:2px;">Itens parados</div>'
        f'<div style="font-size:20px;font-weight:800;color:#334155;">{b_open}'
        f'<span style="font-size:13px;color:#94a3b8;font-weight:400;margin-left:4px;">'
        f'({b_pct:.0f}% dos abertos)</span></div></div>'
        f'</div></div>'
    )

    # Other non-terminal statuses as secondary context
    other = sorted(
        [s for s in active_statuses if s != bottleneck],
        key=lambda s: mean_map[s].total_seconds(),
        reverse=True,
    )
    if other:
        st.markdown("<div style='margin-top:12px;'></div>", unsafe_allow_html=True)
        st.markdown(
            '<div style="font-size:11px;font-weight:700;color:#94a3b8;'
            'text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px;">'
            'Demais Status Ativos</div>',
            unsafe_allow_html=True,
        )
        other_cols = st.columns(min(len(other), 4))
        for i, s in enumerate(other[:4]):
            o_open = int(
                (open_issues["status"] == s).sum() if not open_issues.empty else 0
            )
            with other_cols[i]:
                st.html(_card(
                    s,
                    _fmt_dur(mean_map[s]),
                    sub=f"{o_open} em aberto · med {_fmt_dur(median_map.get(s, timedelta(0)))}",
                ))


main()
