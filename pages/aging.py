import datetime

import altair as alt
import pandas as pd
import sqlalchemy
import streamlit as st

from core_metrics import build_aging_diagnostics, compute_aging, prepare_df
from db import engine
from squad_health import render_context_bar, render_squad_health

OVER_REP_THRESHOLD = 15  # pp

BAND_DISPLAY = [
    ("0–7d",   "0–7 dias",   "#15803d"),
    ("7–14d",  "7–14 dias",  "#ca8a04"),
    ("14–30d", "14–30 dias", "#ca8a04"),
    ("30–60d", "30–60 dias", "#dc2626"),
    ("60+d",   "60+ dias",   "#991b1b"),
]


@st.cache_data(ttl=300)
def _load_issues() -> pd.DataFrame:
    df = pd.read_sql("SELECT * FROM issues_raw", engine)
    return prepare_df(df)


def _row_color(row: pd.Series) -> list[str]:
    d = row["Dias em Aberto"]
    if d < 7:
        bg = "background-color: rgba(21,128,61,0.10); color: #14532d;"
    elif d <= 30:
        bg = "background-color: rgba(202,138,4,0.10); color: #713f12;"
    else:
        bg = "background-color: rgba(220,38,38,0.10); color: #7f1d1d;"
    return [bg] * len(row)


def _card(label: str, value: str, color: str = "#0f172a", sub: str = "") -> str:
    sub_el = (
        f'<div style="font-size:11px;color:#64748b;margin-top:5px;line-height:1.4;">{sub}</div>'
        if sub else ""
    )
    return (
        f'<div style="background:white;border-radius:12px;padding:18px 22px;'
        f'box-shadow:0 1px 4px rgba(0,0,0,0.07);border:1px solid #f1f5f9;'
        f'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,sans-serif;">'
        f'<div style="font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;'
        f'letter-spacing:.07em;margin-bottom:6px;">{label}</div>'
        f'<div style="font-size:30px;font-weight:800;color:{color};line-height:1.1;">{value}</div>'
        f'{sub_el}'
        f'</div>'
    )


def _section_label(text: str) -> None:
    st.markdown(
        f'<div style="font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;'
        f'letter-spacing:.07em;margin:20px 0 10px;">{text}</div>',
        unsafe_allow_html=True,
    )


