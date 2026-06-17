"""
Quick inspection of the local metrics.db database.

Usage: python inspect_db.py
"""
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from db import IssueRaw, MetricSnapshot, engine


def _hr(width=80):
    print("-" * width)


def _header(title, width=80):
    print("=" * width)
    print(f"  {title}")
    print("=" * width)


def inspect_tables():
    inspector = sa_inspect(engine)
    tables = inspector.get_table_names()
    print("\nTables in metrics.db:", tables)


def inspect_issues_raw(session: Session):
    count = session.query(IssueRaw).count()
    _header(f"issues_raw  ({count} rows)  -- last 10 by synced_at")
    rows = (
        session.query(IssueRaw)
        .order_by(IssueRaw.synced_at.desc())
        .limit(10)
        .all()
    )
    if not rows:
        print("  (empty)")
        return

    fmt = "{:<14} {:<12} {:<20} {:<16} {:<13} {:<13}"
    print(fmt.format("key", "issuetype", "team", "status", "created", "synced_at"))
    _hr()
    for r in rows:
        created = r.created.strftime("%Y-%m-%d") if r.created else "NULL"
        synced = r.synced_at.strftime("%Y-%m-%d %H:%M") if r.synced_at else "NULL"
        print(fmt.format(
            r.key or "",
            (r.issuetype or "")[:12],
            (r.team or "")[:20],
            (r.status or "")[:16],
            created,
            synced,
        ))


def inspect_metric_snapshots(session: Session):
    count = session.query(MetricSnapshot).count()
    _header(f"metric_snapshots  ({count} rows)  -- last 10 by computed_at")
    rows = (
        session.query(MetricSnapshot)
        .order_by(MetricSnapshot.computed_at.desc())
        .limit(10)
        .all()
    )
    if not rows:
        print("  (empty)")
        return

    fmt = "{:<10} {:<22} {:<14} {:>12}  {:<7} {}"
    print(fmt.format("period", "team", "metric", "value", "final", "computed_at"))
    _hr()
    for r in rows:
        value_str = f"{r.value:.4f}" if r.value is not None else "NULL"
        fin_str = "TRUE" if r.finalized else "false"
        computed = r.computed_at.strftime("%Y-%m-%d %H:%M") if r.computed_at else ""
        print(fmt.format(
            r.period or "",
            (r.team or "")[:22],
            r.metric_name or "",
            value_str,
            fin_str,
            computed,
        ))


def main():
    inspect_tables()

    with Session(engine) as session:
        print()
        inspect_issues_raw(session)
        print()
        inspect_metric_snapshots(session)
        print()


if __name__ == "__main__":
    main()
