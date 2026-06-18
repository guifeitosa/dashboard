import base64
import os
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
from dotenv import load_dotenv

from core_metrics import TERMINAL_STATUSES

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
    for field in fields:
        field_name = _normalize_migrated(field.get("name"))
        for wanted in field_names:
            if field_name == _normalize_migrated(wanted):
                found[wanted] = field["id"]
                break

    missing = [name for name in field_names if name not in found]
    if missing:
        raise ValueError(f"Custom field(s) not found in Jira: {', '.join(missing)}")
    return found


def fetch_all_issues(
    jql: str,
    fields: List[str],
    expand: Optional[str] = None,
    timeout: int = 60,
) -> List[Dict]:
    """
    Paginate through Jira search results.

    Pass expand='changelog' to include the changelog in every issue.
    Page size is automatically halved when expand is set because each
    response is significantly larger; this keeps individual request
    payloads manageable and prevents silent HTTP timeouts.

    timeout: per-request timeout in seconds (default 60).
    """
    # Smaller pages when changelog is included — response payload is ~10x larger
    max_results = 25 if expand else 50
    all_issues: List[Dict] = []
    seen_keys: set = set()
    # /rest/api/3/search/jql uses nextPageToken; /rest/api/3/search uses startAt.
    # We support both: prefer nextPageToken when present, fall back to startAt.
    page_token: Optional[str] = None
    start_at: int = 0

    while True:
        params: Dict[str, Any] = {
            "jql": jql,
            "fields": ",".join(fields),
            "maxResults": max_results,
        }
        if expand:
            params["expand"] = expand
        if page_token:
            params["nextPageToken"] = page_token
        else:
            params["startAt"] = start_at

        response = requests.get(SEARCH_URL, headers=AUTH_HEADER, params=params, timeout=timeout)
        response.raise_for_status()
        data = response.json()

        page_issues = data.get("issues", [])

        # Stop: empty page
        if not page_issues:
            break

        # Stop: duplicate-key guard — last-resort for endpoints that wrap around
        page_keys = {issue.get("key") for issue in page_issues}
        if page_keys & seen_keys:
            break
        seen_keys |= page_keys

        all_issues.extend(page_issues)

        total = data.get("total") or 0
        print(f"    [{len(all_issues)}/{total or '?'}] issues fetched so far...", flush=True)

        # /rest/api/3/search/jql uses nextPageToken pagination (not startAt/total).
        # The parameter name in the request must also be "nextPageToken" (not "pageToken").
        next_token = data.get("nextPageToken")
        is_last = data.get("isLast", False)

        if is_last or not next_token:
            # Fallback for /rest/api/3/search (offset-based) or genuine last page
            if total > 0 and len(all_issues) < total:
                start_at += len(page_issues)
            else:
                break
        else:
            page_token = next_token

    return all_issues


def normalize_issue(issue: Dict, custom_fields: Dict[str, str]) -> Dict:
    key = issue.get("key")
    fields = issue.get("fields", {})
    issuetype = _normalize_migrated(fields.get("issuetype", {}).get("name"))
    status_obj = fields.get("status", {})
    status = _normalize_migrated(status_obj.get("name"))
    created = fields.get("created")
    resolutiondate = fields.get("resolutiondate")
    updated = fields.get("updated")

    # Jira sometimes leaves resolutiondate null for migrated workflows even when
    # the issue is in a done state (statusCategory returns "indeterminate").
    # Use the explicit terminal-status list instead of statusCategory.key == "done".
    if not resolutiondate and (status or "").lower() in TERMINAL_STATUSES:
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


def extract_status_transitions(issue: Dict, team: Optional[str] = None) -> List[Dict]:
    """
    Extract status-change entries from a Jira issue's changelog.

    Each history entry can contain multiple field changes; we keep only
    those where field == "status". Non-status items (e.g. assignee changes)
    are silently ignored.

    Returns a list of dicts with keys:
        issue_key, from_status, to_status, changed_at (ISO string), team
    """
    changelog = issue.get("changelog", {})
    histories = changelog.get("histories", [])
    transitions: List[Dict] = []

    for history in histories:
        changed_at_str = history.get("created")
        if not changed_at_str:
            continue

        for item in history.get("items", []):
            if item.get("field") != "status":
                continue

            transitions.append({
                "issue_key": issue.get("key"),
                "from_status": item.get("fromString"),
                "to_status": item.get("toString"),
                "changed_at": changed_at_str,
                "team": team,
            })

    return transitions


