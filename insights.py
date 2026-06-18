"""Insight Engine — structured diagnostic events across all metric domains."""
from __future__ import annotations

import calendar
import datetime
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

import pandas as pd

SEVERITY_ORDER: dict[str, int] = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
_FLOW_THRESHOLD = 0.40


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class InsightEvent:
    id: str
    severity: str        # "critical" / "high" / "medium" / "low" / "info"
    category: str        # "throughput" / "aging" / "lead_time" / "mttr" / "cfr" / "deployment" / "flow"
    layer: str           # "insight" / "diagnostic" / "recommendation"
    title: str
    description: str
    evidence: dict = field(default_factory=dict)
    related_ids: list[str] = field(default_factory=list)
    team: str = "Todos"
    period: str = ""


# ── Module-level helpers ──────────────────────────────────────────────────────

def _sf(v) -> float | None:
    """Safe float conversion; returns None if value is None or NaN."""
    if v is None:
        return None
    try:
        f = float(v)
        import math
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _prev_ym(period: str) -> str:
    """Return YYYY-MM for the month immediately before `period`."""
    try:
        yr, mo = int(period[:4]), int(period[5:7])
    except (ValueError, IndexError):
        return ""
    if mo == 1:
        return f"{yr - 1:04d}-12"
    return f"{yr:04d}-{mo - 1:02d}"


def _find_snapshot(snapshots: list[dict], team: str, period: str) -> dict | None:
    """Find a snapshot dict for the given team and period."""
    team_key = team if team else "Todos"
    for s in snapshots:
        if s.get("period") == period and s.get("team") == team_key:
            return s
    return None


def _find_prev_aging(snapshots: list[dict], team: str, period: str) -> dict | None:
    """Return prev month's aging data from snapshots, normalizing keys for build_aging_diagnostics.

    Normalizes: aging_avg_age → avg_age, aging_pct_critical → pct_critical,
    aging_total_open → total_open.
    """
    prev_period = _prev_ym(period)
    if not prev_period:
        return None
    snap = _find_snapshot(snapshots, team, prev_period)
    if snap is None:
        return None
    result: dict = {}
    if "aging_avg_age" in snap:
        result["avg_age"] = _sf(snap["aging_avg_age"])
    if "aging_pct_critical" in snap:
        result["pct_critical"] = _sf(snap["aging_pct_critical"])
    if "aging_total_open" in snap:
        result["total_open"] = _sf(snap["aging_total_open"])
    # Skip if no useful aging data found
    if "avg_age" not in result:
        return None
    return result


def _build_flow_records(df_issues: pd.DataFrame, df_transitions: pd.DataFrame) -> list[dict]:
    """Convert DataFrames to list-of-dicts format for average_time_in_status."""
    if df_transitions.empty:
        return []

    # Group transitions by issue_key
    trans_by_key: dict[str, list[dict]] = {}
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
        # Always convert to plain Python datetime (handles pd.Timestamp, np.datetime64, etc.)
        try:
            if isinstance(raw_ts, datetime.datetime):
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
        if not isinstance(created, datetime.datetime):
            created = pd.Timestamp(created).to_pydatetime().replace(tzinfo=None)

        resdate_raw = row.get("resolutiondate")
        resdate = None
        if resdate_raw is not None:
            try:
                if pd.isna(resdate_raw):
                    resdate = None
                elif isinstance(resdate_raw, datetime.datetime):
                    resdate = resdate_raw.replace(tzinfo=None) if resdate_raw.tzinfo else resdate_raw
                else:
                    ts = pd.Timestamp(resdate_raw)
                    resdate = ts.to_pydatetime().replace(tzinfo=None)
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


# ── InsightEngine ─────────────────────────────────────────────────────────────

