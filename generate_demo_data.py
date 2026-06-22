"""Generate synthetic Jira data for the demo dashboard (metrics_demo.db).

Issue type distribution (stories/tasks):
  História 35%, Melhoria 20%, Tarefa 15%, Dívida Técnica 10%,
  Spike 5%, GMUD 8%, Incidente 7%

Each História gets 1-3 subtasks:
  DEV 50%, QA 30%, Bug-Dev 20%

Run:
    python generate_demo_data.py [--db metrics_demo.db] [--months 7] [--seed 42]
"""
from __future__ import annotations

import argparse
import datetime
import random
from typing import Optional

import pandas as pd
from sqlalchemy import create_engine, text

# ── Config ────────────────────────────────────────────────────────────────────

PADRAO_TYPES = ["História", "Melhoria", "Tarefa", "Dívida Técnica", "Spike"]
PADRAO_WEIGHTS = [35, 20, 15, 10, 5]

PADRAO_FLOW = [
    "Sprint Backlog", "Em Refinamento", "Em desenvolvimento",
    "Em testes", "Revisão de Produto", "Pronto pra produção", "Concluído",
]
PADRAO_OPEN_STATUSES = [
    "Sprint Backlog", "Em Refinamento", "Em desenvolvimento",
    "Em testes", "Revisão de Produto",
]
PADRAO_NEAR_DONE = ["Revisão de Produto", "Pronto pra produção"]

INCIDENTE_FLOW = [
    "Sprint Backlog", "Em Desenvolvimento", "Em Validação", "Pronto pra Prod", "Concluído",
]
INCIDENTE_OPEN = ["Sprint Backlog", "Em Desenvolvimento", "Em Validação", "Pronto pra Prod"]

GMUD_FLOW = [
    "Sprint Backlog", "Aguardando Implantação", "Em Validação",
    "Aguardando Solicitante", "Implantado com Sucesso",
]
GMUD_OPEN = ["Aguardando Implantação", "Em Validação", "Aguardando Solicitante"]
GMUD_FAILURE = "Implantado com Falha"
GMUD_SUCCESS = "Implantado com Sucesso"

SUBTASK_TYPES = ["DEV", "QA", "Bug-Dev"]
SUBTASK_WEIGHTS = [50, 30, 20]

# DEV + Bug-Dev flow includes Code Review; QA does not
DEV_FLOW = ["Sprint Backlog", "Em desenvolvimento", "Code Review", "Concluído"]
QA_FLOW = ["Sprint Backlog", "Em desenvolvimento", "Concluído"]

TEAMS = ["Time Alpha", "Time Beta", "Time Gamma"]


