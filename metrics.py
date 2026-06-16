import pandas as pd


def _ensure_group_columns(df: pd.DataFrame) -> pd.DataFrame:
    if "team" not in df.columns or "year_month" not in df.columns:
        raise ValueError("DataFrame must contain 'team' and 'year_month' columns")
    return df


def calculate_mttr(df: pd.DataFrame) -> pd.DataFrame:
    df = _ensure_group_columns(df)
    incidents = df[
        (df["issuetype"] == "Incidente") &
        (df["is_resolved"])
    ].copy()
    incidents["mttr_hours"] = (incidents["resolutiondate"] - incidents["created"]).dt.total_seconds() / 3600.0
    result = incidents.groupby(["team", "year_month"], as_index=False)["mttr_hours"].mean()
    return result


def calculate_cfr(df: pd.DataFrame) -> pd.DataFrame:
    df = _ensure_group_columns(df)
    incidents = df[df["issuetype"] == "Incidente"].groupby(["team", "year_month"], as_index=False)["key"].count()
    incidents = incidents.rename(columns={"key": "incidente_count"})

    gmud_deploys = df[df["issuetype"] == "GMUD"].copy()
    gmud_deploys["deploy_date"] = gmud_deploys["data_implantacao"].fillna(gmud_deploys["resolutiondate"])
    gmud_deploys = gmud_deploys[gmud_deploys["deploy_date"].notna()]
    gmud_deploys["deploy_month"] = pd.to_datetime(gmud_deploys["deploy_date"]).dt.to_period("M").astype(str)
    gmud_deploys = gmud_deploys.groupby(["team", "deploy_month"], as_index=False)["key"].count()
    gmud_deploys = gmud_deploys.rename(columns={"key": "gmud_deploy_count", "deploy_month": "year_month"})

    combined = pd.merge(incidents, gmud_deploys, on=["team", "year_month"], how="outer")
    combined["incidente_count"] = combined["incidente_count"].fillna(0)
    combined["gmud_deploy_count"] = combined["gmud_deploy_count"].fillna(0)
    combined["cfr_percent"] = combined.apply(
        lambda row: None if row["gmud_deploy_count"] == 0 else (row["incidente_count"] / row["gmud_deploy_count"]) * 100,
        axis=1,
    )
    return combined[["team", "year_month", "cfr_percent"]]


def calculate_lead_time_for_changes(df: pd.DataFrame) -> pd.DataFrame:
    df = _ensure_group_columns(df)
    change_issue_types = {"story", "bug", "task", "história", "historia", "tarefa"}
    changes = df[
        df["issuetype"].astype(str).str.lower().isin(change_issue_types) &
        (df["is_resolved"])
    ].copy()
    changes["lead_time_days"] = (changes["resolutiondate"] - changes["created"]).dt.total_seconds() / 86400.0
    result = changes.groupby(["team", "year_month"], as_index=False)["lead_time_days"].mean()
    return result


def calculate_deployment_frequency(df: pd.DataFrame) -> pd.DataFrame:
    df = _ensure_group_columns(df)
    deployments = df[df["issuetype"] == "GMUD"].copy()
    deployments["deployment_date"] = deployments["data_implantacao"].fillna(deployments["resolutiondate"])
    deployments = deployments[deployments["deployment_date"].notna()]
    deployments["deployment_month"] = pd.to_datetime(deployments["deployment_date"]).dt.to_period("M").astype(str)
    result = deployments.groupby(["team", "deployment_month"], as_index=False)["key"].count()
    result = result.rename(columns={"key": "deployment_count", "deployment_month": "year_month"})
    return result


def calculate_metrics_summary(df: pd.DataFrame) -> pd.DataFrame:
    mttr = calculate_mttr(df)
    cfr = calculate_cfr(df)
    lead_time = calculate_lead_time_for_changes(df)
    deploy = calculate_deployment_frequency(df)

    summary = pd.merge(mttr, cfr, on=["team", "year_month"], how="outer")
    summary = pd.merge(summary, lead_time, on=["team", "year_month"], how="outer")
    summary = pd.merge(summary, deploy, on=["team", "year_month"], how="outer")

    summary = summary.sort_values(["team", "year_month"]).reset_index(drop=True)
    summary["mttr_hours"] = summary["mttr_hours"].round(1)
    summary["lead_time_days"] = summary["lead_time_days"].round(1)
    summary["deployment_count"] = summary["deployment_count"].fillna(0).astype(int)

    return summary