def _fetch_changelogs_batched(
    issue_keys: List[str],
    team_map: Dict[str, Optional[str]],
    batch_size: int = 50,
) -> List[Dict]:
    """
    Fetch changelogs for a known list of issue keys in batches.

    Uses JQL `issue in (key1, key2, ...)` so each call covers `batch_size`
    issues at once — avoids one-call-per-issue while also bypassing the
    pagination problems that occur when expand=changelog is combined with a
    large open-ended JQL query.

    For 500 issues at batch_size=50 this makes ~10 HTTP calls.
    """
    all_transitions: List[Dict] = []

    for i in range(0, len(issue_keys), batch_size):
        batch = issue_keys[i : i + batch_size]
        keys_str = ", ".join(batch)
        jql = f"issue in ({keys_str})"
        # Only the key field is needed — changelog comes via expand
        batch_issues = fetch_all_issues(jql, fields=["key"], expand="changelog")
        for issue in batch_issues:
            team = team_map.get(issue.get("key"))
            transitions = extract_status_transitions(issue, team=team)
            all_transitions.extend(transitions)

        done = min(i + batch_size, len(issue_keys))
        print(f"    Changelogs: {done}/{len(issue_keys)} issues processed", flush=True)

    return all_transitions


def load_issues_and_transitions() -> Tuple[pd.DataFrame, List[Dict]]:
    """
    Fetch all issues then their changelogs in two separate phases.

    Phase 1 — issues (no expand): reliable pagination, returns all issues
    with correct team/field data.
    Phase 2 — changelogs in batches: uses `issue in (...)` JQL so each call
    covers 50 issues at once; avoids per-issue calls and the pagination bug
    that occurs with expand=changelog on large open-ended queries.

    Returns
    -------
    df : pd.DataFrame
        Normalized issues (same schema as load_issues_as_dataframe).
    transitions : list[dict]
        All status-change transitions extracted from changelogs.
    """
    custom_fields = get_custom_field_ids(["Team", "Data de Implantação"])
    fields = (
        ["issuetype", "status", "created", "resolutiondate", "updated"]
        + list(custom_fields.values())
    )

    # Phase 1: fetch all issues without changelog (fast, reliable)
    print("    Phase 1/2: fetching issues...", flush=True)
    issues = fetch_all_issues("project = TD", fields)

    normalized: List[Dict] = []
    for issue in issues:
        normalized.append(normalize_issue(issue, custom_fields))

    df = pd.DataFrame(normalized) if normalized else pd.DataFrame(columns=[
        "key", "issuetype", "team", "status", "created",
        "resolutiondate", "data_implantacao", "updated",
    ])
    df["created"] = pd.to_datetime(df["created"], errors="coerce", utc=True).dt.tz_convert(None)
    df["resolutiondate"] = pd.to_datetime(df["resolutiondate"], errors="coerce", utc=True).dt.tz_convert(None)
    df["data_implantacao"] = pd.to_datetime(df["data_implantacao"], errors="coerce", utc=True).dt.tz_convert(None)
    df["updated"] = pd.to_datetime(df["updated"], errors="coerce", utc=True).dt.tz_convert(None)
    df["team"] = df["team"].fillna("Unknown")
    df["year_month"] = df["created"].dt.to_period("M").astype(str)
    df["is_resolved"] = df["resolutiondate"].notna()

    print(f"    Phase 1/2: {len(df)} issues fetched", flush=True)

    # Phase 2: fetch changelogs in batches
    print("    Phase 2/2: fetching changelogs in batches of 50...", flush=True)
    issue_keys = df["key"].dropna().tolist()
    team_map: Dict[str, Optional[str]] = dict(zip(df["key"], df["team"]))
    transitions = _fetch_changelogs_batched(issue_keys, team_map)

    return df, transitions


def load_issues_as_dataframe() -> pd.DataFrame:
    """Backward-compatible wrapper — returns only the issues DataFrame."""
    df, _ = load_issues_and_transitions()
    return df
