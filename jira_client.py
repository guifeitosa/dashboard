import base64
import os
from typing import Any, Dict, List

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

JIRA_BASE_URL = os.getenv("JIRA_BASE_URL")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")

if not JIRA_BASE_URL or not JIRA_EMAIL or not JIRA_API_TOKEN:
    raise EnvironmentError("Missing Jira credentials in .env")

AUTH_HEADER = {
    "Authorization": "Basic " + base64.b64encode(f"{JIRA_EMAIL}:{JIRA_API_TOKEN}".encode()).decode(),
    "Accept": "application/json",
    "Content-Type": "application/json",
}

SEARCH_URL = f"{JIRA_BASE_URL}/rest/api/3/search/jql"
FIELDS_URL = f"{JIRA_BASE_URL}/rest/api/3/field"


def _normalize_migrated(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        if value.endswith(" (migrated)"):
            return value[: -len(" (migrated)")].strip()
    return value


def get_custom_field_ids(field_names: List[str]) -> Dict[str, str]:
    response = requests.get(FIELDS_URL, headers=AUTH_HEADER)
    response.raise_for_status()
    fields = response.json()

    found = {}
    normalized_names = {name: _normalize_migrated(name) for name in field_names}
    for field in fields:
        field_name = _normalize_migrated(field.get("name"))
        for original_name, normalized_name in normalized_names.items():
            if field_name == normalized_name:
                found[original_name] = field["id"]
                break

    missing = [name for name in field_names if name not in found]
    if missing:
        raise ValueError(f"Custom field(s) not found in Jira: {', '.join(missing)}")
    return found


def fetch_all_issues(jql: str, fields: List[str]) -> List[Dict]:
    start_at = 0
    max_results = 50
    all_issues = []

    while True:
        params = {
            "jql": jql,
            "fields": ",".join(fields),
            "startAt": start_at,
            "maxResults": max_results,
        }
        response = requests.get(SEARCH_URL, headers=AUTH_HEADER, params=params)
        response.raise_for_status()
        data = response.json()

        all_issues.extend(data.get("issues", []))
        if start_at + data.get("maxResults", 0) >= data.get("total", 0):
            break
        start_at += data.get("maxResults", 0)

    return all_issues


def _normalize_migrated(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        if value.endswith(" (migrated)"):
            return value[: -len(" (migrated)")].strip()
    return value


def normalize_issue(issue: Dict, custom_fields: Dict[str, str]) -> Dict:
    key = issue.get("key")
    fields = issue.get("fields", {})
    issuetype = _normalize_migrated(fields.get("issuetype", {}).get("name"))
    status_obj = fields.get("status", {})
    status = _normalize_migrated(status_obj.get("name"))
    status_category = status_obj.get("statusCategory", {}).get("key")
    created = fields.get("created")
    resolutiondate = fields.get("resolutiondate")
    updated = fields.get("updated")

    if not resolutiondate and status_category == "done":
        resolutiondate = updated

    team_value = None
    implant_value = None
    team_field = custom_fields.get("Team")
    implant_field = custom_fields.get("Data de Implantação")
    if team_field:
        team_value = fields.get(team_field)
        if isinstance(team_value, dict):
            team_value = team_value.get("value") or team_value.get("name")
        team_value = _normalize_migrated(team_value)
    if implant_field:
        implant_value = fields.get(implant_field)
        if isinstance(implant_value, dict):
            implant_value = implant_value.get("value") or implant_value.get("name")
        implant_value = _normalize_migrated(implant_value)

    return {
        "key": key,
        "issuetype": issuetype,
        "team": team_value,
        "status": status,
        "created": created,
        "resolutiondate": resolutiondate,
        "data_implantacao": implant_value,
        "updated": updated,
    }


def load_issues_as_dataframe() -> pd.DataFrame:
    custom_fields = get_custom_field_ids(["Team", "Data de Implantação"])
    fields = ["issuetype", "status", "created", "resolutiondate", "updated"] + list(custom_fields.values())
    issues = fetch_all_issues("project = TD", fields)
    normalized = [normalize_issue(issue, custom_fields) for issue in issues]

    df = pd.DataFrame(normalized)
    df["created"] = pd.to_datetime(df["created"], errors="coerce", utc=True).dt.tz_convert(None)
    df["resolutiondate"] = pd.to_datetime(df["resolutiondate"], errors="coerce", utc=True).dt.tz_convert(None)
    df["data_implantacao"] = pd.to_datetime(df["data_implantacao"], errors="coerce", utc=True).dt.tz_convert(None)
    df["updated"] = pd.to_datetime(df["updated"], errors="coerce", utc=True).dt.tz_convert(None)
    df["team"] = df["team"].fillna("Unknown")
    df["year_month"] = df["created"].dt.to_period("M").astype(str)
    df["is_resolved"] = df["resolutiondate"].notna()

    return df
