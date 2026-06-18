"""
Sync Jira issues to issues_raw and compute metric snapshots.

Usage:
    python sync_and_snapshot.py
    python sync_and_snapshot.py --force-recalculate-period=2026-03
"""
import argparse
import math
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy.orm import Session

from db import IssueRaw, IssueTransition, MetricSnapshot, engine, init_db
from jira_client import load_issues_and_transitions
from metrics import calculate_metrics_summary

METRIC_COLUMNS = [
    ("mttr", "mttr_hours"),
    ("cfr", "cfr_percent"),
    ("lead_time", "lead_time_days"),
    ("deployment_count", "deployment_count"),
]


def _safe_float(value):
    try:
        if pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_py_datetime(ts):
    if ts is None:
        return None
    try:
        if pd.isna(ts):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(ts, "to_pydatetime"):
        return ts.to_pydatetime()
    return ts


def sync_transitions(session: Session, transitions: list[dict]) -> int:
    """
    Replace all rows in issue_transitions with the current sync's data.

    Same truncate-and-reinsert strategy as sync_issues_raw: since every
    sync fetches the FULL changelog for all issues, we always have the
    complete picture and can safely rebuild from scratch.
    """
    session.query(IssueTransition).delete()

    rows = []
    for t in transitions:
        raw_ts = t.get("changed_at")
        if not raw_ts:
            continue
        changed_at = pd.to_datetime(raw_ts, utc=True, errors="coerce")
        if pd.isna(changed_at):
            continue
        # Strip timezone so SQLite/SQLAlchemy stores a naive datetime
        changed_at_naive = changed_at.tz_convert(None).to_pydatetime()
        rows.append(IssueTransition(
            issue_key=t["issue_key"],
            from_status=t.get("from_status"),
            to_status=t.get("to_status"),
            changed_at=changed_at_naive,
            team=t.get("team"),
        ))

    session.bulk_save_objects(rows)
    return len(rows)


def print_transitions(session: Session, limit: int = 20) -> None:
    rows = (
        session.query(IssueTransition)
        .order_by(IssueTransition.changed_at.desc())
        .limit(limit)
        .all()
    )
    total = session.query(IssueTransition).count()

    width = 88
    print("\n" + "=" * width)
    print(f"  issue_transitions  ({total} rows total — showing {len(rows)} most recent)")
    print("=" * width)
    print(f"{'issue_key':<14} {'team':<18} {'from_status':<22} {'to_status':<22} changed_at")
    print("-" * width)
    for r in rows:
        ts = r.changed_at.strftime("%Y-%m-%d %H:%M") if r.changed_at else ""
        print(
            f"{(r.issue_key or ''):<14} {(r.team or ''):<18} "
            f"{(r.from_status or ''):<22} {(r.to_status or ''):<22} {ts}"
        )
    print("=" * width)


def sync_issues_raw(session: Session, df: pd.DataFrame) -> int:
    session.query(IssueRaw).delete()
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    rows = []
    for _, row in df.iterrows():
        rows.append(IssueRaw(
            key=row["key"],
            issuetype=row.get("issuetype"),
            team=row.get("team"),
            status=row.get("status"),
            created=_to_py_datetime(row.get("created")),
            resolutiondate=_to_py_datetime(row.get("resolutiondate")),
            data_implantacao=_to_py_datetime(row.get("data_implantacao")),
            updated=_to_py_datetime(row.get("updated")),
            synced_at=now,
        ))

    session.bulk_save_objects(rows)
    return len(rows)


def load_issues_from_db(session: Session) -> pd.DataFrame:
    issues = session.query(IssueRaw).all()
    if not issues:
        return pd.DataFrame(columns=[
            "key", "issuetype", "team", "status", "created",
            "resolutiondate", "data_implantacao", "year_month", "is_resolved",
        ])

    records = [{
        "key": i.key,
        "issuetype": i.issuetype,
        "team": i.team or "Unknown",
        "status": i.status,
        "created": i.created,
        "resolutiondate": i.resolutiondate,
        "data_implantacao": i.data_implantacao,
    } for i in issues]

    df = pd.DataFrame(records)
    df["created"] = pd.to_datetime(df["created"], errors="coerce")
    df["resolutiondate"] = pd.to_datetime(df["resolutiondate"], errors="coerce")
    df["data_implantacao"] = pd.to_datetime(df["data_implantacao"], errors="coerce")
    df["year_month"] = df["created"].dt.to_period("M").astype(str)
    df["is_resolved"] = df["resolutiondate"].notna()
    return df


