import pandas as pd
import altair as alt
import streamlit as st

from core_metrics import (
    build_throughput_diagnostics,
    compute_throughput,
    diagnose_throughput_drop,
    prepare_df,
)
from db import engine
from squad_health import render_squad_health

MONTH_PT = {
    "01": "JAN", "02": "FEV", "03": "MAR", "04": "ABR",
    "05": "MAI", "06": "JUN", "07": "JUL", "08": "AGO",
    "09": "SET", "10": "OUT", "11": "NOV", "12": "DEZ",
}

TYPE_COLORS = {
    "Story":     "#6366f1",
    "História":  "#6366f1",
    "Bug":       "#ef4444",
    "Incidente": "#f97316",
    "GMUD":      "#8b5cf6",
    "Task":      "#06b6d4",
}
TYPE_COLOR_DEFAULT = "#94a3b8"

DROP_THRESHOLD_PCT = 10.0
CAUSE_COLORS = {
    "Aging": "#dc2626",
    "Bugs": "#ef4444",
    "Incidentes": "#f97316",
    "Variação normal": "#94a3b8",
}


@st.cache_data(ttl=300)
def _load_issues() -> pd.DataFrame:
    df = pd.read_sql("SELECT * FROM issues_raw", engine)
    return prepare_df(df)


def fmt_month(ym: str) -> str:
    if not ym or ym == "-":
        return "—"
    try:
        y, m = ym.split("-")
        return f"{MONTH_PT.get(m, m)}/{y[-2:]}"
    except Exception:
        return ym


def _card(label: str, value_html: str, sub: str = "",
          badge: str = "", badge_color: str = "#94a3b8") -> str:
    badge_el = (
        f'<span style="font-size:10px;font-weight:700;color:{badge_color};'
        f'background:rgba(0,0,0,0.05);padding:2px 6px;border-radius:4px;white-space:nowrap;">'
        f'{badge}</span>'
        if badge else ""
    )
    sub_el = (
        f'<div style="font-size:11px;color:#64748b;margin-top:5px;line-height:1.4;">{sub}</div>'
        if sub else ""
    )
    return (
        f'<div style="background:white;border-radius:12px;padding:16px 18px;'
        f'box-shadow:0 1px 4px rgba(0,0,0,0.07);border:1px solid #f1f5f9;'
        f'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,sans-serif;">'
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start;'
        f'margin-bottom:8px;">'
        f'<span style="font-size:10px;font-weight:700;color:#94a3b8;text-transform:uppercase;'
        f'letter-spacing:.07em;">{label}</span>'
        f'{badge_el}'
        f'</div>'
        f'<div style="font-size:24px;font-weight:800;line-height:1.1;">{value_html}</div>'
        f'{sub_el}'
        f'</div>'
    )


def _consecutive_tail(values: list, pred) -> int:
    n = 0
    for v in reversed(values):
        if pred(v):
            n += 1
        else:
            break
    return n