def _ts(dt: datetime.datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _rand_dt(start: datetime.datetime, end: datetime.datetime) -> datetime.datetime:
    delta = (end - start).total_seconds()
    return start + datetime.timedelta(seconds=random.uniform(0, delta))


def generate(
    n_months: int = 7,
    base_monthly: int = 55,
    seed: int = 42,
) -> pd.DataFrame:
    """Return a DataFrame matching the issues_raw schema."""
    random.seed(seed)
    today = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_date = (today - datetime.timedelta(days=30 * n_months)).replace(day=1)

    rows: list[dict] = []
    key_counter = 1000

    def _key() -> str:
        nonlocal key_counter
        key_counter += 1
        return f"TD-{key_counter}"

    synced_at = today.strftime("%Y-%m-%d %H:%M:%S")

    # Build monthly windows
    month_starts: list[datetime.datetime] = []
    cur = start_date
    while cur <= today:
        month_starts.append(cur)
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1)
        else:
            cur = cur.replace(month=cur.month + 1)

    for m_idx, m_start in enumerate(month_starts):
        m_end_date = (
            month_starts[m_idx + 1] - datetime.timedelta(seconds=1)
            if m_idx + 1 < len(month_starts)
            else today
        )
        is_current = m_idx == len(month_starts) - 1

        # Slight throughput variation
        noise = random.randint(-8, 8)
        total = max(25, base_monthly + noise)

        gmud_n = max(2, int(total * 0.08))
        incident_n = max(2, int(total * 0.07))
        story_n = total - gmud_n - incident_n

        # ── Histórias / Melhoria / Tarefa / Dívida Técnica / Spike ──────────
        for _ in range(story_n):
            itype = random.choices(PADRAO_TYPES, weights=PADRAO_WEIGHTS)[0]
            team = random.choice(TEAMS)
            created = _rand_dt(m_start, m_end_date)
            parent_key = _key()

            open_chance = 0.30 if is_current else 0.05
            if random.random() < open_chance:
                status = random.choice(PADRAO_OPEN_STATUSES)
                resolutiondate = None
            else:
                status = "Concluído"
                lead_days = random.gauss(6, 3)
                resolutiondate = created + datetime.timedelta(days=max(1, lead_days))
                if resolutiondate > m_end_date:
                    resolutiondate = m_end_date

            updated = resolutiondate or (created + datetime.timedelta(days=random.randint(0, 10)))

            rows.append({
                "key": parent_key,
                "issuetype": itype,
                "team": team,
                "parent_key": None,
                "status": status,
                "created": _ts(created),
                "resolutiondate": _ts(resolutiondate) if resolutiondate else None,
                "data_implantacao": None,
                "updated": _ts(updated),
                "synced_at": synced_at,
            })

            # Subtasks for História only (other types generally don't have subtasks)
            if itype == "História":
                n_subtasks = random.choices([1, 2, 3], weights=[40, 40, 20])[0]
                for _ in range(n_subtasks):
                    stype = random.choices(SUBTASK_TYPES, weights=SUBTASK_WEIGHTS)[0]
                    s_key = _key()
                    s_created = created + datetime.timedelta(hours=random.randint(1, 24))

                    # DEV/Bug-Dev can get stuck in Code Review; QA never does
                    if status == "Concluído":
                        s_status = "Concluído"
                        s_resolved = resolutiondate
                        s_updated = s_resolved
                    else:
                        if stype in ("DEV", "Bug-Dev"):
                            # 15% chance stuck in Code Review for diagnostic scenario
                            if random.random() < 0.15:
                                s_status = "Code Review"
                                s_updated = today - datetime.timedelta(days=random.randint(6, 14))
                            else:
                                s_status = random.choice(["Em desenvolvimento", "Code Review"])
                                s_updated = today - datetime.timedelta(days=random.randint(0, 4))
                        else:  # QA
                            s_status = "Em desenvolvimento"
                            s_updated = today - datetime.timedelta(days=random.randint(0, 10))
                        s_resolved = None

                    rows.append({
                        "key": s_key,
                        "issuetype": stype,
                        "team": team,
                        "parent_key": parent_key,
                        "status": s_status,
                        "created": _ts(s_created),
                        "resolutiondate": _ts(s_resolved) if s_resolved else None,
                        "data_implantacao": None,
                        "updated": _ts(s_updated),
                        "synced_at": synced_at,
                    })

        # ── GMUDs ────────────────────────────────────────────────────────────
        for _ in range(gmud_n):
            team = random.choice(TEAMS)
            created = _rand_dt(m_start, m_end_date)
            open_chance = 0.20 if is_current else 0.03
            if random.random() < open_chance:
                status = random.choice(GMUD_OPEN)
                resolutiondate = None
                data_implantacao = None
            else:
                fail_chance = 0.12
                status = GMUD_FAILURE if random.random() < fail_chance else GMUD_SUCCESS
                lead_days = random.gauss(5, 2)
                resolutiondate = created + datetime.timedelta(days=max(1, lead_days))
                if resolutiondate > m_end_date:
                    resolutiondate = m_end_date
                data_implantacao = resolutiondate + datetime.timedelta(days=random.randint(0, 3))

            updated = resolutiondate or (created + datetime.timedelta(days=1))
            rows.append({
                "key": _key(),
                "issuetype": "GMUD",
                "team": team,
                "parent_key": None,
                "status": status,
                "created": _ts(created),
                "resolutiondate": _ts(resolutiondate) if resolutiondate else None,
                "data_implantacao": _ts(data_implantacao) if data_implantacao else None,
                "updated": _ts(updated),
                "synced_at": synced_at,
            })

        # ── Incidentes ────────────────────────────────────────────────────────
        for _ in range(incident_n):
            team = random.choice(TEAMS)
            created = _rand_dt(m_start, m_end_date)
            open_chance = 0.15 if is_current else 0.04
            if random.random() < open_chance:
                status = random.choice(INCIDENTE_OPEN)
                resolutiondate = None
            else:
                status = "Concluído"
                mttr_hours = max(1.0, random.gauss(18, 10))
                resolutiondate = created + datetime.timedelta(hours=mttr_hours)
                if resolutiondate > m_end_date:
                    resolutiondate = m_end_date

            updated = resolutiondate or (created + datetime.timedelta(hours=random.randint(1, 6)))
            rows.append({
                "key": _key(),
                "issuetype": "Incidente",
                "team": team,
                "parent_key": None,
                "status": status,
                "created": _ts(created),
                "resolutiondate": _ts(resolutiondate) if resolutiondate else None,
                "data_implantacao": None,
                "updated": _ts(updated),
                "synced_at": synced_at,
            })

    df = pd.DataFrame(rows)
    for col in ("created", "resolutiondate", "data_implantacao", "updated"):
        df[col] = pd.to_datetime(df[col], errors="coerce")
    df["is_resolved"] = df["resolutiondate"].notna()
    df["year_month"] = df["created"].dt.to_period("M").astype(str)
    return df


