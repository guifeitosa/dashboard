"""
Functions for computing per-status durations and lead time from status
transition histories.

All datetime values must be timezone-naive. The sync job already strips
tzinfo before writing to SQLite, so values coming from the DB are ready to use.
"""

from datetime import datetime, timedelta
from typing import Optional

import pandas as pd


def time_in_status(
    issue_key: str,
    created: datetime,
    transitions: list[dict],
    now: datetime,
    initial_status: Optional[str] = None,
) -> dict[str, timedelta]:
    """
    Return how long an issue spent in each status.

    Timeline reconstruction:
        [created → transitions[0].changed_at]  status = transitions[0].from_status
        [transitions[i-1] → transitions[i]]    status = transitions[i-1].to_status
        [transitions[-1] → now]                status = transitions[-1].to_status

    If an issue revisits a status (rework), durations accumulate.

    Parameters
    ----------
    issue_key       : used in error messages only, not in the calculation
    created         : when the issue was created (timezone-naive)
    transitions     : list of dicts with keys from_status, to_status, changed_at (datetime)
    now             : end of the observation window (timezone-naive)
    initial_status  : status label to use when there are no transitions and the
                      initial status is unknown; defaults to "Unknown"

    Returns
    -------
    dict mapping status name → cumulative timedelta spent in that status.
    Negative durations (data quality issues) are clamped to zero.
    """
    sorted_tr = sorted(transitions, key=lambda t: t["changed_at"])

    result: dict[str, timedelta] = {}

    def _add(status: Optional[str], duration: timedelta) -> None:
        label = status or "Unknown"
        if duration.total_seconds() < 0:
            duration = timedelta(0)
        result[label] = result.get(label, timedelta()) + duration

    if not sorted_tr:
        _add(initial_status or "Unknown", now - created)
        return result

    # created → first transition
    _add(sorted_tr[0].get("from_status") or initial_status, sorted_tr[0]["changed_at"] - created)

    # consecutive transitions
    for i in range(1, len(sorted_tr)):
        _add(sorted_tr[i - 1].get("to_status"), sorted_tr[i]["changed_at"] - sorted_tr[i - 1]["changed_at"])

    # last transition → now
    _add(sorted_tr[-1].get("to_status"), now - sorted_tr[-1]["changed_at"])

    return result


def average_time_in_status(
    issues: list[dict],
    now: datetime,
    team: Optional[str] = None,
    issuetype: Optional[str] = None,
) -> dict[str, timedelta]:
    """
    Compute the average time spent in each status across a collection of issues.

    Each issue dict must have:
        issue_key      : str
        created        : datetime (timezone-naive)
        resolutiondate : datetime | None — if set, used as the observation end
                         instead of `now`, so resolved issues don't accumulate
                         time in their final status indefinitely
        team           : str | None
        issuetype      : str | None
        transitions    : list[dict]  (same format as time_in_status)

    Averages are per-status over issues that actually visited that status
    (issues that never reached a status are excluded from its average).

    Returns an empty dict when no issues match the filters.
    """
    filtered = [
        i for i in issues
        if (team is None or i.get("team") == team)
        and (issuetype is None or i.get("issuetype") == issuetype)
    ]

    if not filtered:
        return {}

    totals: dict[str, timedelta] = {}
    counts: dict[str, int] = {}

    for issue in filtered:
        end = issue.get("resolutiondate") or now
        durations = time_in_status(
            issue["issue_key"],
            issue["created"],
            issue.get("transitions", []),
            end,
            initial_status=issue.get("status"),
        )
        for status, duration in durations.items():
            totals[status] = totals.get(status, timedelta()) + duration
            counts[status] = counts.get(status, 0) + 1

    return {status: totals[status] / counts[status] for status in totals}