@st.dialog("Detalhes do Mês", width="large")
def _month_detail(month_ym: str, filt_df: pd.DataFrame, monthly_df: pd.DataFrame) -> None:
    st.markdown(
        f'<div style="font-size:20px;font-weight:800;color:#0f172a;margin-bottom:16px;">'
        f'{fmt_month(month_ym)}</div>',
        unsafe_allow_html=True,
    )

    m_data = filt_df[filt_df["res_month"] == month_ym].copy()
    month_total = len(m_data)

    m_data["ct"] = (
        (m_data["resolutiondate"] - m_data["created"]).dt.total_seconds() / 86400
    )
    avg_ct = m_data["ct"].mean()

    sorted_months = sorted(monthly_df["res_month"].tolist())
    prev_count: int | None = None
    prev_label = "—"
    try:
        idx = sorted_months.index(month_ym)
        if idx > 0:
            prev_ym = sorted_months[idx - 1]
            prev_row = monthly_df[monthly_df["res_month"] == prev_ym]
            if not prev_row.empty:
                prev_count = int(prev_row["count"].iloc[0])
                prev_label = fmt_month(prev_ym)
    except ValueError:
        pass

    if prev_count is not None and prev_count > 0:
        delta_pct = (month_total - prev_count) / prev_count * 100
        vs_str = f"{delta_pct:+.0f}%"
    else:
        vs_str = "—"

    dc1, dc2, dc3 = st.columns(3)
    dc1.metric("Total de Itens", month_total)
    dc2.metric("Ciclo Médio", f"{avg_ct:.1f}d" if pd.notna(avg_ct) else "—")
    dc3.metric(f"vs. {prev_label}", vs_str)

    st.markdown("**Distribuição por Tipo**")

    type_dist = (
        m_data.groupby("issuetype").size()
        .reset_index(name="count")
        .assign(pct=lambda d: (d["count"] / d["count"].sum() * 100).round(1))
    )
    type_dist["pct_label"] = type_dist["pct"].apply(lambda p: f"{p:.0f}%")
    type_dist["color"] = type_dist["issuetype"].map(
        lambda t: TYPE_COLORS.get(t, TYPE_COLOR_DEFAULT)
    )
    type_dist = type_dist.sort_values("count", ascending=False).reset_index(drop=True)

    hbar = (
        alt.Chart(type_dist)
        .mark_bar(cornerRadiusEnd=4)
        .encode(
            y=alt.Y(
                "issuetype:N",
                sort=alt.EncodingSortField("count", order="descending"),
                title=None,
                axis=alt.Axis(labelFontSize=13),
            ),
            x=alt.X("count:Q", title=None),
            color=alt.Color(
                "issuetype:N",
                scale=alt.Scale(
                    domain=type_dist["issuetype"].tolist(),
                    range=type_dist["color"].tolist(),
                ),
                legend=None,
            ),
            tooltip=[
                alt.Tooltip("issuetype:N", title="Tipo"),
                alt.Tooltip("count:Q", title="Itens"),
                alt.Tooltip("pct:Q", title="%", format=".1f"),
            ],
        )
    )
    pct_text = (
        alt.Chart(type_dist)
        .mark_text(align="left", dx=4, fontSize=11, color="#475569")
        .encode(
            y=alt.Y(
                "issuetype:N",
                sort=alt.EncodingSortField("count", order="descending"),
            ),
            x=alt.X("count:Q"),
            text="pct_label:N",
        )
    )
    st.altair_chart(
        (hbar + pct_text)
        .properties(height=max(100, len(type_dist) * 44))
        .configure_view(strokeWidth=0),
        use_container_width=True,
    )


