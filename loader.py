import pandas as pd


def load_jira_issues_from_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={
        "key": str,
        "issuetype": str,
        "team": str,
        "status": str,
        "created": str,
        "resolutiondate": str,
        "data_implantacao": str,
    })
    df["created"] = pd.to_datetime(df["created"], errors="coerce")
    df["resolutiondate"] = pd.to_datetime(df["resolutiondate"], errors="coerce")
    df["data_implantacao"] = pd.to_datetime(df["data_implantacao"], errors="coerce").dt.date

    df["year_month"] = df["created"].dt.to_period("M").astype(str)
    df["is_resolved"] = df["resolutiondate"].notna()

    if "updated" in df.columns:
        df["updated"] = pd.to_datetime(df["updated"], errors="coerce")

    return df