class InsightEngine:
    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    def _next_id(self, category: str, severity: str, period: str) -> str:
        base = f"{category}_{severity}_{period}"
        n = self._counters.get(base, 0)
        self._counters[base] = n + 1
        return base if n == 0 else f"{base}_{n}"

    def run(
        self,
        team: str | None,
        period: str,
        df_issues: pd.DataFrame,
        df_transitions: pd.DataFrame,
        prev_snapshots: list[dict],
    ) -> list[InsightEvent]:
        """Run all analyzers and return events sorted by severity."""
        self._counters = {}

        team_arg = team if team and team != "Todos" else None
        team_label = team if team and team != "Todos" else "Todos"

        # Lazily prepare the issues DataFrame
        from core_metrics import prepare_df
        df = prepare_df(df_issues.copy())

        all_events: list[InsightEvent] = []

        all_events.extend(self._analyze_throughput(team_arg, team_label, period, df))
        all_events.extend(self._analyze_aging(team_arg, team_label, period, df, prev_snapshots))
        all_events.extend(self._analyze_dora(team_arg, team_label, period, df))
        all_events.extend(
            self._analyze_flow(team_arg, team_label, period, df, df_transitions, all_events)
        )

        all_events.sort(key=lambda e: SEVERITY_ORDER.get(e.severity, 99))
        return all_events

    # ── Throughput ────────────────────────────────────────────────────────────

    def _analyze_throughput(
        self,
        team_arg: str | None,
        team_label: str,
        period: str,
        df: "pd.DataFrame",
    ) -> list[InsightEvent]:
        try:
            from core_metrics import compute_throughput, build_throughput_diagnostics
        except ImportError:
            return []

        try:
            tp = compute_throughput(df, team=team_arg)
            closed_list = tp.get("closed", [])
            pred = tp.get("predictability", {"label": "N/A"})
            events = build_throughput_diagnostics(
                closed_list, df, team_arg, pred,
                team_label=team_label, period=period,
            )
            return events
        except Exception:
            return []

    # ── Aging ─────────────────────────────────────────────────────────────────

    def _analyze_aging(
        self,
        team_arg: str | None,
        team_label: str,
        period: str,
        df: "pd.DataFrame",
        prev_snapshots: list[dict],
    ) -> list[InsightEvent]:
        try:
            from core_metrics import build_aging_diagnostics
        except ImportError:
            return []

        prev_aging = _find_prev_aging(prev_snapshots, team_label, period)

        try:
            today = datetime.date.today()
            events = build_aging_diagnostics(
                df, team_arg, None,
                today=today,
                prev_aging=prev_aging,
                team_label=team_label,
                period=period,
            )
            return events
        except Exception:
            return []

    # ── DORA ──────────────────────────────────────────────────────────────────

    def _analyze_dora(
        self,
        team_arg: str | None,
        team_label: str,
        period: str,
        df: "pd.DataFrame",
    ) -> list[InsightEvent]:
        try:
            from core_metrics import calculate_metrics_summary, aggregate_metrics_by_month, build_dora_diagnostics
        except ImportError:
            return []

        try:
            summary = calculate_metrics_summary(df)
            if summary.empty:
                return []

            available_months = sorted(summary["year_month"].unique().tolist())
            if not available_months:
                return []

            # Add deploy_freq_interval
            def _interval(row):
                cnt = row.get("deployment_count")
                if cnt is None or (isinstance(cnt, float) and pd.isna(cnt)) or cnt == 0:
                    return None
                ym = str(row.get("year_month", ""))
                try:
                    yr, mo = int(ym[:4]), int(ym[5:7])
                    return calendar.monthrange(yr, mo)[1] / cnt
                except Exception:
                    return None

            summary["deploy_freq_interval"] = summary.apply(_interval, axis=1)

            _DORA_KEYS = ["lead_time_days", "deploy_freq_interval", "mttr_hours", "cfr_percent"]

            def _row(month: str) -> dict | None:
                if not month:
                    return None
                sub = summary[summary["year_month"] == month]
                return None if sub.empty else sub.iloc[0].to_dict()

            # Filter by team
            if team_arg is not None:
                summary_t = summary[summary["team"] == team_arg]
            else:
                # Aggregate across teams per month
                rows = [{"year_month": m, **aggregate_metrics_by_month(summary, m)} for m in available_months]
                summary_t = pd.DataFrame(rows) if rows else pd.DataFrame()
                if not summary_t.empty:
                    summary_t["deploy_freq_interval"] = summary_t.apply(_interval, axis=1)

            available_months_t = sorted(summary_t["year_month"].unique().tolist()) if not summary_t.empty else available_months

            def _row_t(month: str) -> dict | None:
                if not month:
                    return None
                sub = summary_t[summary_t["year_month"] == month]
                return None if sub.empty else sub.iloc[0].to_dict()

            # Find current and previous months with data
            months_with_data = [m for m in available_months_t if _row_t(m) is not None]
            if not months_with_data:
                return []

            current_m = months_with_data[-1]
            prev_m = months_with_data[-2] if len(months_with_data) >= 2 else None

            current_row = _row_t(current_m)
            prev_row = _row_t(prev_m) if prev_m else None

            cur_dict = {k: _sf((current_row or {}).get(k)) for k in _DORA_KEYS}
            prev_dict = ({k: _sf((prev_row or {}).get(k)) for k in _DORA_KEYS}
                         if prev_row is not None else None)

            return build_dora_diagnostics(
                cur_dict, prev_dict,
                team_label=team_label, period=current_m,
            )
        except Exception:
            return []

    # ── Flow ──────────────────────────────────────────────────────────────────

    def _analyze_flow(
        self,
        team_arg: str | None,
        team_label: str,
        period: str,
        df: "pd.DataFrame",
        df_transitions: "pd.DataFrame",
        prior_events: list[InsightEvent],
    ) -> list[InsightEvent]:
        if df_transitions is None or df_transitions.empty:
            return []

        try:
            from status_time import average_time_in_status
            from core_metrics import TERMINAL_STATUSES
        except ImportError:
            return []

        try:
            records = _build_flow_records(df, df_transitions)
            if not records:
                return []

            now = datetime.datetime.now()
            avg_times = average_time_in_status(records, now, team=team_arg)

            if not avg_times:
                return []

            # Filter to non-terminal statuses only; guard against NaN/NaT timedeltas
            non_terminal = {}
            for s, td in avg_times.items():
                if s.strip().lower() in TERMINAL_STATUSES:
                    continue
                try:
                    secs = td.total_seconds()
                    if secs != secs:  # NaN check
                        continue
                    non_terminal[s] = td
                except Exception:
                    continue
            if not non_terminal:
                return []

            total_secs = sum(td.total_seconds() for td in non_terminal.values())
            if total_secs <= 0 or total_secs != total_secs:  # guard zero and NaN
                return []

            # Dominant status = status with highest average time
            dominant_status = max(non_terminal, key=lambda s: non_terminal[s].total_seconds())
            dominant_secs = non_terminal[dominant_status].total_seconds()
            if dominant_secs != dominant_secs:  # guard NaN
                return []
            pct = dominant_secs / total_secs
            if pct != pct:  # guard NaN
                return []

            if pct <= _FLOW_THRESHOLD:
                return []

            # Find a lead_time insight in prior events to link to
            lt_insight = next(
                (
                    e for e in prior_events
                    if e.category == "lead_time"
                    and e.layer == "insight"
                    and e.severity in ("critical", "high", "medium")
                ),
                None,
            )

            severity = "high" if lt_insight is not None else "medium"
            dominant_days = dominant_secs / 86400

            diag_id = self._next_id("flow", severity, period)
            related_diag = [lt_insight.id] if lt_insight is not None else []
            diag = InsightEvent(
                id=diag_id,
                severity=severity,
                category="flow",
                layer="diagnostic",
                title=f"'{dominant_status}' está travando o fluxo",
                description=(
                    f"Em média, {pct:.0%} do tempo que um item passa no processo está sendo gasto "
                    f"em '{dominant_status}' ({dominant_days:.1f} dias). Os itens chegam nesse status "
                    "e ficam esperando — isso está segurando as entregas."
                ),
                evidence={
                    "dominant_status": dominant_status,
                    "pct_lead_time": pct,
                    "avg_days": dominant_days,
                },
                related_ids=related_diag,
                team=team_label,
                period=period,
            )

            rec_id = self._next_id("flow", "info", period)
            rec = InsightEvent(
                id=rec_id,
                severity="info",
                category="flow",
                layer="recommendation",
                title=f"Conversar com o time sobre o que prende itens em '{dominant_status}'",
                description=(
                    f"Vale entender com o time por que tantos itens ficam parados em '{dominant_status}'. "
                    "Fila de revisão? Dependência de outra equipe? Falta de capacidade? "
                    "Uma sessão de 30 minutos pode destravar vários itens de uma vez."
                ),
                evidence={},
                related_ids=[diag_id],
                team=team_label,
                period=period,
            )

            return [diag, rec]

        except Exception:
            return []
