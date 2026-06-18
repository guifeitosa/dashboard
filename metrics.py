import pandas as pd

# GMUDs that reached a terminal deployment status.
# "Implantado com Falha" counts as a failure for CFR; both count as total deploys.
_GMUD_FAIL: frozenset[str] = frozenset({"implantado com falha"})
_GMUD_TERMINAL: frozenset[str] = frozenset({"implantado com sucesso", "implantado com falha"})


def _ensure_group_columns(df: pd.DataFrame) -> pd.DataFrame:
    if "team" not in df.columns or "year_month" not in df.columns:
        raise ValueError("DataFrame must contain 'team' and 'year_month' columns")
    return df


def _gmud_terminal_df(df: pd.DataFrame) -> pd.DataFrame:
    """Return terminal GMUDs with a '_deploy_month' column based on resolutiondate."""
    gmud = df[df["issuetype"] == "GMUD"].copy()
    if gmud.empty:
        gmud["_status_norm"] = pd.Series(dtype=str)
        gmud["_deploy_month"] = pd.Series(dtype=str)
        return gmud
    gmud["_status_norm"] = gmud["status"].str.strip().str.lower()
    terminal = gmud[gmud["_status_norm"].isin(_GMUD_TERMINAL)].copy()
    if terminal.empty:
        terminal["_deploy_month"] = pd.Series(dtype=str)
        return terminal
    # Use resolutiondate month when available; fall back to created-based year_month.
    res = pd.to_datetime(terminal["resolutiondate"], errors="coerce")
    terminal["_deploy_month"] = res.dt.to_period("M").astype(str).where(res.notna(), terminal["year_month"])
    return terminal


def calculate_mttr(df: pd.DataFrame) -> pd.DataFrame:
    df = _ensure_group_columns(df)
    incidents = df[
        (df["issuetype"] == "Incidente") &
        (df["is_resolved"])
    ].copy()
    incidents["mttr_hours"] = (incidents["resolutiondate"] - incidents["created"]).dt.total_seconds() / 3600.0
    result = incidents.groupby(["team", "year_month"], as_index=False).agg(
        mttr_hours=("mttr_hours", "mean"),
        incidente_count=("key", "count"),
    )
    return result


def calculate_cfr(df: pd.DataFrame) -> pd.DataFrame:
    """CFR = GMUDs with 'Implantado com Falha' / all terminal GMUDs, grouped by deploy month."""
    _EMPTY = pd.DataFrame(columns=["team", "year_month", "cfr_percent", "gmud_deploy_count", "gmud_fail_count"])
    df = _ensure_group_columns(df)

    terminal = _gmud_terminal_df(df)
    if terminal.empty:
        return _EMPTY

    total = terminal.groupby(["team", "_deploy_month"], as_index=False)["key"].count()
    total = total.rename(columns={"key": "gmud_deploy_count", "_deploy_month": "year_month"})

    failed = terminal[terminal["_status_norm"].isin(_GMUD_FAIL)]
    if not failed.empty:
        fail_count = failed.groupby(["team", "_deploy_month"], as_index=False)["key"].count()
        fail_count = fail_count.rename(columns={"key": "gmud_fail_count", "_deploy_month": "year_month"})
        combined = pd.merge(total, fail_count, on=["team", "year_month"], how="left")
    else:
        combined = total.copy()
        combined["gmud_fail_count"] = 0

    combined["gmud_fail_count"] = combined["gmud_fail_count"].fillna(0).astype(int)
    combined["cfr_percent"] = (combined["gmud_fail_count"] / combined["gmud_deploy_count"]) * 100

    return combined[["team", "year_month", "cfr_percent", "gmud_deploy_count", "gmud_fail_count"]]


def _business_days_between(start, end):
    start_date = pd.to_datetime(start).date()
    end_date = pd.to_datetime(end).date()
    if pd.isna(start_date) or pd.isna(end_date) or end_date < start_date:
        return float("nan")
    return len(pd.bdate_range(start=start_date, end=end_date))


def calculate_lead_time_for_changes(df: pd.DataFrame) -> pd.DataFrame:
    df = _ensure_group_columns(df)
    change_issue_types = {"story", "bug", "task", "história", "historia", "tarefa"}
    changes = df[
        df["issuetype"].astype(str).str.lower().isin(change_issue_types) &
        (df["is_resolved"])
    ].copy()
    changes["lead_time_days"] = changes.apply(
        lambda row: _business_days_between(row["created"], row["resolutiondate"]),
        axis=1,
    )
    result = changes.groupby(["team", "year_month"], as_index=False)["lead_time_days"].mean()
    return result


def calculate_deployment_frequency(df: pd.DataFrame) -> pd.DataFrame:
    """Deployment count = terminal GMUDs per team per month, grouped by resolutiondate month."""
    _EMPTY = pd.DataFrame(columns=["team", "year_month", "deployment_count"])
    df = _ensure_group_columns(df)

    terminal = _gmud_terminal_df(df)
    if terminal.empty:
        return _EMPTY

    result = terminal.groupby(["team", "_deploy_month"], as_index=False)["key"].count()
    result = result.rename(columns={"key": "deployment_count", "_deploy_month": "year_month"})
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
    summary["incidente_count"] = summary["incidente_count"].fillna(0).astype(int)
    summary["gmud_fail_count"] = summary["gmud_fail_count"].fillna(0).astype(int)
    summary["gmud_deploy_count"] = summary["gmud_deploy_count"].fillna(0).astype(int)

    return summary


def aggregate_metrics_by_month(summary_df: pd.DataFrame, year_month: str) -> dict:
    summary_df = summary_df.copy()
    for col in ("incidente_count", "gmud_fail_count", "gmud_deploy_count", "deployment_count"):
        if col not in summary_df.columns:
            summary_df[col] = 0
        summary_df[col] = summary_df[col].fillna(0).astype(int)

    month_df = summary_df[summary_df["year_month"] == year_month]
    if month_df.empty:
        return {}

    total_failures = month_df["gmud_fail_count"].sum()
    total_gmuds = month_df["gmud_deploy_count"].sum()
    cfr = None if total_gmuds == 0 else (total_failures / total_gmuds) * 100

    mttr = None
    total_incidents = month_df["incidente_count"].sum()
    if total_incidents > 0:
        mttr = (month_df["mttr_hours"] * month_df["incidente_count"]).sum() / total_incidents

    lead_time = month_df["lead_time_days"].mean()
    deployment_count = month_df["deployment_count"].sum()

    return {
        "cfr_percent": cfr,
        "mttr_hours": mttr,
        "lead_time_days": lead_time,
        "deployment_count": deployment_count,
    }