def generate_transitions(df_issues: pd.DataFrame, seed: int = 43) -> pd.DataFrame:
    """Generate realistic status transitions for resolved padrao issues.

    Covers:
    - 85% of padrao issues go through Sprint Backlog (15% skip → fallback_start=created)
    - 80% of padrao issues go through Revisão de Produto (20% skip → fallback_end=Concluído)
    - DEV/Bug-Dev: Sprint Backlog → Em desenvolvimento → Code Review → Concluído
    - QA: Sprint Backlog → Em desenvolvimento → Concluído
    """
    rng = random.Random(seed)
    rows: list[dict] = []

    for _, issue in df_issues.iterrows():
        itype = issue.get("issuetype", "")
        key = issue.get("key", "")
        created = pd.Timestamp(issue["created"]).to_pydatetime()
        resdate_raw = issue.get("resolutiondate")

        try:
            if resdate_raw is None or pd.isna(resdate_raw):
                continue
        except (TypeError, ValueError):
            continue

        resdate = pd.Timestamp(resdate_raw).to_pydatetime()
        total_secs = (resdate - created).total_seconds()
        if total_secs <= 0:
            continue

        def _tr(frm, to, ts):
            rows.append({"issue_key": key, "from_status": frm, "to_status": to, "changed_at": ts})

        if itype in PADRAO_TYPES:
            # Sprint Backlog (85% chance; 15% skip = fallback to created)
            if rng.random() < 0.85:
                sb_ts = created + datetime.timedelta(seconds=rng.uniform(0, 0.08 * total_secs))
                _tr("Backlog", "Sprint Backlog", sb_ts)
                prev = "Sprint Backlog"
            else:
                prev = "Backlog"  # will start directly in dev

            # Em desenvolvimento
            dev_frac = rng.uniform(0.10, 0.35)
            dev_ts = created + datetime.timedelta(seconds=dev_frac * total_secs)
            _tr(prev, "Em desenvolvimento", dev_ts)

            # Revisão de Produto (80% chance; 20% skip → cycle time uses Concluído)
            if rng.random() < 0.80:
                rp_frac = rng.uniform(0.70, 0.90)
                rp_ts = created + datetime.timedelta(seconds=rp_frac * total_secs)
                _tr("Em testes", "Revisão de Produto", rp_ts)
                last = "Revisão de Produto"
            else:
                last = "Em desenvolvimento"

            _tr(last, "Concluído", resdate)

        elif itype in ("DEV", "Bug-Dev"):
            dev_ts = created + datetime.timedelta(seconds=rng.uniform(0.05, 0.20) * total_secs)
            _tr("Sprint Backlog", "Em desenvolvimento", dev_ts)
            cr_ts = created + datetime.timedelta(seconds=rng.uniform(0.65, 0.85) * total_secs)
            _tr("Em desenvolvimento", "Code Review", cr_ts)
            _tr("Code Review", "Concluído", resdate)

        elif itype == "QA":
            dev_ts = created + datetime.timedelta(seconds=rng.uniform(0.10, 0.30) * total_secs)
            _tr("Sprint Backlog", "Em desenvolvimento", dev_ts)
            _tr("Em desenvolvimento", "Concluído", resdate)

    if not rows:
        return pd.DataFrame(columns=["issue_key", "from_status", "to_status", "changed_at"])
    df_tr = pd.DataFrame(rows)
    df_tr["changed_at"] = pd.to_datetime(df_tr["changed_at"])
    return df_tr


