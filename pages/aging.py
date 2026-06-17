import datetime

import pandas as pd
import streamlit as st

from loader import load_jira_issues_from_csv
from squad_health import render_squad_health

DATA_PATH = "data/jira_issues_synthetic.csv"


def _row_color(row: pd.Series) -> list[str]:
    d = row["Dias em Aberto"]
    if d < 7:
        bg = "background-color: rgba(21,128,61,0.10); color: #14532d;"
    elif d <= 30:
        bg = "background-color: rgba(202,138,4,0.10); color: #713f12;"
    else:
        bg = "background-color: rgba(220,38,38,0.10); color: #7f1d1d;"
    return [bg] * len(row)


def _card(label: str, value: str, color: str = "#0f172a") -> str:
    return (
        f'<div style="background:white;border-radius:12px;padding:18px 22px;'
        f'box-shadow:0 1px 4px rgba(0,0,0,0.07);border:1px solid #f1f5f9;'
        f'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,sans-serif;">'
        f'<div style="font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;'
        f'letter-spacing:.07em;margin-bottom:6px;">{label}</div>'
        f'<div style="font-size:30px;font-weight:800;color:{color};line-height:1.1;">{value}</div>'
        f'</div>'
    )


def main():
    render_squad_health()

    df = load_jira_issues_from_csv(DATA_PATH)

    # dias_parado mede dias desde a CRIAÇÃO, não desde a última mudança de status —
    # aproximação a ser refinada quando o changelog do Jira estiver disponível.
    open_issues = df[~df["is_resolved"]].copy()
    today = pd.Timestamp(datetime.date.today())
    open_issues["dias_parado"] = (today - open_issues["created"]).dt.days

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

    # ── Page title ──────────────────────────────────────────────────────────
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

    # ── KPI cards ────────────────────────────────────────────────────────────
    total_open = len(filt)
    avg_age = filt["dias_parado"].mean()
    over7 = int((filt["dias_parado"] >= 7).sum())
    over30 = int((filt["dias_parado"] > 30).sum())

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.html(_card("Total em Aberto", str(total_open)))
    with c2:
        st.html(_card("Idade Média", f"{avg_age:.0f}d"))
    with c3:
        st.html(_card("≥ 7 dias", str(over7), "#ca8a04"))
    with c4:
        st.html(_card("> 30 dias", str(over30), "#dc2626"))

    st.markdown("<div style='margin-top:16px;'></div>", unsafe_allow_html=True)

    # ── Colored table ────────────────────────────────────────────────────────
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


main()