def main():
    render_squad_health()
    render_context_bar()

    df = _load_issues()

    # Open issues for filter dropdowns and the detail table
    open_issues = df[~df["is_resolved"]].copy()
    today_ts = pd.Timestamp(datetime.date.today())
    open_issues["dias_parado"] = (today_ts - open_issues["created"]).dt.days

    selected_team = st.session_state.get("global_team", "Todos")
    st.sidebar.title("Filtros")
    selected_type = st.sidebar.selectbox(
        "Tipo",
        ["Todos"] + sorted(open_issues["issuetype"].dropna().unique().tolist()),
    )

    # Filtered open issues — used only for the detail table
    filt = open_issues.copy()
    if selected_team != "Todos":
        filt = filt[filt["team"] == selected_team]
    if selected_type != "Todos":
        filt = filt[filt["issuetype"] == selected_type]
    filt = filt.sort_values("dias_parado", ascending=False)

    # ── Page title ────────────────────────────────────────────────────────────
    st.markdown(
        '<div style="font-size:28px;font-weight:800;color:#0f172a;letter-spacing:-0.5px;'
        'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,sans-serif;'
        'margin-bottom:4px;">Aging</div>'
        '<div style="font-size:13px;color:#64748b;margin-bottom:20px;">'
        'Itens em aberto por tempo desde a criação</div>',
        unsafe_allow_html=True,
    )

    if filt.empty:
        st.info("Nenhum item em aberto para o filtro selecionado.")
        return

    # ── Core calculation via core_metrics ────────────────────────────────────
    team_arg  = selected_team if selected_team != "Todos" else None
    type_arg  = selected_type if selected_type != "Todos" else None
    today_date = datetime.date.today()
    aging = compute_aging(df, team=team_arg, issuetype=type_arg, today=today_date)

    # Previous period aging — prefer metric_snapshots (accurate historical state)
    # over the shifted-date approach (which is structurally incorrect: new items
    # get negative ages, resolved items are excluded from both sides of the comparison).
    # Snapshots are stored per team only (no issuetype breakdown), so they're used
    # regardless of the issuetype filter.  Fallback: shifted-date with guard.
    prev_period = (today_date.replace(day=1) - datetime.timedelta(days=1)).strftime("%Y-%m")
    team_key = team_arg or "Todos"
    with engine.connect() as _conn:
        _snap_rows = _conn.execute(
            sqlalchemy.text(
                "SELECT metric_name, value FROM metric_snapshots "
                "WHERE period = :p AND team = :t "
                "AND metric_name IN "
                "('aging_avg_age','aging_pct_critical','aging_total_open')"
            ),
            {"p": prev_period, "t": team_key},
        ).fetchall()
    if len(_snap_rows) == 3:
        _snap = {r[0]: r[1] for r in _snap_rows}
        prev_aging: dict | None = {
            "avg_age":      _snap["aging_avg_age"],
            "pct_critical": _snap["aging_pct_critical"],
            "total_open":   int(_snap["aging_total_open"]),
        }
    else:
        prev_aging = compute_aging(
            df, team=team_arg, issuetype=type_arg,
            today=today_date - datetime.timedelta(days=30),
        )

    total_open = aging["total_open"]
    avg_age    = aging["avg_age"]
    bands      = aging["bands"]
    sem_mov    = aging["sem_movimento"]

    over7  = bands["7–14d"] + bands["14–30d"] + bands["30–60d"] + bands["60+d"]
    over30 = bands["30–60d"] + bands["60+d"]

    # ── KPI cards ─────────────────────────────────────────────────────────────
    if sem_mov is not None:
        sem_mov_pct = sem_mov / total_open * 100 if total_open else 0
        sem_mov_sub = f"{sem_mov_pct:.0f}% dos itens abertos"
        sem_mov_color = (
            "#dc2626" if sem_mov_pct >= 40 else
            "#ca8a04" if sem_mov_pct >= 20 else
            "#0f172a"
        )
        sem_mov_val = str(sem_mov)
    else:
        sem_mov_val   = "—"
        sem_mov_sub   = "campo 'updated' indisponível"
        sem_mov_color = "#94a3b8"

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.html(_card("Total em Aberto", str(total_open)))
    with c2:
        st.html(_card("Idade Média", f"{avg_age:.0f}d"))
    with c3:
        st.html(_card("≥ 7 dias", str(over7), "#ca8a04"))
    with c4:
        st.html(_card("> 30 dias", str(over30), "#dc2626"))
    with c5:
        st.html(_card("Sem Movimentação", sem_mov_val, sem_mov_color, sub=sem_mov_sub))

    st.markdown("<div style='margin-top:16px;'></div>", unsafe_allow_html=True)

    # ── Histograma por faixa de dias ──────────────────────────────────────────
    _section_label("Distribuição por Faixa de Tempo")

    band_rows = [
        {"faixa": label, "count": bands[key], "color": color}
        for key, label, color in BAND_DISPLAY
    ]
    hist_df = pd.DataFrame(band_rows)
    band_order = [r["faixa"] for r in band_rows]

    bars = (
        alt.Chart(hist_df)
        .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X("faixa:N", sort=band_order, title=None,
                    axis=alt.Axis(labelFontSize=12, labelAngle=0)),
            y=alt.Y("count:Q", title="Itens em aberto",
                    axis=alt.Axis(grid=True, gridColor="#f1f5f9")),
            color=alt.Color(
                "faixa:N",
                scale=alt.Scale(
                    domain=[r["faixa"] for r in band_rows],
                    range=[r["color"] for r in band_rows],
                ),
                legend=None,
            ),
            tooltip=[
                alt.Tooltip("faixa:N", title="Faixa"),
                alt.Tooltip("count:Q", title="Itens"),
            ],
        )
        .properties(height=220)
    )
    labels = (
        alt.Chart(hist_df)
        .mark_text(dy=-10, fontSize=13, fontWeight=700)
        .encode(
            x=alt.X("faixa:N", sort=band_order),
            y=alt.Y("count:Q"),
            text=alt.Text("count:Q"),
            color=alt.Color(
                "faixa:N",
                scale=alt.Scale(
                    domain=[r["faixa"] for r in band_rows],
                    range=[r["color"] for r in band_rows],
                ),
                legend=None,
            ),
        )
    )
    st.altair_chart(
        (bars + labels).configure_view(strokeWidth=0),
        use_container_width=True,
    )

    # ── Diagnóstico & Recomendação ────────────────────────────────────────────
    _ag_period = today_date.strftime("%Y-%m")
    _ag_team_label = selected_team  # already computed
    _ag_events = build_aging_diagnostics(
        df, team_arg, type_arg,
        today=today_date,
        prev_aging=prev_aging,
        team_label=_ag_team_label,
        period=_ag_period,
    )
    diag_items = [e.description for e in _ag_events if e.layer in ("insight", "diagnostic")]
    rec_items  = [e.description for e in _ag_events if e.layer == "recommendation"]
    _section_label("Diagnóstico &amp; Recomendação")
    if not diag_items:
        st.markdown(
            '<span style="font-size:13px;color:#94a3b8;">'
            'Nenhum fator de destaque identificado neste período.</span>',
            unsafe_allow_html=True,
        )
    else:
        col_d, col_r = st.columns(2, gap="large")
        with col_d:
            st.markdown("**🔍 Diagnóstico**")
            for d in diag_items:
                st.markdown(f"- {d}")
        with col_r:
            st.markdown("**🎯 Recomendação**")
            for r in rec_items:
                st.markdown(f"- {r}")

    # ── Lista de itens ────────────────────────────────────────────────────────
    _section_label("Lista de Itens em Aberto")

    table = filt[["key", "issuetype", "team", "created", "dias_parado"]].copy()
    table["created"] = table["created"].dt.strftime("%d/%m/%Y")
    table = table.rename(columns={
        "key":         "Chave",
        "issuetype":   "Tipo",
        "team":        "Time",
        "created":     "Criado em",
        "dias_parado": "Dias em Aberto",
    })
    styled = table.style.apply(_row_color, axis=1)
    st.dataframe(styled, use_container_width=True, hide_index=True)
    st.caption(
        "Dias em Aberto = dias desde a criação do item, não desde a última mudança de status. "
        "Aproximação a ser refinada quando o changelog do Jira estiver disponível."
    )

    # ── Diagnóstico: sobre-representação na faixa crítica (> 30 dias) ─────────
    diagnosis = aging["diagnosis"]
    if not diagnosis:
        if over30 > 0:
            _section_label("Diagnóstico — Fatores sobre-representados na faixa crítica (> 30 dias)")
            st.markdown(
                '<div style="font-size:13px;color:#64748b;">'
                'Nenhum Tipo ou Time com sobre-representação relevante (≥ 15 p.p.) na faixa crítica.'
                '</div>',
                unsafe_allow_html=True,
            )
        return

    _section_label("Diagnóstico — Fatores sobre-representados na faixa crítica (> 30 dias)")
    diagnosis_sorted = sorted(diagnosis, key=lambda f: f["over_rep"], reverse=True)
    for f in diagnosis_sorted:
        n_label = f"{f['n_red']} {'item' if f['n_red'] == 1 else 'itens'}"
        st.markdown(
            f"- **{f['dim']}: {f['val']}** — "
            f"{f['pct_red']:.0f}% dos itens críticos vs. {f['pct_total']:.0f}% do total de abertos "
            f"(**+{f['over_rep']:.0f} p.p.** de sobre-representação, {n_label} em vermelho)."
        )


main()
