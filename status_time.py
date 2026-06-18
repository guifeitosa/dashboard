"""
Functions for computing per-status durations and lead time from status
transition histories.

All datetime values must be timezone-naive. The sync job already strips
tzinfo before writing to SQLite, so values coming from the DB are ready to use.
"""

from datetime import datetime, timedelta
from typing import Optional


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