def lead_time_real(
    issue_key: str,
    transitions: list[dict],
    start_status: str = "Fazendo",
    end_status: str = "Feito",
) -> Optional[timedelta]:
    """
    Calculate lead time from when an issue first enters `start_status` to
    when it first enters `end_status` (after start).

    Parametrised to work with any workflow — not hard-coded to "Fazendo"/"Feito".

    Returns None when:
    - the issue never transitioned into `start_status`, OR
    - the issue reached `start_status` but never subsequently reached `end_status`

    Callers should exclude None results from averages rather than substituting
    a default value, because the issue genuinely has no measurable lead time
    for the requested interval.
    """
    sorted_tr = sorted(transitions, key=lambda t: t["changed_at"])

    start_time: Optional[datetime] = None

    for tr in sorted_tr:
        if start_time is None and tr.get("to_status") == start_status:
            start_time = tr["changed_at"]
            continue
        if start_time is not None and tr.get("to_status") == end_status:
            return tr["changed_at"] - start_time

    return None


# ── Lead Time & Cycle Time via transitions ────────────────────────────────────

def _first_transition_to(transitions: list[dict], status: str) -> Optional[datetime]:
    """Return the timestamp of the FIRST transition whose to_status matches."""
    for tr in sorted(transitions, key=lambda t: t["changed_at"]):
        if tr.get("to_status") == status:
            return tr["changed_at"]
    return None


def _first_transition_to_after(
    transitions: list[dict], status: str, after: datetime
) -> Optional[datetime]:
    """Return the first transition to `status` that occurs strictly after `after`."""
    for tr in sorted(transitions, key=lambda t: t["changed_at"]):
        if tr["changed_at"] > after and tr.get("to_status") == status:
            return tr["changed_at"]
    return None


def _build_trans_lookup(df_transitions: Optional[pd.DataFrame]) -> dict[str, list[dict]]:
    """Convert df_transitions DataFrame into a dict keyed by issue_key."""
    lookup: dict[str, list[dict]] = {}
    if df_transitions is None or df_transitions.empty:
        return lookup
    for _, row in df_transitions.iterrows():
        key = str(row.get("issue_key", ""))
        if not key:
            continue
        raw_ts = row.get("changed_at")
        try:
            if pd.isna(raw_ts):
                continue
        except (TypeError, ValueError):
            pass
        try:
            if isinstance(raw_ts, datetime):
                changed_at = raw_ts.replace(tzinfo=None) if raw_ts.tzinfo else raw_ts
            else:
                changed_at = pd.Timestamp(raw_ts).to_pydatetime().replace(tzinfo=None)
        except Exception:
            continue
        lookup.setdefault(key, []).append({
            "from_status": row.get("from_status"),
            "to_status": row.get("to_status"),
            "changed_at": changed_at,
        })
    return lookup