def main():
    render_squad_health()

    df = _load_issues()

    resolved = df[df["is_resolved"]].copy()
    resolved["res_month"] = resolved["resolutiondate"].dt.to_period("M").astype(str)

    months = sorted(resolved["res_month"].dropna().unique().tolist())

    if not months:
        st.error("Sem itens resolvidos para exibir.")
        return

    selected_team = st.session_state.get("global_team", "Todos")

    # ── Top-bar filters (period only — team comes from global sidebar) ───────
    fc1, fc2, _ = st.columns([1.8, 1.8, 5])
    with fc1:
        if len(months) > 1:
            selected_start = st.select_slider("De", options=months, value=months[0])
        else:
            selected_start = months[0]
            st.caption(f"De: {fmt_month(months[0])}")
    with fc2:
        if len(months) > 1:
            selected_end = st.select_slider("Até", options=months, value=months[-1])
        else:
            selected_end = months[0]
            st.caption(f"Até: {fmt_month(months[0])}")

    if selected_start > selected_end:
        st.warning("Período inválido: início deve ser anterior ao fim.")
        return

    # ── Core calculation via core_metrics ───────────────────────────────────
    team_arg = selected_team if selected_team != "Todos" else None
    tp = compute_throughput(df, team=team_arg, start_month=selected_start, end_month=selected_end)

    if not tp:
        st.error("Sem dados para o filtro selecionado.")
        return

    # Unpack result
    monthly_list = tp["monthly"]
    closed_list  = tp["closed"]
    wip          = tp["wip"]
    avg_val      = tp["avg"]
    cv           = tp["cv"]
    n_months     = tp["n_months"]
    total        = tp["total"]
    best_m       = tp["best"]
    worst_m      = tp["worst"]
    trend        = tp["trend"]
    health       = tp["health"]
    pred         = tp["predictability"]
    drop_pct     = tp["drop_pct"]

    closed_counts = [m["count"] for m in closed_list]
    last_count = float(closed_counts[-1]) if closed_counts else 0.0

    # Convert to DataFrames for Altair (keep column names the chart expects)
    monthly = pd.DataFrame([
        {"res_month": m["month"], "month_label": m["label"],
         "count": m["count"], "is_wip": m["is_wip"]}
        for m in monthly_list
    ])
    closed = pd.DataFrame([
        {"res_month": m["month"], "month_label": m["label"], "count": m["count"]}
        for m in closed_list
    ])

    # Filtered resolved issues for type chart, table, and month-detail dialog
    filt = resolved[
        (resolved["res_month"] >= selected_start) & (resolved["res_month"] <= selected_end)
    ].copy()
    if selected_team != "Todos":
        filt = filt[filt["team"] == selected_team]

    # avg_pct: recent half vs historic half (used in header and badge)
    split_idx = max(1, n_months // 2)
    hist_counts   = closed_counts[:split_idx]
    recent_counts = closed_counts[split_idx:]
    hist_avg   = sum(hist_counts)   / len(hist_counts)   if hist_counts   else None
    recent_avg = sum(recent_counts) / len(recent_counts) if recent_counts else None
    avg_pct: float | None = None
    if hist_avg and recent_avg and hist_avg > 0 and n_months >= 3:
        avg_pct = (recent_avg - hist_avg) / hist_avg * 100

    # ── Header ───────────────────────────────────────────────────────────────
    summary_color = trend["color"]
    n_recent = len(recent_counts)
    if trend["label"] == "Crescimento" and avg_pct is not None:
        summary_txt = f"Crescimento de {abs(avg_pct):.0f}% nos últimos {n_recent} meses"
    elif trend["label"] == "Queda" and avg_pct is not None:
        summary_txt = f"Queda de {abs(avg_pct):.0f}% nos últimos {n_recent} meses"
    else:
        summary_txt = trend["desc"]

    st.markdown(
        f'<div style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\','
        f'Roboto,sans-serif;margin-bottom:16px;">'
        f'<div style="font-size:28px;font-weight:800;color:#0f172a;'
        f'letter-spacing:-0.5px;margin-bottom:2px;">Throughput</div>'
        f'<div style="font-size:13px;color:#64748b;margin-bottom:8px;">'
        f'Itens concluídos por mês</div>'
        f'<div style="font-size:13px;">'
        f'<span style="font-weight:600;color:{health["color"]};">'
        f'{health["emoji"]} Saúde: {health["label"]}</span>'
        f'<span style="color:#cbd5e1;margin:0 8px;">·</span>'
        f'<span style="font-weight:600;color:{summary_color};">'
        f'{trend["icon"]} {summary_txt}</span>'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    st.divider()

    # ── 7 KPI cards ──────────────────────────────────────────────────────────
    c1, c2, c3, c4, c5, c6, c7 = st.columns(7)

    if avg_pct is not None:
        avg_badge = f"{'↑' if avg_pct >= 0.5 else ('↓' if avg_pct <= -0.5 else '→')} {abs(avg_pct):.0f}%"
        avg_badge_color = "#15803d" if avg_pct >= 0.5 else ("#dc2626" if avg_pct <= -0.5 else "#94a3b8")
    else:
        avg_badge, avg_badge_color = "", "#94a3b8"
    with c1:
        st.html(_card(
            "Média / Mês",
            f'<span style="color:#0f172a;">{avg_val:.1f}</span>',
            sub="itens/mês",
            badge=avg_badge,
            badge_color=avg_badge_color,
        ))

    with c2:
        st.html(_card(
            "Total no Período",
            f'<span style="color:#0f172a;">{total}</span>',
            sub=f"{n_months} {'mês' if n_months == 1 else 'meses'} fechados",
            badge=f"+ {wip['count']} em andamento" if wip is not None else "",
            badge_color="#94a3b8",
        ))

    best_pct = (best_m["count"] - avg_val) / avg_val * 100 if avg_val > 0 else 0
    best_badge = f"↑ {best_pct:.0f}%" if best_pct >= 0.5 else "= média"
    best_badge_color = "#15803d" if best_pct >= 0.5 else "#94a3b8"
    with c3:
        st.html(_card(
            "Melhor Mês",
            f'<span style="color:#0f172a;">{int(best_m["count"])}</span>',
            sub=best_m["label"],
            badge=best_badge,
            badge_color=best_badge_color,
        ))

    worst_pct = (worst_m["count"] - avg_val) / avg_val * 100 if avg_val > 0 else 0
    worst_badge = f"↓ {abs(worst_pct):.0f}%" if worst_pct <= -0.5 else "= média"
    worst_badge_color = "#dc2626" if worst_pct <= -0.5 else "#94a3b8"
    with c4:
        st.html(_card(
            "Pior Mês",
            f'<span style="color:#0f172a;">{int(worst_m["count"])}</span>',
            sub=worst_m["label"],
            badge=worst_badge,
            badge_color=worst_badge_color,
        ))

    with c5:
        st.html(_card(
            "Tendência",
            f'<span style="color:{trend["color"]};">{trend["icon"]} {trend["label"]}</span>',
            sub=trend["desc"],
        ))

    with c6:
        st.html(_card(
            "Saúde",
            f'<span style="color:{health["color"]};">{health["emoji"]} {health["label"]}</span>',
            sub=health["desc"],
        ))

    with c7:
        st.html(_card(
            "Previsibilidade",
            f'<span style="color:{pred["color"]};">{pred["emoji"]} {pred["label"]}</span>',
            sub=f"Desvio histórico: {cv * 100:.0f}%",
        ))

    st.markdown("<div style='margin-top:8px;'></div>", unsafe_allow_html=True)

    # ── Bar chart with click-to-drill-down ───────────────────────────────────
    if "tp_last_clicked" not in st.session_state:
        st.session_state.tp_last_clicked = None

    bar_sel = alt.selection_point(name="bar_click", fields=["res_month"])

    bars = (
        alt.Chart(monthly)
        .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X("month_label:N", sort=None, title=None,
                    axis=alt.Axis(labelAngle=-30, labelFontSize=12)),
            y=alt.Y("count:Q", title="Itens concluídos", axis=alt.Axis(grid=True)),
            color=alt.condition(
                alt.datum.is_wip,
                alt.value("#94a3b8"),
                alt.value("#6366f1"),
            ),
            opacity=alt.condition(bar_sel, alt.value(1.0), alt.value(0.7)),
            tooltip=[
                alt.Tooltip("month_label:N", title="Mês"),
                alt.Tooltip("count:Q", title="Itens"),
            ],
        )
        .add_params(bar_sel)
    )
    avg_rule = (
        alt.Chart(pd.DataFrame({"avg": [avg_val]}))
        .mark_rule(color="#ef4444", strokeDash=[6, 3], strokeWidth=2)
        .encode(y="avg:Q")
    )
    avg_label = (
        alt.Chart(pd.DataFrame({"avg": [avg_val], "lbl": [f"Média (fechados): {avg_val:.1f}"]}))
        .mark_text(align="right", dx=-4, dy=-8, color="#ef4444",
                   fontSize=11, fontWeight="bold")
        .encode(y=alt.Y("avg:Q"), x=alt.value(800), text="lbl:N")
    )
    wip_label_df = monthly[monthly["is_wip"]].copy().reset_index(drop=True)
    wip_text = (
        alt.Chart(wip_label_df)
        .mark_text(align="center", dy=-10, fontSize=10, fontWeight=600, color="#94a3b8")
        .encode(
            x=alt.X("month_label:N", sort=None),
            y=alt.Y("count:Q"),
            text=alt.value("em andamento"),
        )
    )
    event = st.altair_chart(
        (bars + avg_rule + avg_label + wip_text)
        .properties(height=300)
        .configure_view(strokeWidth=0),
        use_container_width=True,
        on_select="rerun",
    )
    st.caption("Clique em uma barra para ver os detalhes do mês.")

    sel_items = (event.selection or {}).get("bar_click", [])
    clicked = sel_items[0].get("res_month") if sel_items else None
    if clicked and clicked != st.session_state.tp_last_clicked:
        st.session_state.tp_last_clicked = clicked
        _month_detail(clicked, filt, monthly)
    elif not clicked:
        st.session_state.tp_last_clicked = None

    # ── Type distribution + Insights ─────────────────────────────────────────
    type_counts = filt.groupby("issuetype").size().reset_index(name="count")
    type_counts["pct"] = (type_counts["count"] / type_counts["count"].sum() * 100).round(1)
    type_counts["pct_label"] = type_counts["pct"].apply(lambda p: f"{p:.0f}%")
    type_counts["color"] = type_counts["issuetype"].map(
        lambda t: TYPE_COLORS.get(t, TYPE_COLOR_DEFAULT)
    )
    type_counts = type_counts.sort_values("count", ascending=False).reset_index(drop=True)

    col_types, col_insights = st.columns([2, 3], gap="large")

    with col_types:
        st.markdown(
            '<div style="font-size:11px;font-weight:700;color:#94a3b8;'
            'text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px;">'
            'Por Tipo</div>',
            unsafe_allow_html=True,
        )
        hbar = (
            alt.Chart(type_counts)
            .mark_bar(cornerRadiusEnd=4)
            .encode(
                y=alt.Y(
                    "issuetype:N",
                    sort=alt.EncodingSortField("count", order="descending"),
                    title=None,
                    axis=alt.Axis(labelFontSize=13),
                ),
                x=alt.X("count:Q", title=None, axis=alt.Axis(grid=True, tickCount=5)),
                color=alt.Color(
                    "issuetype:N",
                    scale=alt.Scale(
                        domain=type_counts["issuetype"].tolist(),
                        range=type_counts["color"].tolist(),
                    ),
                    legend=None,
                ),
                tooltip=[
                    alt.Tooltip("issuetype:N", title="Tipo"),
                    alt.Tooltip("count:Q", title="Itens"),
                    alt.Tooltip("pct:Q", title="%", format=".1f"),
                ],
            )
        )
        pct_text = (
            alt.Chart(type_counts)
            .mark_text(align="left", dx=4, fontSize=11, fontWeight=600, color="#475569")
            .encode(
                y=alt.Y(
                    "issuetype:N",
                    sort=alt.EncodingSortField("count", order="descending"),
                ),
                x=alt.X("count:Q"),
                text="pct_label:N",
            )
        )
        st.altair_chart(
            (hbar + pct_text)
            .properties(height=max(140, len(type_counts) * 42))
            .configure_view(strokeWidth=0),
            use_container_width=True,
        )

    with col_insights:
        st.markdown(
            '<div style="font-size:11px;font-weight:700;color:#94a3b8;'
            'text-transform:uppercase;letter-spacing:.07em;margin-bottom:12px;">'
            'Insights Automáticos</div>',
            unsafe_allow_html=True,
        )

        bullets: list[str] = []

        bullets.append(
            f"Maior throughput em **{best_m['label']}** com {int(best_m['count'])} itens."
        )

        last_label = closed_list[-1]["label"] if closed_list else monthly_list[-1]["label"]
        last_pct = (last_count - avg_val) / avg_val * 100 if avg_val > 0 else 0
        direction = "acima" if last_pct >= 0 else "abaixo"
        bullets.append(
            f"Último mês fechado (**{last_label}**): {int(last_count)} itens "
            f"— {abs(last_pct):.0f}% {direction} da média ({avg_val:.1f})."
        )

        if not type_counts.empty:
            top = type_counts.iloc[0]
            bullets.append(
                f"Tipo mais frequente: **{top['issuetype']}** "
                f"({top['pct']:.0f}% dos itens concluídos)."
            )

        n_above = _consecutive_tail(closed_counts, lambda c: c > avg_val)
        n_below = _consecutive_tail(closed_counts, lambda c: c < avg_val)
        if n_above >= 2:
            bullets.append(f"**{n_above} meses consecutivos** acima da média do período.")
        elif n_below >= 2:
            bullets.append(f"**{n_below} meses consecutivos** abaixo da média do período.")

        for b in bullets:
            st.markdown(f"- {b}")

    # ── Diagnóstico & Recomendação ────────────────────────────────────────────
    diag_items, rec_items = build_throughput_diagnostics(
        closed_list, df, team_arg, pred
    )

    st.markdown("<div style='margin-top:24px;'></div>", unsafe_allow_html=True)
    st.markdown(
        '<div style="font-size:11px;font-weight:700;color:#94a3b8;'
        'text-transform:uppercase;letter-spacing:.07em;margin-bottom:12px;">'
        'Diagnóstico &amp; Recomendação</div>',
        unsafe_allow_html=True,
    )
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

    # ── Diagnóstico da queda ──────────────────────────────────────────────────
    if drop_pct >= DROP_THRESHOLD_PCT:
        closed_month_keys = [m["month"] for m in closed_list]
        tp_by_month = {m["month"]: m["count"] for m in closed_list}
        team_df = df if selected_team == "Todos" else df[df["team"] == selected_team]
        parts = diagnose_throughput_drop(closed_month_keys, tp_by_month, team_df)
        if parts:
            st.markdown(
                '<div style="font-size:11px;font-weight:700;color:#94a3b8;'
                'text-transform:uppercase;letter-spacing:.07em;margin:24px 0 6px;">'
                'Diagnóstico da Queda</div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<div style="font-size:13px;color:#475569;margin-bottom:12px;">'
                f'Queda de <b>{drop_pct:.0f}%</b> no mês mais recente vs. a média do período. '
                f'Decomposição aproximada das causas candidatas:</div>',
                unsafe_allow_html=True,
            )

            segs = "".join(
                f'<div style="width:{p["pct"]:.1f}%;'
                f'background:{CAUSE_COLORS.get(p["label"], "#94a3b8")};"></div>'
                for p in parts if p["pct"] > 0
            )
            legend = "".join(
                '<div style="display:flex;align-items:center;gap:8px;margin-top:7px;">'
                f'<span style="width:11px;height:11px;border-radius:3px;flex-shrink:0;'
                f'background:{CAUSE_COLORS.get(p["label"], "#94a3b8")};display:inline-block;"></span>'
                f'<span style="font-size:13px;color:#334155;font-weight:600;">{p["label"]}</span>'
                f'<span style="font-size:13px;color:#64748b;margin-left:auto;font-weight:700;">'
                f'{p["pct"]:.0f}%</span>'
                '</div>'
                for p in parts
            )
            st.html(
                '<div style="max-width:520px;font-family:-apple-system,BlinkMacSystemFont,'
                "'Segoe UI',Roboto,sans-serif;background:white;border-radius:12px;"
                'padding:16px 18px;box-shadow:0 1px 4px rgba(0,0,0,0.07);border:1px solid #f1f5f9;">'
                '<div style="display:flex;height:16px;border-radius:8px;overflow:hidden;'
                f'background:#f1f5f9;">{segs}</div>'
                f'<div style="margin-top:10px;">{legend}</div>'
                '</div>'
            )
            st.caption(
                "Correlação heurística simples (primeira aproximação), não uma prova causal "
                "real nem análise estatística rigorosa: mede o quanto Aging, Bugs e Incidentes "
                "subiram no mês mais recente vs. a própria média do período e compara a força "
                "combinada desses sinais com a queda real do throughput. O restante é tratado "
                "como variação normal."
            )

    # ── Últimos 50 itens concluídos ───────────────────────────────────────────
    st.markdown(
        '<div style="font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;'
        'letter-spacing:.07em;margin:20px 0 8px;">Últimos 50 Itens Concluídos</div>',
        unsafe_allow_html=True,
    )

    filt = filt.copy()
    filt["cycle_time_dias"] = (
        (filt["resolutiondate"] - filt["created"]).dt.total_seconds() / 86400
    ).round(1)

    table = (
        filt.sort_values("resolutiondate", ascending=False)
        .head(50)[["key", "issuetype", "team", "resolutiondate", "cycle_time_dias"]]
        .copy()
    )
    table["resolutiondate"] = table["resolutiondate"].dt.strftime("%d/%m/%Y")
    table = table.rename(columns={
        "key": "Chave",
        "issuetype": "Tipo",
        "team": "Time",
        "resolutiondate": "Data Conclusão",
        "cycle_time_dias": "Cycle Time (dias)",
    })
    st.dataframe(table, use_container_width=True, hide_index=True)


main()