def upsert_snapshot(
    session: Session,
    period: str,
    team: str,
    metric_name: str,
    value,
    finalized: bool,
    force_period: str | None,
) -> str:
    existing = session.query(MetricSnapshot).filter_by(
        period=period, team=team, metric_name=metric_name
    ).first()

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    if existing:
        if existing.finalized and period != force_period:
            return "skipped"
        existing.value = value
        existing.computed_at = now
        existing.finalized = finalized
        return "updated"

    session.add(MetricSnapshot(
        period=period, team=team, metric_name=metric_name,
        value=value, computed_at=now, finalized=finalized,
    ))
    return "inserted"


def process_period(session, summary_df, period, finalized, force_period, counts):
    period_df = summary_df[summary_df["year_month"] == period]
    for _, row in period_df.iterrows():
        team = row["team"]
        for metric_name, col in METRIC_COLUMNS:
            value = _safe_float(row.get(col))
            result = upsert_snapshot(session, period, team, metric_name, value, finalized, force_period)
            counts[result] = counts.get(result, 0) + 1


def print_snapshots(session: Session):
    rows = (
        session.query(MetricSnapshot)
        .order_by(MetricSnapshot.period, MetricSnapshot.team, MetricSnapshot.metric_name)
        .all()
    )

    width = 88
    print("\n" + "=" * width)
    print(f"  metric_snapshots  ({len(rows)} rows)")
    print("=" * width)
    print(f"{'period':<10} {'team':<22} {'metric':<14} {'value':>12}  {'fin':<5} {'computed_at'}")
    print("-" * width)

    for r in rows:
        value_str = f"{r.value:.4f}" if r.value is not None else "NULL"
        fin_str = "TRUE" if r.finalized else "false"
        computed = r.computed_at.strftime("%Y-%m-%d %H:%M") if r.computed_at else ""
        print(f"{r.period:<10} {(r.team or ''):<22} {r.metric_name:<14} {value_str:>12}  {fin_str:<5} {computed}")

    print("=" * width)


def main():
    parser = argparse.ArgumentParser(
        description="Sync Jira -> issues_raw and compute metric_snapshots."
    )
    parser.add_argument(
        "--force-recalculate-period",
        type=str,
        default=None,
        metavar="YYYY-MM",
        help="Force recalculation of a finalized period (e.g. --force-recalculate-period=2026-03)",
    )
    args = parser.parse_args()
    force_period = args.force_recalculate_period

    if force_period:
        print(f"[WARN] Force-recalculate mode: '{force_period}' will be overwritten even if finalized.")

    # 1. Init DB
    init_db()
    print("[OK] Database schema ready (metrics.db)")

    # 2. Fetch from Jira (issues + changelogs in a single batch)
    print("[..] Fetching issues + changelogs from Jira (expand=changelog)...")
    jira_df, transitions = load_issues_and_transitions()
    print(f"[OK] {len(jira_df)} issues fetched, {len(transitions)} status transitions extracted")

    with Session(engine) as session:
        # 3. Sync issues_raw (truncate + re-insert)
        issues_count = sync_issues_raw(session, jira_df)
        session.commit()
        print(f"[OK] issues_raw synced: {issues_count} rows written")

        # 3b. Sync issue_transitions (truncate + re-insert)
        t_count = sync_transitions(session, transitions)
        session.commit()
        print(f"[OK] issue_transitions synced: {t_count} rows written")

        # 4. Reload from DB (single source of truth for metrics)
        df = load_issues_from_db(session)
        if df.empty:
            print("[WARN] No issues in DB — nothing to calculate.")
            return

        # 5. Calculate all metrics at once
        summary = calculate_metrics_summary(df)

        current_period = datetime.now().strftime("%Y-%m")
        all_periods = sorted(summary["year_month"].dropna().unique().tolist())
        past_periods = [p for p in all_periods if p < current_period]
        current_in_data = [p for p in all_periods if p == current_period]

        counts = {"inserted": 0, "updated": 0, "skipped": 0}

        # 6. Finalize past periods (write once, never overwrite finalized)
        for period in past_periods:
            process_period(session, summary, period, finalized=True, force_period=force_period, counts=counts)

        # 7. Upsert current period — always finalized=False (month still open)
        for period in current_in_data:
            process_period(session, summary, period, finalized=False, force_period=force_period, counts=counts)

        session.commit()

        # 8. Print summary
        print()
        print("-- Snapshot update summary " + "-" * 52)
        print(f"  Current period  : {current_period}  (finalized=False, will update on every run)")
        print(f"  Past periods    : {len(past_periods)} finalized")
        if past_periods:
            shown = past_periods[-5:]
            suffix = "..." if len(past_periods) > 5 else ""
            print(f"  Last periods    : {', '.join(shown)}{suffix}")
        print(f"  Rows inserted   : {counts['inserted']}")
        print(f"  Rows updated    : {counts['updated']}")
        print(f"  Rows skipped    : {counts['skipped']}  (already finalized, use --force-recalculate-period to override)")

        # 9. Print metric_snapshots + transition sample for validation
        print_snapshots(session)
        print_transitions(session)


if __name__ == "__main__":
    main()