def calculate_lead_and_cycle_time(
    df_issues: pd.DataFrame,
    df_transitions: Optional[pd.DataFrame],
    issuetype: Optional[str] = None,
    team: Optional[str] = None,
) -> pd.DataFrame:
    """Compute lead_time_days and cycle_time_days per resolved issue via transitions.

    Columns returned: issue_key | issuetype | team | lead_time_days | cycle_time_days | res_month

    Groups with ``lead_time: null`` in config (GMUD, Incidente, subtasks) are excluded.
    When df_transitions is None/empty, falls back to created→resolutiondate for lead time
    and leaves cycle_time_days as NaN.
    """
    from config import get_config as _get_config  # lazy — avoids circular import at load

    _COLS = ["issue_key", "issuetype", "team", "lead_time_days", "cycle_time_days", "res_month"]
    _EMPTY = pd.DataFrame(columns=_COLS)

    if df_issues is None or df_issues.empty:
        return _EMPTY

    cfg = _get_config()
    use_transitions = df_transitions is not None and not df_transitions.empty
    trans_lookup = _build_trans_lookup(df_transitions)

    rows: list[dict] = []

    for _, row in df_issues.iterrows():
        itype = str(row.get("issuetype", ""))
        if issuetype is not None and itype != issuetype:
            continue

        row_team = row.get("team")
        if team is not None and row_team != team:
            continue

        lt_cfg = cfg.lead_time_config(itype)
        if lt_cfg is None:
            continue  # GMUD, Incidente, subtasks — not measured

        ct_cfg = cfg.cycle_time_config(itype)

        # Must be resolved
        resdate_raw = row.get("resolutiondate")
        try:
            if pd.isna(resdate_raw):
                continue
        except (TypeError, ValueError):
            if resdate_raw is None:
                continue

        try:
            created_dt: datetime = pd.Timestamp(row.get("created")).to_pydatetime().replace(tzinfo=None)
            resdate_dt: datetime = pd.Timestamp(resdate_raw).to_pydatetime().replace(tzinfo=None)
        except Exception:
            continue

        key = str(row.get("key", row.get("issue_key", "")))
        transitions = trans_lookup.get(key, [])

        # ── Lead Time ─────────────────────────────────────────────────────────
        lt_start = _first_transition_to(transitions, lt_cfg["start_status"])
        if lt_start is None:
            if lt_cfg.get("fallback_start") == "created":
                lt_start = created_dt
            else:
                continue

        lt_end = _first_transition_to_after(transitions, lt_cfg["end_status"], lt_start)
        if lt_end is None:
            if not use_transitions:
                lt_end = resdate_dt  # no transitions at all — use resolutiondate
            else:
                continue  # transitions exist but issue never reached end_status

        if lt_end < lt_start:
            continue

        lead_days = float(max(1, len(pd.bdate_range(start=lt_start.date(), end=lt_end.date()))))

        # ── Cycle Time ────────────────────────────────────────────────────────
        cycle_days: Optional[float] = None
        if ct_cfg is not None:
            ct_start = _first_transition_to(transitions, ct_cfg["start_status"])
            if ct_start is not None:
                ct_end = _first_transition_to_after(transitions, ct_cfg["end_status"], ct_start)
                if ct_end is None and ct_cfg.get("fallback_end"):
                    ct_end = _first_transition_to_after(transitions, ct_cfg["fallback_end"], ct_start)
                if ct_end is None and not use_transitions:
                    ct_end = resdate_dt
                if ct_end is not None and ct_end >= ct_start:
                    cycle_days = float(max(1, len(pd.bdate_range(
                        start=ct_start.date(), end=ct_end.date()
                    ))))

        res_month = resdate_dt.strftime("%Y-%m")
        rows.append({
            "issue_key": key,
            "issuetype": itype,
            "team": row_team,
            "lead_time_days": lead_days,
            "cycle_time_days": cycle_days if cycle_days is not None else float("nan"),
            "res_month": res_month,
        })

    return pd.DataFrame(rows, columns=_COLS) if rows else _EMPTY


# ── WIP history helpers ───────────────────────────────────────────────────────

def build_issue_records(
    df_issues: pd.DataFrame,
    df_transitions: pd.DataFrame,
) -> list[dict]:
    """Convert DataFrames to list-of-dicts format for average_time_in_status.

    This is the public version of insights._build_flow_records, placed here to
    avoid circular imports when core_metrics calls it for WIP limit computation.
    """
    trans_by_key: dict[str, list[dict]] = {}
    if df_transitions is not None and not df_transitions.empty:
      for _, row in df_transitions.iterrows():
        key = row.get("issue_key")
        if key is None:
            continue
        raw_ts = row["changed_at"]
        try:
            if pd.isna(raw_ts):
                continue
        except (TypeError, ValueError):
            pass
        try:
            if isinstance(raw_ts, datetime):
                changed_at = raw_ts.replace(tzinfo=None) if raw_ts.tzinfo else raw_ts
            else:
                changed_at = pd.Timestamp(raw_ts).to_pydatetime().replace(tzinfo=None)
        except Exception:
            continue
        entry = {
            "from_status": row.get("from_status"),
            "to_status": row.get("to_status"),
            "changed_at": changed_at,
        }
        trans_by_key.setdefault(str(key), []).append(entry)

    records = []
    for _, row in df_issues.iterrows():
        key = str(row.get("key", row.get("issue_key", "")))
        created = row.get("created")
        if created is None or (isinstance(created, float) and pd.isna(created)):
            continue
        if hasattr(created, "tzinfo") and created.tzinfo is not None:
            created = created.replace(tzinfo=None)
        if not isinstance(created, datetime):
            created = pd.Timestamp(created).to_pydatetime().replace(tzinfo=None)

        resdate_raw = row.get("resolutiondate")
        resdate = None
        if resdate_raw is not None:
            try:
                if pd.isna(resdate_raw):
                    resdate = None
                elif isinstance(resdate_raw, datetime):
                    resdate = resdate_raw.replace(tzinfo=None) if resdate_raw.tzinfo else resdate_raw
                else:
                    resdate = pd.Timestamp(resdate_raw).to_pydatetime().replace(tzinfo=None)
            except Exception:
                resdate = None

        records.append({
            "issue_key": key,
            "created": created,
            "resolutiondate": resdate,
            "team": row.get("team"),
            "issuetype": row.get("issuetype"),
            "status": row.get("status"),
            "transitions": trans_by_key.get(key, []),
        })
    return records


