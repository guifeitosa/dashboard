from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, UniqueConstraint, create_engine
from sqlalchemy.orm import declarative_base

DATABASE_URL = "sqlite:///metrics.db"
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


def init_db():
    Base.metadata.create_all(engine)
