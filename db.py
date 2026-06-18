import os

from sqlalchemy import Boolean, Column, DateTime, Float, Index, Integer, String, UniqueConstraint, create_engine
from sqlalchemy.orm import declarative_base

_db_path = os.environ.get("DASHBOARD_DB_PATH", "metrics.db")
DATABASE_URL = f"sqlite:///{_db_path}"
engine = create_engine(DATABASE_URL, echo=False)
Base = declarative_base()


class IssueRaw(Base):
    __tablename__ = "issues_raw"
    key = Column(String, primary_key=True)
    issuetype = Column(String)
    team = Column(String)
    status = Column(String)
    created = Column(DateTime)
    resolutiondate = Column(DateTime)
    data_implantacao = Column(DateTime)
    updated = Column(DateTime)
    synced_at = Column(DateTime)


class MetricSnapshot(Base):
    __tablename__ = "metric_snapshots"
    id = Column(Integer, primary_key=True, autoincrement=True)
    period = Column(String, nullable=False)
    team = Column(String, nullable=False)
    metric_name = Column(String, nullable=False)
    value = Column(Float)
    computed_at = Column(DateTime)
    finalized = Column(Boolean, default=False)

    __table_args__ = (
        UniqueConstraint("period", "team", "metric_name", name="uq_period_team_metric"),
    )


class IssueTransition(Base):
    """One row per status change extracted from the Jira changelog."""
    __tablename__ = "issue_transitions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    issue_key = Column(String, nullable=False)
    from_status = Column(String)
    to_status = Column(String)
    changed_at = Column(DateTime, nullable=False)
    team = Column(String)

    __table_args__ = (
        UniqueConstraint(
            "issue_key", "from_status", "to_status", "changed_at",
            name="uq_issue_transition",
        ),
        Index("ix_transitions_issue_key", "issue_key"),
        Index("ix_transitions_team_changed_at", "team", "changed_at"),
    )


def init_db():
    Base.metadata.create_all(engine)