def _status_at(
    transitions: list[dict],
    snap_dt: datetime,
    *,
    current_status: Optional[str],
) -> Optional[str]:
    """Return the status of an issue at snap_dt based on its transition history.

    Transitions do not need to be pre-sorted.

    - If there are transitions at or before snap_dt: return the to_status of the
      latest one (the most recent known state).
    - If all transitions are in the future: return the from_status of the earliest
      transition (the state before anything changed).
    - If there are no transitions at all: return current_status.
    """
    if not transitions:
        return current_status
    sorted_tr = sorted(transitions, key=lambda t: t["changed_at"])
    last_past: Optional[dict] = None
    for tr in sorted_tr:
        if tr["changed_at"] <= snap_dt:
            last_past = tr
        else:
            break
    if last_past is not None:
        return last_past.get("to_status") or current_status
    # All transitions are future → state before first transition
    return sorted_tr[0].get("from_status") or current_status


def reconstruct_wip_history(
    df_issues: pd.DataFrame,
    df_transitions: pd.DataFrame,
    freq: str = "W",
    team: Optional[str] = None,
    today: Optional[datetime] = None,
) -> pd.DataFrame:
    """Reconstruct WIP count by status and team across periodic snapshots.

    Algorithm (per snapshot date):
      - Issue is "in flight" if created ≤ snap AND (resolutiondate IS NULL OR > snap).
      - Status at snap is determined by _status_at using transition history.
      - Terminal statuses are excluded from the WIP count.

    Returns DataFrame with columns: date | status | count | team.
    """
    from core_metrics import TERMINAL_STATUSES  # lazy: avoids circular import at load time

    _EMPTY = pd.DataFrame(columns=["date", "status", "count", "team"])

    if df_issues is None or df_issues.empty:
        return _EMPTY

    records = build_issue_records(df_issues, df_transitions)
    if not records:
        return _EMPTY

    if today is None:
        today_dt: datetime = datetime.now()
    elif isinstance(today, datetime):
        today_dt = today
    else:
        today_dt = pd.Timestamp(today).to_pydatetime()

    if team is not None:
        records = [r for r in records if r.get("team") == team]
    if not records:
        return _EMPTY

    min_created = min(r["created"] for r in records)
    date_range = pd.date_range(start=min_created, end=today_dt, freq=freq)
    if len(date_range) == 0:
        return _EMPTY

    rows: list[dict] = []
    for snap in date_range:
        snap_dt = snap.to_pydatetime().replace(tzinfo=None)

        for rec in records:
            if rec["created"] > snap_dt:
                continue
            resdate = rec.get("resolutiondate")
            if resdate is not None and resdate <= snap_dt:
                continue

            status = _status_at(
                rec.get("transitions", []),
                snap_dt,
                current_status=rec.get("status"),
            )
            if status is None:
                continue
            if status.strip().lower() in TERMINAL_STATUSES:
                continue

            rows.append({
                "date": snap,
                "status": status,
                "team": rec.get("team") or "Sem Time",
            })

    if not rows:
        return _EMPTY

    result = (
        pd.DataFrame(rows)
        .groupby(["date", "status", "team"], as_index=False)
        .size()
        .rename(columns={"size": "count"})
    )
    return result[["date", "status", "count", "team"]]
