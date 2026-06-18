"""
Generate a rich synthetic demo dataset and write it to metrics_demo.db.

Run from the project root:
    python scripts/generate_demo_data.py

What is generated
-----------------
- 7 months of history  (TODAY-6 months … TODAY)
- ~300+ issues across 3 fixed teams: "Time Alpha", "Time Beta", "Time Gamma"
- Types: GMUD (deploys), História (user stories), Incidente, Subtask
- História full flow: Backlog → Discovery → Design → Pronto pra Refinamento →
  Em Refinamento → Pronto pra desenvolvimento → Sprint Backlog →
  Em desenvolvimento → Pronto pra testes → Em testes → Revisão de Produto →
  Pronto pra produção → Concluído
- Subtask flow: Sprint Backlog → Em desenvolvimento → Code Review → Concluído
- 0–3 subtasks per História
- 3 diagnostic scenarios:
    a. Subtasks todas Concluídas mas História pai ainda em status aberto
    b. Subtasks presas em Code Review há >5 dias
    c. Histórias em "Revisão de Produto" / "Pronto pra produção" há >3 dias
- Lead Time 1–7 business days, MTTR 2–20 h, CFR oscillating good/bad by month
- At least one month with >30/60d Aging and a visible status bottleneck
- metric_snapshots derived by running the REAL core_metrics functions — no
  invented numbers
"""
from __future__ import annotations

import datetime
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
from sqlalchemy.orm import Session

os.environ.setdefault("DASHBOARD_DB_PATH", str(ROOT / "metrics_demo.db"))

from db import Base, IssueRaw, IssueTransition, MetricSnapshot, engine, init_db
from core_metrics import compute_aging, prepare_df
from metrics import calculate_metrics_summary
from sync_and_snapshot import upsert_snapshot

# ── Reproducibility ───────────────────────────────────────────────────────────
random.seed(42)

# ── Constants ─────────────────────────────────────────────────────────────────
TODAY = datetime.date(2026, 6, 18)
TEAMS = ["Time Alpha", "Time Beta", "Time Gamma"]

# Full flow for user stories (Histórias)
HISTORIA_FLOW = [
    "Backlog",
    "Discovery",
    "Design",
    "Pronto pra Refinamento",
    "Em Refinamento",
    "Pronto pra desenvolvimento",
    "Sprint Backlog",
    "Em desenvolvimento",
    "Pronto pra testes",
    "Em testes",
    "Revisão de Produto",
    "Pronto pra produção",
    "Concluído",
]
HISTORIA_DONE = "Concluído"
# Indices of valid open statuses (excludes terminal "Concluído" and near-terminal "Pronto pra produção")
HISTORIA_OPEN_RANGE = list(range(1, len(HISTORIA_FLOW) - 2))

# Flow for subtasks
SUBTASK_FLOW = ["Sprint Backlog", "Em desenvolvimento", "Code Review", "Concluído"]
SUBTASK_DONE = "Concluído"

# GMUD deployment flow — terminal is "Implantado com Sucesso" (or "Implantado com Falha" on failure)
GMUD_FLOW = [
    "Sprint Backlog",
    "Aguardando Implantação",
    "Em Validação",
    "Aguardando Solicitante",
    "Implantado com Sucesso",
]
GMUD_FAIL_STATUS = "Implantado com Falha"

# Incidente flow
INCIDENTE_FLOW = [
    "Sprint Backlog",
    "Em Desenvolvimento",
    "Em Validação",
    "Pronto pra Prod",
    "Concluído",
]

# Month boundaries: last 7 complete months + current month
def _month_start(offset: int) -> datetime.date:
    d = TODAY.replace(day=1)
    for _ in range(offset):
        d = (d - datetime.timedelta(days=1)).replace(day=1)
    return d

MONTHS = [_month_start(i) for i in range(6, -1, -1)]  # oldest → newest (7 months)

