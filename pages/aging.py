import datetime

import altair as alt
import pandas as pd
import streamlit as st

from loader import load_jira_issues_from_csv
from squad_health import render_squad_health

DATA_PATH = "data/jira_issues_synthetic.csv"

OVER_REP_THRESHOLD = 15  # pp


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

    df = load_jira_issues_from_csv(DATA_PATH)
    has_updated = "updated" in df.columns and df["updated"].notna().any()

    open_issues = df[~df["is_resolved"]].copy()
    today = pd.Timestamp(datetime.date.today())
    open_issues["dias_parado"] = (today - open_issues["created"]).dt.days
    if has_updated:
        open_issues["dias_sem_update"] = (today - open_issues["updated"]).dt.days

    st.sidebar.title("Filtros")
    selected_team = st.sidebar.selectbox(
        "Time",
        ["Todos"] + sorted(open_issues["team"].dropna().unique().tolist()),
    )
    selected_type = st.sidebar.selectbox(
        "Tipo",
        ["Todos"] + sorted(open_issues["issuetype"].dropna().unique().tolist()),
    )

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

    # ── KPI cards ─────────────────────────────────────────────────────────────
    total_open = len(filt)
    avg_age = filt["dias_parado"].mean()
    over7 = int((filt["dias_parado"] >= 7).sum())
    over30 = int((filt["dias_parado"] > 30).sum())

    if has_updated and "dias_sem_update" in filt.columns:
        sem_mov = int((filt["dias_sem_update"] > 14).sum())
        sem_mov_pct = sem_mov / total_open * 100 if total_open else 0
        sem_mov_sub = f"{sem_mov_pct:.0f}% dos itens abertos"
        sem_mov_color = (
            "#dc2626" if sem_mov_pct >= 40 else
            "#ca8a04" if sem_mov_pct >= 20 else
            "#0f172a"
        )
        sem_mov_val = str(sem_mov)
    else:
        sem_mov_val = "—"
        sem_mov_sub = "campo 'updated' indisponível"
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

    d = filt["dias_parado"]
    band_rows = [
        {"faixa": "0–7 dias",   "count": int((d < 7).sum()),                         "color": "#15803d"},
        {"faixa": "7–14 dias",  "count": int(((d >= 7)  & (d < 14)).sum()),          "color": "#ca8a04"},
        {"faixa": "14–30 dias", "count": int(((d >= 14) & (d <= 30)).sum()),         "color": "#ca8a04"},
        {"faixa": "30–60 dias", "count": int(((d > 30)  & (d <= 60)).sum()),         "color": "#dc2626"},
        {"faixa": "60+ dias",   "count": int((d > 60).sum()),                         "color": "#991b1b"},
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

    # ── Lista de itens ────────────────────────────────────────────────────────
    _section_label("Lista de Itens em Aberto")

    table = filt[["key", "issuetype", "team", "created", "dias_parado"]].copy()
    table["created"] = table["created"].dt.strftime("%d/%m/%Y")
    table = table.rename(columns={
        "key": "Chave",
        "issuetype": "Tipo",
        "team": "Time",
        "created": "Criado em",
        "dias_parado": "Dias em Aberto",
    })
    styled = table.style.apply(_row_color, axis=1)
    st.dataframe(styled, use_container_width=True, hide_index=True)
    st.caption(
        "Dias em Aberto = dias desde a criação do item, não desde a última mudança de status. "
        "Aproximação a ser refinada quando o changelog do Jira estiver disponível."
    )

    # ── Diagnóstico: sobre-representação na faixa crítica (> 30 dias) ─────────
    red = filt[filt["dias_parado"] > 30]
    if red.empty or total_open == 0:
        return

    _section_label("Diagnóstico — Fatores sobre-representados na faixa crítica (> 30 dias)")

    factors = []
    for dim, col in [("Tipo", "issuetype"), ("Time", "team")]:
        for val in filt[col].dropna().unique():
            n_total = int((filt[col] == val).sum())
            n_red   = int((red[col] == val).sum())
            pct_total = n_total / total_open * 100
            pct_red   = n_red   / len(red)   * 100
            over_rep  = pct_red - pct_total
            if over_rep >= OVER_REP_THRESHOLD and n_red >= 1:
                factors.append({
                    "dim": dim,
                    "val": val,
                    "n_red": n_red,
                    "pct_red": pct_red,
                    "pct_total": pct_total,
                    "over_rep": over_rep,
                })

    if not factors:
        st.markdown(
            '<div style="font-size:13px;color:#64748b;">'
            'Nenhum Tipo ou Time com sobre-representação relevante (≥ 15 p.p.) na faixa crítica.'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    factors.sort(key=lambda f: f["over_rep"], reverse=True)
    for f in factors:
        n_label = f"{f['n_red']} {'item' if f['n_red'] == 1 else 'itens'}"
        st.markdown(
            f"- **{f['dim']}: {f['val']}** — "
            f"{f['pct_red']:.0f}% dos itens críticos vs. {f['pct_total']:.0f}% do total de abertos "
            f"(**+{f['over_rep']:.0f} p.p.** de sobre-representação, {n_label} em vermelho)."
        )


main()