def _populate_metric_snapshots(df: pd.DataFrame, df_transitions: pd.DataFrame, engine) -> None:
    """Derive metric_snapshots from real core_metrics functions and write to DB."""
    import datetime as _dt
    from metrics import calculate_metrics_summary
    _METRIC_COLUMNS = [
        ("mttr", "mttr_hours"),
        ("cfr", "cfr_percent"),
        ("lead_time", "lead_time_days"),
        ("deployment_count", "deployment_count"),
    ]

    summary = calculate_metrics_summary(df)
    if summary.empty:
        return

    rows = []
    now = _dt.datetime.utcnow()
    for _, row in summary.iterrows():
        period = row["year_month"]
        team = row["team"]
        for metric_name, col in _METRIC_COLUMNS:
            val = row.get(col)
            if val is not None and not (isinstance(val, float) and pd.isna(val)):
                rows.append({
                    "period": period,
                    "team": team,
                    "metric_name": metric_name,
                    "value": float(val),
                    "computed_at": now,
                    "finalized": True,
                })

    if rows:
        from sqlalchemy import insert as _insert, Table as _Table, MetaData as _Meta
        meta = _Meta()
        meta.reflect(bind=engine)
        snap_tbl = meta.tables["metric_snapshots"]
        with engine.begin() as conn:
            conn.execute(snap_tbl.insert(), rows)


def save_to_db(df: pd.DataFrame, db_path: str = "metrics_demo.db") -> None:
    from db import Base
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS issues_raw"))
        conn.execute(text("DROP TABLE IF EXISTS issue_transitions"))
        conn.execute(text("DROP TABLE IF EXISTS metric_snapshots"))
    Base.metadata.create_all(engine)  # ensures metric_snapshots schema is created

    df_clean = df.drop(columns=["is_resolved", "year_month"], errors="ignore")
    df_clean.to_sql("issues_raw", engine, if_exists="replace", index=False)

    df_transitions = generate_transitions(df)
    df_transitions.to_sql("issue_transitions", engine, if_exists="replace", index=False)

    _populate_metric_snapshots(df, df_transitions, engine)

    print(f"[OK] {len(df)} issues written to {db_path}")
    print(f"[OK] {len(df_transitions)} transitions written to {db_path}")
    _print_summary(df)


def _print_summary(df: pd.DataFrame) -> None:
    print("\nType distribution:")
    for itype, cnt in df["issuetype"].value_counts().items():
        print(f"  {itype:20s} {cnt:4d}  ({cnt/len(df)*100:.1f}%)")

    subtask_types = {"DEV", "QA", "Bug-Dev"}
    sub = df[df["issuetype"].isin(subtask_types)]
    print(f"\nSubtask CR-scenario (DEV/Bug-Dev stuck in Code Review > 5d):")
    stuck = sub[
        sub["issuetype"].isin({"DEV", "Bug-Dev"})
        & (sub["status"] == "Code Review")
        & sub["resolutiondate"].isna()
    ]
    print(f"  {len(stuck)} items — Rule B should fire for these")

    qa_cr = sub[sub["issuetype"].eq("QA") & (sub["status"] == "Code Review")]
    print(f"  QA in Code Review: {len(qa_cr)} — Rule B must NOT fire for these")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate demo Jira data")
    parser.add_argument("--db", default="metrics_demo.db")
    parser.add_argument("--months", type=int, default=7)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    df = generate(n_months=args.months, seed=args.seed)
    save_to_db(df, db_path=args.db)
