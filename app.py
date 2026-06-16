import altair as alt
import pandas as pd
import streamlit as st

from loader import load_jira_issues_from_csv
from metrics import calculate_metrics_summary

DATA_PATH = "data/jira_issues_synthetic.csv"


def _business_days_between(start, end):
    start_date = pd.to_datetime(start).date()
    end_date = pd.to_datetime(end).date()
    if pd.isna(start_date) or pd.isna(end_date) or end_date < start_date:
        return float("nan")
    return len(pd.bdate_range(start=start_date, end=end_date))


def build_monthly_aggregates(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["created_month"] = df["created"].dt.to_period("M").astype(str)
    df["deploy_month"] = pd.to_datetime(df["data_implantacao"]).dt.to_period("M").astype(str)

    incidents = df[df["issuetype"] == "Incidente"].copy()
    incidents["mttr_hours"] = (incidents["resolutiondate"] - incidents["created"]).dt.total_seconds() / 3600.0
    mttr = incidents.groupby("created_month")["mttr_hours"].mean()

    incident_counts = incidents.groupby("created_month")["key"].count()
    gmud_deploys = df[(df["issuetype"] == "GMUD") & df["data_implantacao"].notna()].groupby("deploy_month")["key"].count()
    cfr = (incident_counts / gmud_deploys.reindex(incident_counts.index)).replace([pd.NA, float("inf"), float("nan")], pd.NA) * 100

    changes = df[(df["issuetype"].isin(["Story", "Bug", "Task"])) & (df["is_resolved"])].copy()
    changes["lead_time_days"] = changes.apply(
        lambda row: _business_days_between(row["created"], row["resolutiondate"]),
        axis=1,
    )
    lead_time = changes.groupby("created_month")["lead_time_days"].mean()

    deployments = gmud_deploys

    all_months = sorted(set(mttr.index).union(cfr.index).union(lead_time.index).union(deployments.index))
    monthly = pd.DataFrame({"year_month": all_months})
    monthly["mttr_hours"] = monthly["year_month"].map(mttr.to_dict())
    monthly["cfr_percent"] = monthly["year_month"].map(cfr.to_dict())
    monthly["lead_time_days"] = monthly["year_month"].map(lead_time.to_dict())
    monthly["deployment_count"] = monthly["year_month"].map(deployments.to_dict()).fillna(0).astype(int)
    return monthly


def compute_metric_change(current, previous):
    if pd.isna(current) or pd.isna(previous) or previous == 0:
        return None
    return (current - previous) / abs(previous) * 100


def render_metric_card(label: str, value, delta_value, unit: str, better_when_lower: bool = True, target: float | None = None):
    if pd.isna(value) or value is None:
        value_text = "N/A"
    elif isinstance(value, (int, float)):
        value_text = f"{value:.1f}{unit}"
    else:
        value_text = str(value)

    if delta_value is None or pd.isna(delta_value):
        delta_text = "Sem dado anterior"
        delta_color = "normal"
    else:
        if better_when_lower:
            arrow = "▼" if delta_value < 0 else "▲" if delta_value > 0 else ""
            delta_color = "inverse"
        else:
            arrow = "▲" if delta_value > 0 else "▼" if delta_value < 0 else ""
            delta_color = "normal"
        delta_text = f"{arrow} {abs(delta_value):.1f}%"

    st.metric(label, value_text, delta_text, delta_color=delta_color)

    if delta_value is None or pd.isna(delta_value):
        st.write("Sem dado anterior")
    elif target is not None and label.startswith("CFR"):
        if isinstance(value, (int, float)) and value <= target:
            st.write("Dentro da meta")
        else:
            st.write("Acima da meta")


def build_charts(summary: pd.DataFrame, selected_team: str, cfr_target: float):
    if summary.empty:
        st.warning("Sem dados para o período selecionado.")
        return

    base = summary.copy()
    base["year_month"] = pd.Categorical(base["year_month"], ordered=True, categories=sorted(base["year_month"].unique()))

    st.markdown("### 📊 CFR por mês")
    cfr_chart = alt.Chart(base).mark_line(point=True).encode(
        x=alt.X("year_month:N", title="Mês"),
        y=alt.Y("cfr_percent:Q", title="CFR (%)", scale=alt.Scale(domain=[0, 150], clamp=True)),
        color=alt.Color("team:N", scale=alt.Scale(scheme="set2"), legend=alt.Legend(title="Time")) if selected_team == "Todos" else alt.value("#1f77b4"),
        tooltip=[
            alt.Tooltip("team:N", title="Time"),
            alt.Tooltip("year_month:N", title="Mês"),
            alt.Tooltip("cfr_percent:Q", title="CFR (%)", format=".1f"),
            alt.Tooltip("gmud_deploy_count:Q", title="GMUDs"),
            alt.Tooltip("incidente_count:Q", title="Incidentes"),
            alt.Tooltip("deployment_count:Q", title="Deploys"),
        ],
    ).properties(height=320)

    target_rule = alt.Chart(pd.DataFrame({"y": [cfr_target]})).mark_rule(color="#db4c3f", strokeDash=[4, 4]).encode(y="y:Q")
    target_text = alt.Chart(pd.DataFrame({"y": [cfr_target]})).mark_text(dy=-10, color="#db4c3f").encode(y="y:Q", text=alt.value(f"Meta {cfr_target:.0f}%"))

    overflow_annotation = alt.Chart(base[base["cfr_percent"] > 150]).mark_text(dy=-10, color="#db4c3f").encode(
        x=alt.X("year_month:N"),
        y=alt.value(150),
        text=alt.value(">150%"),
    ) if not base[base["cfr_percent"] > 150].empty else None

    chart = cfr_chart + target_rule + target_text
    if overflow_annotation is not None:
        chart = chart + overflow_annotation

    st.altair_chart(chart, use_container_width=True)

    st.markdown("### MTTR por mês")
    mttr_chart = alt.Chart(base).mark_bar().encode(
        x=alt.X("year_month:N", title="Mês"),
        y=alt.Y("mttr_hours:Q", title="MTTR (horas)"),
        color=alt.Color("team:N", scale=alt.Scale(scheme="set2"), legend=alt.Legend(title="Time")) if selected_team == "Todos" else alt.value("#2c7fb8"),
    ).properties(height=320)
    st.altair_chart(mttr_chart, use_container_width=True)

    st.markdown("### Lead Time for Changes por mês")
    if selected_team == "Todos":
        lead_chart = alt.Chart(base).mark_line(point=True).encode(
            x=alt.X("year_month:N", title="Mês"),
            y=alt.Y("lead_time_days:Q", title="Lead Time (dias)"),
            detail="team:N",
            color=alt.Color("team:N", scale=alt.Scale(scheme="set2"), legend=alt.Legend(title="Time")),
        ).properties(height=320)
    else:
        lead_chart = alt.Chart(base).mark_line(point=True, color="#2a9d8f").encode(
            x=alt.X("year_month:N", title="Mês"),
            y=alt.Y("lead_time_days:Q", title="Lead Time (dias)"),
        ).properties(height=320)
    st.altair_chart(lead_chart, use_container_width=True)

    st.markdown("### Deployment Frequency por mês")
    deploy_chart = alt.Chart(base).mark_bar().encode(
        x=alt.X("year_month:N", title="Mês"),
        y=alt.Y("deployment_count:Q", title="GMUDs implantadas"),
        color=alt.Color("team:N", scale=alt.Scale(scheme="set2"), legend=alt.Legend(title="Time")) if selected_team == "Todos" else alt.value("#e76f51"),
    ).properties(height=320)
    st.altair_chart(deploy_chart, use_container_width=True)


def main():
    st.set_page_config(page_title="Dashboard de Métricas de Engenharia", layout="wide")
    st.title("Dashboard Interno de Métricas de Engenharia")
    st.markdown("Painel local com métricas de Jira sintético: CFR, MTTR, Lead Time e Deployment Frequency.")

    df = load_jira_issues_from_csv(DATA_PATH)
    summary = calculate_metrics_summary(df)

    teams = ["Todos"] + sorted(df["team"].unique().tolist())
    months = sorted(summary["year_month"].unique().tolist())

    st.sidebar.title("Filtros")
    st.sidebar.markdown("Selecione o time, período e meta CFR para ajustar o dashboard.")
    selected_team = st.sidebar.selectbox("Time", teams, index=0)
    selected_start = st.sidebar.select_slider("Período inicial", options=months, value=months[0])
    selected_end = st.sidebar.select_slider("Período final", options=months, value=months[-1])
    cfr_target = st.sidebar.number_input("Meta CFR (%)", min_value=0.0, value=15.0, step=1.0)

    if selected_start > selected_end:
        st.warning("Selecione um período válido: início anterior ao fim.")
        return

    st.markdown("---")
    st.markdown("## 📋 Visão Geral")

    filtered = summary[(summary["year_month"] >= selected_start) & (summary["year_month"] <= selected_end)].copy()
    if selected_team != "Todos":
        filtered = filtered[filtered["team"] == selected_team]

    filtered = filtered.sort_values(["team", "year_month"])

    if filtered.empty:
        st.error("Nenhum dado disponível para o filtro selecionado.")
        return

    monthly_df = df.copy()
    if selected_team != "Todos":
        monthly_df = monthly_df[monthly_df["team"] == selected_team]
    monthly_df = monthly_df[
        (monthly_df["created"].dt.to_period("M").astype(str) >= selected_start)
        | (
            (monthly_df["data_implantacao"].notna())
            & (pd.to_datetime(monthly_df["data_implantacao"]).dt.to_period("M").astype(str) >= selected_start)
        )
    ]
    monthly_df = monthly_df[
        (monthly_df["created"].dt.to_period("M").astype(str) <= selected_end)
        | (
            (monthly_df["data_implantacao"].notna())
            & (pd.to_datetime(monthly_df["data_implantacao"]).dt.to_period("M").astype(str) <= selected_end)
        )
    ]
    monthly_metrics = build_monthly_aggregates(monthly_df)

    available_months = sorted(filtered["year_month"].unique().tolist())
    current_month = selected_end if selected_end in available_months else available_months[-1]
    current_index = available_months.index(current_month)
    previous_month = available_months[current_index - 1] if current_index > 0 else None

    current = filtered[filtered["year_month"] == current_month].squeeze()
    previous = filtered[filtered["year_month"] == previous_month].squeeze() if previous_month is not None else None

    st.markdown("---")
    st.markdown("## 📊 KPIs de desempenho")
    card1, card2, card3, card4 = st.columns(4)
    with card1:
        cfr_value = current["cfr_percent"] if not current.empty else None
        cfr_prev = previous["cfr_percent"] if previous is not None and not previous.empty else None
        cfr_change = compute_metric_change(cfr_value, cfr_prev)
        render_metric_card("CFR último mês", cfr_value, cfr_change, "%", better_when_lower=True, target=cfr_target)
    with card2:
        mttr_value = current["mttr_hours"] if not current.empty else None
        mttr_prev = previous["mttr_hours"] if previous is not None and not previous.empty else None
        mttr_change = compute_metric_change(mttr_value, mttr_prev)
        render_metric_card("MTTR último mês", mttr_value, mttr_change, "h", better_when_lower=True)
    with card3:
        lead_value = current["lead_time_days"] if not current.empty else None
        lead_prev = previous["lead_time_days"] if previous is not None and not previous.empty else None
        lead_change = compute_metric_change(lead_value, lead_prev)
        render_metric_card("Lead Time último mês", lead_value, lead_change, "d", better_when_lower=True)
    with card4:
        deploy_value = current["deployment_count"] if not current.empty else 0
        deploy_prev = previous["deployment_count"] if previous is not None and not previous.empty else 0
        deploy_change = compute_metric_change(deploy_value, deploy_prev)
        render_metric_card("Deploys último mês", deploy_value, deploy_change, "", better_when_lower=False)

    st.markdown("---")
    st.markdown("## 📌 Consolidados")
    avg_df = filtered.copy()
    avg_cfr = avg_df["cfr_percent"].mean()
    avg_mttr = avg_df["mttr_hours"].mean()
    avg_lead = avg_df["lead_time_days"].mean()

    card5, card6, card7 = st.columns(3)
    with card5:
        st.metric("CFR médio no período", f"{avg_cfr:.1f}%" if pd.notna(avg_cfr) else "N/A")
    with card6:
        st.metric("MTTR médio no período", f"{avg_mttr:.1f}h" if pd.notna(avg_mttr) else "N/A")
    with card7:
        st.metric("Lead Time médio no período", f"{avg_lead:.1f}d úteis" if pd.notna(avg_lead) else "N/A")

    st.markdown("---")
    build_charts(filtered, selected_team, cfr_target)


if __name__ == "__main__":
    main()
