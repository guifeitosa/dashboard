import altair as alt
import pandas as pd
import streamlit as st

from loader import load_jira_issues_from_csv
from metrics import calculate_metrics_summary

DATA_PATH = "data/jira_issues_synthetic.csv"


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
    changes["lead_time_days"] = (changes["resolutiondate"] - changes["created"]).dt.total_seconds() / 86400.0
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


def render_metric_card(label: str, value, delta_value, unit: str):
    if pd.isna(value) or value is None:
        value_text = "N/A"
    elif isinstance(value, (int, float)):
        value_text = f"{value:.1f}{unit}"
    else:
        value_text = str(value)

    delta_text = "N/A"
    if delta_value is not None and not pd.isna(delta_value):
        arrow = "▲" if delta_value > 0 else "▼" if delta_value < 0 else ""
        delta_text = f"{arrow} {abs(delta_value):.1f}%"

    st.metric(label, value_text, delta_text)


def build_charts(summary: pd.DataFrame, selected_team: str, cfr_target: float):
    if summary.empty:
        st.warning("Sem dados para o período selecionado.")
        return

    base = summary.copy()
    base["year_month"] = pd.Categorical(base["year_month"], ordered=True, categories=sorted(base["year_month"].unique()))

    st.markdown("### CFR por mês")
    cfr_chart = alt.Chart(base).mark_line(point=True).encode(
        x=alt.X("year_month:N", title="Mês"),
        y=alt.Y("cfr_percent:Q", title="CFR (%)"),
        color=alt.Color("team:N", scale=alt.Scale(scheme="set2"), legend=alt.Legend(title="Time")) if selected_team == "Todos" else alt.value("#1f77b4"),
    ).properties(height=320)

    target_rule = alt.Chart(pd.DataFrame({"y": [cfr_target]})).mark_rule(color="#db4c3f", strokeDash=[4, 4]).encode(y="y:Q")
    target_text = alt.Chart(pd.DataFrame({"y": [cfr_target]})).mark_text(dy=-10, color="#db4c3f").encode(y="y:Q", text=alt.value(f"Meta {cfr_target:.0f}%"))
    st.altair_chart(cfr_chart + target_rule + target_text, use_container_width=True)

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

    with st.container():
        col1, col2, col3, col4 = st.columns([2, 4, 3, 2])
        selected_team = col1.selectbox("Time", teams, index=0)
        selected_start = col2.select_slider("Período inicial", options=months, value=months[0])
        selected_end = col3.select_slider("Período final", options=months, value=months[-1])
        cfr_target = col4.number_input("Meta CFR (%)", min_value=0.0, value=15.0, step=1.0)

    if selected_start > selected_end:
        st.warning("Selecione um período válido: início anterior ao fim.")
        return

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
    monthly_df = monthly_df[(monthly_df["created"].dt.to_period("M").astype(str) >= selected_start) | (monthly_df["data_implantacao"].notna() & pd.to_datetime(monthly_df["data_implantacao"]).dt.to_period("M").astype(str) >= selected_start)]
    monthly_df = monthly_df[(monthly_df["created"].dt.to_period("M").astype(str) <= selected_end) | (monthly_df["data_implantacao"].notna() & pd.to_datetime(monthly_df["data_implantacao"]).dt.to_period("M").astype(str) <= selected_end)]
    monthly_metrics = build_monthly_aggregates(monthly_df)

    latest_month = max(months.index(selected_end), months.index(selected_start))
    latest_month = selected_end
    prev_index = months.index(latest_month) - 1
    prev_month = months[prev_index] if prev_index >= 0 else None

    current = monthly_metrics[monthly_metrics["year_month"] == latest_month].squeeze()
    previous = monthly_metrics[monthly_metrics["year_month"] == prev_month].squeeze() if prev_month is not None else None

    st.markdown("---")
    card1, card2, card3, card4 = st.columns(4)
    with card1:
        cfr_value = current["cfr_percent"] if not current.empty else None
        cfr_prev = previous["cfr_percent"] if previous is not None and not previous.empty else None
        cfr_change = compute_metric_change(cfr_value, cfr_prev)
        render_metric_card("CFR último mês", cfr_value, cfr_change, "%")
    with card2:
        mttr_value = current["mttr_hours"] if not current.empty else None
        mttr_prev = previous["mttr_hours"] if previous is not None and not previous.empty else None
        mttr_change = compute_metric_change(mttr_value, mttr_prev)
        render_metric_card("MTTR último mês", mttr_value, mttr_change, "h")
    with card3:
        lead_value = current["lead_time_days"] if not current.empty else None
        lead_prev = previous["lead_time_days"] if previous is not None and not previous.empty else None
        lead_change = compute_metric_change(lead_value, lead_prev)
        render_metric_card("Lead Time último mês", lead_value, lead_change, "d")
    with card4:
        deploy_value = current["deployment_count"] if not current.empty else 0
        deploy_prev = previous["deployment_count"] if previous is not None and not previous.empty else 0
        deploy_change = compute_metric_change(deploy_value, deploy_prev)
        render_metric_card("Deploys último mês", deploy_value, deploy_change, "")

    st.markdown("---")
    build_charts(filtered, selected_team, cfr_target)


if __name__ == "__main__":
    main()