def _month_end(ms: datetime.date) -> datetime.date:
    next_m = (ms.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
    return next_m - datetime.timedelta(days=1)

def _rand_dt(date: datetime.date, hour_range=(8, 19)) -> datetime.datetime:
    h = random.randint(*hour_range)
    m = random.randint(0, 59)
    s = random.randint(0, 59)
    return datetime.datetime(date.year, date.month, date.day, h, m, s)

def _add_bdays(d: datetime.date, n: int) -> datetime.date:
    while n > 0:
        d += datetime.timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d

# ── Issue + transition generation helpers ────────────────────────────────────

_issue_counter = 0

def _next_key() -> str:
    global _issue_counter
    _issue_counter += 1
    return f"DEMO-{_issue_counter:04d}"


def _make_transitions(
    key: str,
    team: str,
    created: datetime.datetime,
    statuses: list[str],
    resolution_dt: datetime.datetime | None,
) -> list[dict]:
    transitions = []
    n = len(statuses)
    if n < 2:
        return transitions

    end = resolution_dt or (created + datetime.timedelta(days=2))
    total_seconds = max(1, (end - created).total_seconds())
    step = total_seconds / (n - 1)

    for i in range(n - 1):
        jitter = random.uniform(-step * 0.1, step * 0.1)
        ts = created + datetime.timedelta(seconds=step * i + jitter)
        ts = max(created, min(ts, end))
        transitions.append({
            "issue_key": key,
            "from_status": statuses[i],
            "to_status": statuses[i + 1],
            "changed_at": ts,
            "team": team,
        })
    return transitions


def _make_gmud(
    team: str,
    month_start: datetime.date,
    failure_prob: float,
) -> tuple[dict, list[dict]]:
    key = _next_key()
    created_day = random.randint(1, 20)
    created = _rand_dt(month_start.replace(day=min(created_day, _month_end(month_start).day)))
    implant_offset = random.randint(1, 5)
    implant_date = _add_bdays(created.date(), implant_offset)
    if implant_date > _month_end(month_start):
        implant_date = _month_end(month_start)
    implant_dt = _rand_dt(implant_date)
    resolution_dt = implant_dt + datetime.timedelta(hours=random.randint(1, 4))

    is_failure = random.random() < failure_prob
    final_status = GMUD_FAIL_STATUS if is_failure else GMUD_FLOW[-1]
    flow = GMUD_FLOW[:-1] + [final_status]

    issue = {
        "key": key,
        "issuetype": "GMUD",
        "team": team,
        "parent_key": None,
        "status": final_status,
        "created": created,
        "resolutiondate": resolution_dt,
        "data_implantacao": None,
        "updated": resolution_dt,
    }
    transitions = _make_transitions(
        key, team, created,
        statuses=flow,
        resolution_dt=resolution_dt,
    )
    return issue, transitions


def _make_incidente(
    team: str,
    month_start: datetime.date,
    mttr_hours: float,
) -> tuple[dict, list[dict]]:
    key = _next_key()
    day = random.randint(1, _month_end(month_start).day)
    created = _rand_dt(month_start.replace(day=day), hour_range=(0, 22))
    res_dt = created + datetime.timedelta(hours=mttr_hours + random.uniform(-1, 1))

    issue = {
        "key": key,
        "issuetype": "Incidente",
        "team": team,
        "parent_key": None,
        "status": INCIDENTE_FLOW[-1],
        "created": created,
        "resolutiondate": res_dt,
        "data_implantacao": None,
        "updated": res_dt,
    }
    transitions = _make_transitions(
        key, team, created,
        statuses=INCIDENTE_FLOW,
        resolution_dt=res_dt,
    )
    return issue, transitions


def _make_historia(
    team: str,
    month_start: datetime.date,
    lead_time_days: int,
    resolved: bool = True,
    created_override: datetime.date | None = None,
    stop_at: str | None = None,
) -> tuple[dict, list[dict]]:
    """
    stop_at: when resolved=False, force this specific status from HISTORIA_FLOW.
    If None and resolved=False, pick randomly from non-terminal positions.
    """
    key = _next_key()
    base_day = created_override or month_start.replace(
        day=random.randint(1, max(1, _month_end(month_start).day - lead_time_days - 1))
    )
    created = _rand_dt(base_day)

    if resolved:
        res_day = _add_bdays(base_day, lead_time_days)
        res_dt = _rand_dt(res_day, hour_range=(14, 20))
        status = HISTORIA_DONE
        updated = res_dt
        statuses = HISTORIA_FLOW
    else:
        res_dt = None
        if stop_at is not None and stop_at in HISTORIA_FLOW:
            idx = HISTORIA_FLOW.index(stop_at)
        else:
            idx = random.choice(HISTORIA_OPEN_RANGE)
        status = HISTORIA_FLOW[idx]
        updated = _rand_dt(base_day + datetime.timedelta(days=random.randint(0, lead_time_days)))
        statuses = HISTORIA_FLOW[:idx + 1]

    issue = {
        "key": key,
        "issuetype": "História",
        "team": team,
        "parent_key": None,
        "status": status,
        "created": created,
        "resolutiondate": res_dt,
        "data_implantacao": None,
        "updated": updated,
    }
    transitions = _make_transitions(key, team, created, statuses=statuses, resolution_dt=res_dt)
    return issue, transitions


def _make_subtask(
    parent_key: str,
    team: str,
    parent_created: datetime.datetime,
    resolved: bool = True,
    stuck_in_code_review: bool = False,
) -> tuple[dict, list[dict]]:
    """Subtask flow: Sprint Backlog → Em desenvolvimento → Code Review → Concluído."""
    key = _next_key()
    created = parent_created + datetime.timedelta(hours=random.randint(2, 24))

    if stuck_in_code_review:
        status = "Code Review"
        res_dt = None
        updated_date = TODAY - datetime.timedelta(days=random.randint(6, 12))
        updated = _rand_dt(updated_date)
        statuses = SUBTASK_FLOW[:3]  # Sprint Backlog → Em desenvolvimento → Code Review
    elif resolved:
        lt_days = random.randint(1, 5)
        res_day = _add_bdays(created.date(), lt_days)
        res_dt = _rand_dt(res_day, hour_range=(14, 20))
        status = SUBTASK_DONE
        updated = res_dt
        statuses = SUBTASK_FLOW
    else:
        idx = random.randint(1, 2)
        status = SUBTASK_FLOW[idx]
        res_dt = None
        updated = _rand_dt(created.date() + datetime.timedelta(days=random.randint(0, 3)))
        statuses = SUBTASK_FLOW[:idx + 1]

    issue = {
        "key": key,
        "issuetype": "Subtask",
        "team": team,
        "parent_key": parent_key,
        "status": status,
        "created": created,
        "resolutiondate": res_dt,
        "data_implantacao": None,
        "updated": updated,
    }
    transitions = _make_transitions(key, team, created, statuses=statuses, resolution_dt=res_dt)
    return issue, transitions


# ── Per-month issue generation plan ──────────────────────────────────────────

def _plan_month(month_idx: int, month_start: datetime.date):
    """Return list of (issue_dict, [transition_dict, ...]) for one month."""
    results: list[tuple[dict, list[dict]]] = []

    # Bad months have 25-35% GMUD failure rate; good months 10-15%.
    is_bad_month = month_idx in (1, 3, 5)
    failure_prob = 0.30 if is_bad_month else 0.12
    is_aging_bad_month = (month_idx == 2)
    is_current_month = (month_start == MONTHS[-1])
    is_recent = month_idx >= 4  # 3 most recent months get diagnostic scenarios

    for team in TEAMS:
        # ── GMUDs ──────────────────────────────────────────────────────────────
        n_gmuds = random.randint(4, 7)
        for _ in range(n_gmuds):
            results.append(_make_gmud(team, month_start, failure_prob))

        # ── Incidentes — MTTR varies; CFR is now GMUD-driven, not incident-driven
        if is_bad_month:
            n_incidents = random.randint(2, 3)
            mttr_base = random.uniform(6, 18)
        else:
            n_incidents = 1
            mttr_base = random.uniform(2, 8)

        for _ in range(n_incidents):
            results.append(_make_incidente(team, month_start, mttr_base + random.uniform(-1, 2)))

        # ── Histórias com subtasks ─────────────────────────────────────────────
        n_stories = random.randint(8, 14)
        for _ in range(n_stories):
            lt = random.randint(1, 7)
            hist, hist_tr = _make_historia(team, month_start, lt, resolved=True)
            results.append((hist, hist_tr))
            parent_key = hist["key"]
            for _ in range(random.randint(0, 3)):
                results.append(_make_subtask(parent_key, team, hist["created"], resolved=True))

        # ── Aging bad month: items stuck in Em testes ──────────────────────────
        if is_aging_bad_month:
            for _ in range(12):
                old_day = month_start + datetime.timedelta(days=random.randint(0, 3))
                issue_d, tr_d = _make_historia(
                    team, month_start,
                    lead_time_days=3,
                    resolved=False,
                    created_override=old_day,
                )
                issue_d["status"] = "Em testes"
                results.append((issue_d, tr_d))

        # ── Current month: open items ──────────────────────────────────────────
        if is_current_month:
            for _ in range(random.randint(3, 6)):
                lt = random.randint(1, 3)
                hist, hist_tr = _make_historia(team, month_start, lt, resolved=False)
                results.append((hist, hist_tr))
                parent_key = hist["key"]
                for _ in range(random.randint(0, 2)):
                    results.append(_make_subtask(parent_key, team, hist["created"], resolved=False))

        # ── Scenario a: subtasks Concluídas, pai ainda aberto (Time Alpha) ────
        if is_recent and team == "Time Alpha":
            hist, hist_tr = _make_historia(
                team, month_start, lead_time_days=10,
                resolved=False, stop_at="Revisão de Produto",
            )
            results.append((hist, hist_tr))
            parent_key = hist["key"]
            for _ in range(random.randint(2, 3)):
                results.append(_make_subtask(parent_key, team, hist["created"], resolved=True))

        # ── Scenario b: subtasks presas em Code Review >5 dias (Time Beta) ────
        if is_recent and team == "Time Beta":
            hist, hist_tr = _make_historia(
                team, month_start, lead_time_days=10,
                resolved=False, stop_at="Em desenvolvimento",
            )
            results.append((hist, hist_tr))
            parent_key = hist["key"]
            for _ in range(random.randint(1, 2)):
                results.append(_make_subtask(
                    parent_key, team, hist["created"], stuck_in_code_review=True,
                ))

        # ── Scenario c: Histórias em Revisão/Pronto há >3 dias (Time Gamma) ──
        if is_recent and team == "Time Gamma":
            stop_status = random.choice(["Revisão de Produto", "Pronto pra produção"])
            hist, hist_tr = _make_historia(
                team, month_start, lead_time_days=14,
                resolved=False, stop_at=stop_status,
            )
            hist["updated"] = _rand_dt(TODAY - datetime.timedelta(days=random.randint(4, 7)))
            results.append((hist, hist_tr))

    return results


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    Base.metadata.drop_all(engine)
    init_db()
    print(f"[OK] Schema ready — {engine.url}")

    all_issues: list[dict] = []
    all_transitions: list[dict] = []

    for idx, ms in enumerate(MONTHS):
        pairs = _plan_month(idx, ms)
        for issue, transitions in pairs:
            all_issues.append(issue)
            all_transitions.extend(transitions)

    print(f"[..] Generated {len(all_issues)} issues, {len(all_transitions)} transitions")

    # ── Write issues_raw ─────────────────────────────────────────────────────
    with Session(engine) as session:
        now = datetime.datetime.now()
        rows = []
        for r in all_issues:
            rows.append(IssueRaw(
                key=r["key"],
                issuetype=r["issuetype"],
                team=r["team"],
                parent_key=r.get("parent_key"),
                status=r["status"],
                created=r["created"],
                resolutiondate=r.get("resolutiondate"),
                data_implantacao=r.get("data_implantacao"),
                updated=r.get("updated"),
                synced_at=now,
            ))
        session.bulk_save_objects(rows)

        # ── Write issue_transitions ──────────────────────────────────────────
        t_rows = []
        seen = set()
        for t in all_transitions:
            dedup_key = (t["issue_key"], t["from_status"], t["to_status"],
                         t["changed_at"].isoformat())
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            t_rows.append(IssueTransition(
                issue_key=t["issue_key"],
                from_status=t["from_status"],
                to_status=t["to_status"],
                changed_at=t["changed_at"],
                team=t["team"],
            ))
        session.bulk_save_objects(t_rows)
        session.commit()
        print(f"[OK] issues_raw: {len(rows)} rows | issue_transitions: {len(t_rows)} rows")

    # ── Derive metric_snapshots from REAL core_metrics functions ─────────────
    df_raw = pd.read_sql("SELECT * FROM issues_raw", engine)
    df = prepare_df(df_raw)
    summary = calculate_metrics_summary(df)

    current_period = TODAY.strftime("%Y-%m")
    all_periods = sorted(summary["year_month"].dropna().unique().tolist())

    snap_counts = {"inserted": 0, "updated": 0}

    METRIC_COLUMNS = [
        ("mttr", "mttr_hours"),
        ("cfr", "cfr_percent"),
        ("lead_time", "lead_time_days"),
        ("deployment_count", "deployment_count"),
    ]

    with Session(engine) as session:
        for period in all_periods:
            finalized = period < current_period
            period_df = summary[summary["year_month"] == period]
            for _, row in period_df.iterrows():
                team = row["team"]
                for metric_name, col in METRIC_COLUMNS:
                    val = row.get(col)
                    try:
                        import math
                        val = None if (val is None or (isinstance(val, float) and math.isnan(val))) else float(val)
                    except (TypeError, ValueError):
                        val = None
                    result = upsert_snapshot(
                        session, period, team, metric_name, val,
                        finalized=finalized, force_period=None,
                    )
                    if result in snap_counts:
                        snap_counts[result] += 1

        # ── Aging snapshots per-team + "Todos" per period ─────────────────────
        aging_teams: list[str | None] = [None] + TEAMS
        for period in all_periods:
            period_str = period
            year, month = int(period_str[:4]), int(period_str[5:7])
            if year == TODAY.year and month == TODAY.month:
                ref_date = TODAY
            else:
                next_month = datetime.date(year + (month // 12), (month % 12) + 1, 1)
                ref_date = next_month - datetime.timedelta(days=1)

            finalized = period < current_period
            for team in aging_teams:
                team_key = team or "Todos"
                ref_ts = pd.Timestamp(ref_date)
                hist_df = df[df["created"] <= ref_ts].copy()
                hist_df.loc[
                    hist_df["resolutiondate"].notna() & (hist_df["resolutiondate"] <= ref_ts),
                    "is_resolved",
                ] = True
                hist_df.loc[
                    hist_df["resolutiondate"].isna() | (hist_df["resolutiondate"] > ref_ts),
                    "is_resolved",
                ] = False
                ag = compute_aging(hist_df, team=team, today=ref_date)
                pct_crit = (
                    (ag["bands"]["30–60d"] + ag["bands"]["60+d"]) / ag["total_open"]
                    if ag["total_open"] > 0 else 0.0
                )
                for metric_name, value in [
                    ("aging_avg_age",      ag["avg_age"]),
                    ("aging_pct_critical", pct_crit),
                    ("aging_total_open",   float(ag["total_open"])),
                ]:
                    result = upsert_snapshot(
                        session, period, team_key, metric_name, value,
                        finalized=finalized, force_period=None,
                    )
                    if result in snap_counts:
                        snap_counts[result] += 1

        session.commit()

    print(f"[OK] metric_snapshots: {snap_counts['inserted']} inserted, {snap_counts['updated']} updated")

    # ── Quick summary ─────────────────────────────────────────────────────────
    df_snap = pd.read_sql(
        "SELECT period, team, metric_name, value FROM metric_snapshots "
        "WHERE metric_name IN ('cfr','lead_time','deployment_count','aging_avg_age') "
        "ORDER BY period, team, metric_name",
        engine,
    )
    print("\n-- Sample metric_snapshots --")
    print(df_snap.to_string(index=False, max_rows=40))

    open_df = df[~df["is_resolved"]]
    subtask_count = len(df[df["issuetype"] == "Subtask"]) if "issuetype" in df.columns else 0
    open_count = len(open_df)
    old_count = len(df[~df["is_resolved"] & (
        (pd.Timestamp(TODAY) - df["created"]).dt.days > 30
    )])
    print(f"\n-- Total issues: {len(df)} | Subtasks: {subtask_count}")
    print(f"-- Open items: {open_count} | older than 30d: {old_count}")

    from core_metrics import diagnose_status_concentration
    bottleneck = diagnose_status_concentration(open_df)
    print(f"-- Bottleneck status (all teams): {bottleneck or 'none detected'}")

    status_dist = open_df["status"].value_counts().head(8)
    print(f"\n-- Open items by status:\n{status_dist.to_string()}")

    if "parent_key" in df.columns:
        stuck_cr = df[(df["issuetype"] == "Subtask") & (df["status"] == "Code Review") & (~df["is_resolved"])]
        print(f"\n-- Scenario b: subtasks presas em Code Review: {len(stuck_cr)}")
        scenario_c = open_df[open_df["status"].isin(["Revisão de Produto", "Pronto pra produção"])]
        print(f"-- Scenario c: Histórias em Revisão/Pronto pra produção (open): {len(scenario_c)}")


if __name__ == "__main__":
    main()
