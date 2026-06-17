"""
Tests for the snapshot layer (db.py + sync_and_snapshot.py).

All tests run against an in-memory SQLite database — the real metrics.db
is never touched.
"""
from datetime import datetime

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from db import Base, IssueRaw, MetricSnapshot
from sync_and_snapshot import load_issues_from_db, sync_issues_raw, upsert_snapshot


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def session():
    """Fresh in-memory SQLite database for each test."""
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(test_engine)
    with Session(test_engine) as s:
        yield s


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _issue_df(overrides: list[dict]) -> pd.DataFrame:
    """Build a minimal DataFrame compatible with sync_issues_raw."""
    defaults = {
        "issuetype": "Story",
        "team": "Alpha",
        "status": "Done",
        "created": pd.Timestamp("2026-06-01"),
        "resolutiondate": pd.Timestamp("2026-06-05"),
        "data_implantacao": None,
        "updated": pd.Timestamp("2026-06-05"),
    }
    return pd.DataFrame([{**defaults, **row} for row in overrides])


def _add_snapshot(session, period, team, metric, value, finalized):
    session.add(MetricSnapshot(
        period=period, team=team, metric_name=metric,
        value=value, finalized=finalized, computed_at=datetime.now(),
    ))
    session.commit()


# ---------------------------------------------------------------------------
# a. A finalized row is NEVER overwritten by a normal sync call
# ---------------------------------------------------------------------------

def test_finalized_snapshot_is_immutable(session):
    """Once finalized=True, normal upsert must skip — never change value."""
    _add_snapshot(session, "2026-05", "Alpha", "mttr", 10.0, finalized=True)

    result = upsert_snapshot(
        session, "2026-05", "Alpha", "mttr",
        value=99.0, finalized=True, force_period=None,
    )
    session.commit()

    row = session.query(MetricSnapshot).filter_by(
        period="2026-05", team="Alpha", metric_name="mttr"
    ).one()
    assert result == "skipped"
    assert row.value == 10.0, "Finalized value must be preserved"
    assert row.finalized is True


# ---------------------------------------------------------------------------
# b. The current (unfinalized) period is updated on every run
# ---------------------------------------------------------------------------

def test_current_period_updated_on_each_run(session):
    """An unfinalized row can be overwritten freely — it's still in progress."""
    upsert_snapshot(session, "2026-06", "Alpha", "mttr", 5.0, finalized=False, force_period=None)
    session.commit()

    result = upsert_snapshot(session, "2026-06", "Alpha", "mttr", 8.0, finalized=False, force_period=None)
    session.commit()

    row = session.query(MetricSnapshot).filter_by(
        period="2026-06", team="Alpha", metric_name="mttr"
    ).one()
    assert result == "updated"
    assert row.value == 8.0
    assert row.finalized is False, "Current period must remain unfinalized"


# ---------------------------------------------------------------------------
# c. When the month turns, an unfinalized past period is finalized once
# ---------------------------------------------------------------------------

def test_past_unfinalized_period_gets_finalized(session):
    """Simulate: 2026-05 was current (finalized=False). A June run finalizes it."""
    upsert_snapshot(session, "2026-05", "Alpha", "mttr", 5.0, finalized=False, force_period=None)
    session.commit()

    # June run treats 2026-05 as a past period → finalized=True
    result = upsert_snapshot(session, "2026-05", "Alpha", "mttr", 5.0, finalized=True, force_period=None)
    session.commit()

    row = session.query(MetricSnapshot).filter_by(
        period="2026-05", team="Alpha", metric_name="mttr"
    ).one()
    assert result == "updated"
    assert row.finalized is True


def test_finalized_past_period_stays_locked_on_subsequent_runs(session):
    """After finalization, further runs must not touch that period (complement of c)."""
    _add_snapshot(session, "2026-05", "Alpha", "mttr", 5.0, finalized=True)

    result = upsert_snapshot(session, "2026-05", "Alpha", "mttr", 99.0, finalized=True, force_period=None)
    session.commit()

    assert result == "skipped"
    assert session.query(MetricSnapshot).filter_by(period="2026-05").one().value == 5.0


# ---------------------------------------------------------------------------
# d. --force-recalculate-period overwrites finalized=True for that period only
# ---------------------------------------------------------------------------

def test_force_overwrites_named_finalized_period(session):
    """Explicit force flag must allow overwriting an otherwise immutable row."""
    _add_snapshot(session, "2026-03", "Alpha", "mttr", 10.0, finalized=True)

    result = upsert_snapshot(
        session, "2026-03", "Alpha", "mttr",
        value=99.0, finalized=True, force_period="2026-03",
    )
    session.commit()

    assert result == "updated"
    assert session.query(MetricSnapshot).filter_by(period="2026-03").one().value == 99.0


def test_force_does_not_affect_other_finalized_periods(session):
    """force_period='2026-03' must leave 2026-04 untouched."""
    _add_snapshot(session, "2026-03", "Alpha", "mttr", 10.0, finalized=True)
    _add_snapshot(session, "2026-04", "Alpha", "mttr", 20.0, finalized=True)

    # Force only 2026-03 — attempt to update 2026-04 in the same run
    result_04 = upsert_snapshot(
        session, "2026-04", "Alpha", "mttr",
        value=99.0, finalized=True, force_period="2026-03",
    )
    session.commit()

    assert result_04 == "skipped"
    assert session.query(MetricSnapshot).filter_by(period="2026-04").one().value == 20.0


# ---------------------------------------------------------------------------
# e. Syncing the same DataFrame twice never duplicates rows in issues_raw
# ---------------------------------------------------------------------------

def test_issues_raw_sync_is_idempotent(session):
    """sync_issues_raw truncates before inserting, so running it twice is safe."""
    df = _issue_df([{"key": "TD-1"}, {"key": "TD-2"}])

    sync_issues_raw(session, df)
    session.commit()
    count_first = session.query(IssueRaw).count()

    sync_issues_raw(session, df)
    session.commit()
    count_second = session.query(IssueRaw).count()

    assert count_first == 2
    assert count_second == 2, "Second sync must not duplicate rows"


def test_issues_raw_contains_correct_keys_after_sync(session):
    """Sanity check: the keys written to issues_raw match the source DataFrame."""
    df = _issue_df([{"key": "TD-10"}, {"key": "TD-20"}, {"key": "TD-30"}])

    sync_issues_raw(session, df)
    session.commit()

    stored_keys = {r.key for r in session.query(IssueRaw).all()}
    assert stored_keys == {"TD-10", "TD-20", "TD-30"}


def test_load_issues_from_db_derives_year_month_and_is_resolved(session):
    """load_issues_from_db must reconstruct year_month and is_resolved correctly."""
    df = _issue_df([
        {"key": "TD-1", "created": pd.Timestamp("2026-03-15"), "resolutiondate": pd.Timestamp("2026-03-20")},
        {"key": "TD-2", "created": pd.Timestamp("2026-04-01"), "resolutiondate": None},
    ])
    sync_issues_raw(session, df)
    session.commit()

    result = load_issues_from_db(session)

    td1 = result[result["key"] == "TD-1"].iloc[0]
    assert td1["year_month"] == "2026-03"
    assert bool(td1["is_resolved"]) is True

    td2 = result[result["key"] == "TD-2"].iloc[0]
    assert td2["year_month"] == "2026-04"
    assert bool(td2["is_resolved"]) is False
