import pandas as pd
import altair as alt
import streamlit as st

from loader import load_jira_issues_from_csv
from squad_health import render_squad_health

DATA_PATH = "data/jira_issues_synthetic.csv"

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

# Diagnóstico da queda (heurístico) — limiar de "queda relevante" no mês mais
# recente vs. a média, e cores das causas candidatas.
DROP_THRESHOLD_PCT = 10.0
CAUSE_COLORS = {
    "Aging": "#dc2626",
    "Bugs": "#ef4444",
    "Incidentes": "#f97316",
    "Variação normal": "#94a3b8",
}


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


def _compute_trend(counts: list, avg_val: float) -> tuple:
    """
    Crescimento: últimos 3 meses todos acima da média
    Queda: últimos 2 meses ambos abaixo da média
    Estável: demais casos
    """
    if len(counts) >= 3 and all(c > avg_val for c in counts[-3:]):
        return "Crescimento", "↗", "#15803d", "Últimos 3 meses acima da média"
    if len(counts) >= 2 and all(c < avg_val for c in counts[-2:]):
        return "Queda", "↘", "#dc2626", "Últimos 2 meses abaixo da média"
    return "Estável", "→", "#94a3b8", "Sem tendência definida"


def _compute_health(trend_label: str, last_count: float,
                    avg_val: float, cv: float) -> tuple:
    """
    Crítica: último mês < 50% da média  OU  (Queda E último < 70% da média)
    Boa:     (Crescimento ou Estável)  E  cv < 0.40
    Atenção: demais casos (queda moderada ou alta variabilidade)
    cv = desvio_padrão / média  (coeficiente de variação)
    """
    ratio = (last_count / avg_val) if avg_val > 0 else 1.0
    if ratio < 0.50 or (trend_label == "Queda" and ratio < 0.70):
        return "Crítica", "🔴", "#dc2626", "Último mês muito abaixo da média"
    if trend_label in ("Crescimento", "Estável") and cv < 0.40:
        return "Boa", "🟢", "#15803d", "Volume estável ou crescente"
    return "Atenção", "🟡", "#ca8a04", "Queda moderada ou alta variabilidade"


def _compute_predictability(cv: float) -> tuple:
    """
    Previsibilidade pelo coeficiente de variação (cv = desvio padrão / média)
    do throughput mensal no período selecionado:
      🟢 Alta  : desvio < 15%
      🟡 Média : 15% – 30%
      🔴 Baixa : > 30%
    """
    pct = cv * 100
    if pct < 15:
        return "Alta", "🟢", "#15803d"
    if pct <= 30:
        return "Média", "🟡", "#ca8a04"
    return "Baixa", "🔴", "#dc2626"


def _diagnose_drop(period_months: list, tp_by_month: dict,
                   team_df: pd.DataFrame) -> list:
    """
    Diagnóstico HEURÍSTICO de uma queda de throughput.

    Isto é uma CORRELAÇÃO HEURÍSTICA SIMPLES, não uma prova causal real — uma
    primeira aproximação útil, não uma análise estatística rigorosa. Todos os
    valores abaixo vêm dos dados reais do período (nenhum percentual é fixo).

    Fórmula:
      1. Para cada candidato f (Aging, Bugs, Incidentes), o desvio % dele mesmo
         vs. a própria média do período:
             desvio_f = (f_atual - f_média) / f_média
         Clipado em 0 quando vai na direção "boa" (ex.: Bugs caindo não explica
         queda de throughput), via max(0, ...).
      2. Força total do sinal = Σ desvio_f  (já zerados onde negativos).
      3. delta_throughput = (tp_média - tp_atual) / tp_média  (queda relativa);
         fração_explicada = min(1, força_total / delta_throughput). Se os sinais
         combinados forem pequenos perante a queda, a maior parte vira "normal".
      4. Cada causa recebe: fração_explicada * (desvio_f / força_total) * 100%.
         "Variação normal" recebe (1 - fração_explicada) * 100%.

    Retorna: lista de {"label", "pct"} ordenada desc, ou [] se inconclusivo.
    """
    months = list(period_months)
    if len(months) < 3:
        return []

    # Queda relativa do throughput (mês mais recente vs. média do período).
    tp_s = pd.Series([float(tp_by_month.get(m, 0)) for m in months])
    tp_mean = tp_s.mean()
    tp_last = tp_s.iloc[-1]
    delta_throughput = (tp_mean - tp_last) / tp_mean if tp_mean > 0 else 0.0
    if delta_throughput <= 0:
        return []

    lower_type = team_df["issuetype"].astype(str).str.lower()

    def _created_counts(sub: pd.DataFrame) -> pd.Series:
        cm = sub["created"].dt.to_period("M").astype(str)
        vc = cm.value_counts().to_dict()
        return pd.Series([float(vc.get(m, 0)) for m in months])

    bugs = _created_counts(team_df[lower_type == "bug"])
    incidents = _created_counts(team_df[lower_type == "incidente"])

    # Aging: backlog "envelhecido" (>30d em aberto) reconstruído ao fim de cada
    # mês — item criado há mais de 30 dias e ainda não resolvido naquela data.
    aging_vals = []
    for m in months:
        t_end = pd.Period(m, freq="M").end_time
        cutoff = t_end - pd.Timedelta(days=30)
        aged = team_df[
            (team_df["created"] <= cutoff)
            & (team_df["resolutiondate"].isna() | (team_df["resolutiondate"] > t_end))
        ]
        aging_vals.append(float(len(aged)))
    aging = pd.Series(aging_vals)

    factors = {"Aging": aging, "Bugs": bugs, "Incidentes": incidents}

    # 1. Desvio % de cada fator vs. a própria média, clipado na direção "ruim".
    #    Subir Aging/Bugs/Incidentes é o que pode explicar queda de throughput;
    #    cair vai na direção boa, então zera (não explica a queda).
    deviations = {}
    for label, series in factors.items():
        mean = series.mean()
        last = series.iloc[-1]
        deviations[label] = max(0.0, (last - mean) / mean) if mean > 0 else 0.0

    # 2. Força total do sinal.
    total_signal = sum(deviations.values())
    if total_signal <= 0:
        return [{"label": "Variação normal", "pct": 100.0}]

    # 3. Fração explicada: sinais pequenos perante a queda → sobra vira "normal".
    explained = min(1.0, total_signal / delta_throughput)

    # 4. Distribui a fração explicada entre as causas, proporcional ao desvio.
    parts = []
    shown_pct = 0.0
    for label, dev in deviations.items():
        pct = explained * (dev / total_signal) * 100
        if pct >= 1:
            parts.append({"label": label, "pct": pct})
            shown_pct += pct
    # Sobra (1 - fração_explicada, mais causas < 1% descartadas) = variação normal.
    parts.append({"label": "Variação normal", "pct": 100.0 - shown_pct})
    parts.sort(key=lambda d: d["pct"], reverse=True)
    return parts


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

    # Previous month within the period
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

    df = load_jira_issues_from_csv(DATA_PATH)

    # res_month based on resolutiondate (completion), not created
    resolved = df[df["is_resolved"]].copy()
    resolved["res_month"] = resolved["resolutiondate"].dt.to_period("M").astype(str)

    teams = ["Todos"] + sorted(df["team"].dropna().unique().tolist())
    months = sorted(resolved["res_month"].dropna().unique().tolist())

    if not months:
        st.error("Sem itens resolvidos para exibir.")
        return

    # ── Top-bar filters (no sidebar) ────────────────────────────────────────
    fc1, fc2, fc3, _ = st.columns([1.5, 1.8, 1.8, 5])
    with fc1:
        selected_team = st.selectbox("Time", teams)
    with fc2:
        selected_start = st.select_slider("De", options=months, value=months[0])
    with fc3:
        selected_end = st.select_slider("Até", options=months, value=months[-1])

    if selected_start > selected_end:
        st.warning("Período inválido: início deve ser anterior ao fim.")
        return

    filt = resolved[
        (resolved["res_month"] >= selected_start) & (resolved["res_month"] <= selected_end)
    ].copy()
    if selected_team != "Todos":
        filt = filt[filt["team"] == selected_team]

    if filt.empty:
        st.error("Sem dados para o filtro selecionado.")
        return

    # ── Monthly aggregation ──────────────────────────────────────────────────
    monthly = (
        filt.groupby("res_month").size()
        .reset_index(name="count")
        .sort_values("res_month")
    )
    monthly["month_label"] = monthly["res_month"].apply(fmt_month)

    # ── Separate WIP (most recent, in progress) from closed months ────────────
    # The most recent month is always "in progress" and excluded from every KPI
    # baseline, trend, health and diagnostic calculation — matching the same
    # finalized=False rule used in metric_snapshots. It still appears in the
    # chart, visually distinguished. When only one month exists, no WIP split.
    monthly["is_wip"] = False
    if len(monthly) > 1:
        monthly.loc[monthly.index[-1], "is_wip"] = True
        closed = monthly.iloc[:-1].copy()
    else:
        closed = monthly.copy()
    wip_row = monthly[monthly["is_wip"]].iloc[0] if monthly["is_wip"].any() else None

    closed_counts = closed["count"].tolist()
    n_months = len(closed_counts)                           # closed months only
    total = int(sum(closed_counts))
    avg_val = sum(closed_counts) / n_months if n_months else 0.0

    best_row = closed.loc[closed["count"].idxmax()] if not closed.empty else monthly.iloc[0]
    worst_row = closed.loc[closed["count"].idxmin()] if not closed.empty else monthly.iloc[0]
    last_count = float(closed_counts[-1]) if closed_counts else 0.0

    # ── Period split: first half = histórico, second half = recente ──────────
    split_idx = max(1, n_months // 2)
    hist_counts = closed_counts[:split_idx]
    recent_counts = closed_counts[split_idx:]
    hist_avg = sum(hist_counts) / len(hist_counts) if hist_counts else None
    recent_avg = sum(recent_counts) / len(recent_counts) if recent_counts else None

    # pct_change: recente vs histórico (only meaningful with >= 3 closed months)
    avg_pct: float | None = None
    if hist_avg and recent_avg and hist_avg > 0 and n_months >= 3:
        avg_pct = (recent_avg - hist_avg) / hist_avg * 100

    # ── Trend & health (closed months only) ──────────────────────────────────
    trend_label, trend_icon, trend_color, trend_desc = _compute_trend(closed_counts, avg_val)
    cv = (closed["count"].std() / avg_val) if (avg_val > 0 and n_months > 1) else 0.0
    health_label, health_emoji, health_color, health_desc = _compute_health(
        trend_label, last_count, avg_val, cv
    )

    # ── Header with inline summary ───────────────────────────────────────────
    # Icon, color and label always come from _compute_trend (same source as the
    # Tendência card) so they can never contradict each other. avg_pct is used
    # only to quantify the magnitude when it agrees with trend_label.
    summary_color = trend_color
    n_recent = len(recent_counts)
    if trend_label == "Crescimento" and avg_pct is not None:
        summary_txt = f"Crescimento de {abs(avg_pct):.0f}% nos últimos {n_recent} meses"
    elif trend_label == "Queda" and avg_pct is not None:
        summary_txt = f"Queda de {abs(avg_pct):.0f}% nos últimos {n_recent} meses"
    else:
        summary_txt = trend_desc

    st.markdown(
        f'<div style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\','
        f'Roboto,sans-serif;margin-bottom:16px;">'
        f'<div style="font-size:28px;font-weight:800;color:#0f172a;'
        f'letter-spacing:-0.5px;margin-bottom:2px;">Throughput</div>'
        f'<div style="font-size:13px;color:#64748b;margin-bottom:8px;">'
        f'Itens concluídos por mês</div>'
        f'<div style="font-size:13px;">'
        f'<span style="font-weight:600;color:{health_color};">'
        f'{health_emoji} Saúde: {health_label}</span>'
        f'<span style="color:#cbd5e1;margin:0 8px;">·</span>'
        f'<span style="font-weight:600;color:{summary_color};">'
        f'{trend_icon} {summary_txt}</span>'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    st.divider()

    # ── 7 KPI cards ──────────────────────────────────────────────────────────
    c1, c2, c3, c4, c5, c6, c7 = st.columns(7)

    # Card 1: Média/Mês — badge = variação recente vs histórico
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

    # Card 2: Total (closed months only; WIP count shown as badge)
    with c2:
        st.html(_card(
            "Total no Período",
            f'<span style="color:#0f172a;">{total}</span>',
            sub=f"{n_months} {'mês' if n_months == 1 else 'meses'} fechados",
            badge=f"+ {int(wip_row['count'])} em andamento" if wip_row is not None else "",
            badge_color="#94a3b8",
        ))

    # Card 3: Melhor mês — badge = % acima da média
    best_pct = (best_row["count"] - avg_val) / avg_val * 100 if avg_val > 0 else 0
    best_badge = f"↑ {best_pct:.0f}%" if best_pct >= 0.5 else "= média"
    best_badge_color = "#15803d" if best_pct >= 0.5 else "#94a3b8"
    with c3:
        st.html(_card(
            "Melhor Mês",
            f'<span style="color:#0f172a;">{int(best_row["count"])}</span>',
            sub=best_row["month_label"],
            badge=best_badge,
            badge_color=best_badge_color,
        ))

    # Card 4: Pior mês — badge = % abaixo da média
    worst_pct = (worst_row["count"] - avg_val) / avg_val * 100 if avg_val > 0 else 0
    worst_badge = f"↓ {abs(worst_pct):.0f}%" if worst_pct <= -0.5 else "= média"
    worst_badge_color = "#dc2626" if worst_pct <= -0.5 else "#94a3b8"
    with c4:
        st.html(_card(
            "Pior Mês",
            f'<span style="color:#0f172a;">{int(worst_row["count"])}</span>',
            sub=worst_row["month_label"],
            badge=worst_badge,
            badge_color=worst_badge_color,
        ))

    # Card 5: Tendência
    with c5:
        st.html(_card(
            "Tendência",
            f'<span style="color:{trend_color};">{trend_icon} {trend_label}</span>',
            sub=trend_desc,
        ))

    # Card 6: Saúde do Throughput
    with c6:
        st.html(_card(
            "Saúde",
            f'<span style="color:{health_color};">{health_emoji} {health_label}</span>',
            sub=health_desc,
        ))

    # Card 7: Previsibilidade — coeficiente de variação do throughput mensal
    pred_label, pred_emoji, pred_color = _compute_predictability(cv)
    with c7:
        st.html(_card(
            "Previsibilidade",
            f'<span style="color:{pred_color};">{pred_emoji} {pred_label}</span>',
            sub=f"Desvio histórico: {cv * 100:.0f}%",
        ))

    st.markdown("<div style='margin-top:8px;'></div>", unsafe_allow_html=True)

    # ── Bar chart (full width) with click-to-drill-down ──────────────────────
    # tp_last_clicked prevents the dialog from reopening automatically after
    # the user closes it (the Vega-Lite selection stays active across reruns).
    if "tp_last_clicked" not in st.session_state:
        st.session_state.tp_last_clicked = None

    bar_sel = alt.selection_point(name="bar_click", fields=["res_month"])

    # Closed bars: indigo; WIP bar: gray — is_wip column set in the split above.
    bars = (
        alt.Chart(monthly)
        .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X("month_label:N", sort=None, title=None,
                    axis=alt.Axis(labelAngle=-30, labelFontSize=12)),
            y=alt.Y("count:Q", title="Itens concluídos", axis=alt.Axis(grid=True)),
            color=alt.condition(
                alt.datum.is_wip,
                alt.value("#94a3b8"),   # in-progress month → gray
                alt.value("#6366f1"),   # closed months → indigo
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

    # Open detail dialog only when a NEW month is clicked.
    # Same-month click after dialog close is intentionally ignored — user
    # must click elsewhere to deselect before re-opening the same month.
    sel_items = (event.selection or {}).get("bar_click", [])
    clicked = sel_items[0].get("res_month") if sel_items else None
    if clicked and clicked != st.session_state.tp_last_clicked:
        st.session_state.tp_last_clicked = clicked
        _month_detail(clicked, filt, monthly)
    elif not clicked:
        st.session_state.tp_last_clicked = None

    # ── Horizontal type chart + Insights ────────────────────────────────────
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

        # 1. Peak month (closed months only — WIP excluded)
        best_i = closed.loc[closed["count"].idxmax()] if not closed.empty else monthly.iloc[0]
        bullets.append(
            f"Maior throughput em **{best_i['month_label']}** com {int(best_i['count'])} itens."
        )

        # 2. Most recent CLOSED month vs avg (WIP excluded)
        last_label = closed.iloc[-1]["month_label"] if not closed.empty else monthly.iloc[-1]["month_label"]
        last_pct = (last_count - avg_val) / avg_val * 100 if avg_val > 0 else 0
        direction = "acima" if last_pct >= 0 else "abaixo"
        bullets.append(
            f"Último mês fechado (**{last_label}**): {int(last_count)} itens "
            f"— {abs(last_pct):.0f}% {direction} da média ({avg_val:.1f})."
        )

        # 3. Dominant type
        if not type_counts.empty:
            top = type_counts.iloc[0]
            bullets.append(
                f"Tipo mais frequente: **{top['issuetype']}** "
                f"({top['pct']:.0f}% dos itens concluídos)."
            )

        # 4. Consecutive run above or below avg
        n_above = _consecutive_tail(closed_counts, lambda c: c > avg_val)
        n_below = _consecutive_tail(closed_counts, lambda c: c < avg_val)
        if n_above >= 2:
            bullets.append(f"**{n_above} meses consecutivos** acima da média do período.")
        elif n_below >= 2:
            bullets.append(f"**{n_below} meses consecutivos** abaixo da média do período.")

        for b in bullets:
            st.markdown(f"- {b}")

    # ── Diagnóstico da queda (heurístico) ────────────────────────────────────
    # Só aparece quando o mês mais recente cai de forma relevante vs. a média do
    # período. A decomposição é uma APROXIMAÇÃO HEURÍSTICA (ver _diagnose_drop),
    # não uma análise causal real.
    # drop_pct: last CLOSED month vs closed baseline — WIP month never triggers this
    drop_pct = (avg_val - last_count) / avg_val * 100 if avg_val > 0 else 0.0
    if drop_pct >= DROP_THRESHOLD_PCT:
        tp_by_month = dict(zip(closed["res_month"], closed["count"]))
        period_months = closed["res_month"].tolist()
        team_df = df if selected_team == "Todos" else df[df["team"] == selected_team]
        parts = _diagnose_drop(period_months, tp_by_month, team_df)
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

    # ── Last 50 completed items ───────────────────────────────────────────────
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
