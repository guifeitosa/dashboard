import calendar
import datetime
import re

import pandas as pd
import streamlit as st

from loader import load_jira_issues_from_csv
from squad_health import compute_squad_health, render_squad_health

DATA_PATH = "data/jira_issues_synthetic.csv"

_LEVEL_COLOR = {
    "Elite": "#15803d", "High": "#22c55e",
    "Medium": "#ca8a04", "Low": "#dc2626", "N/A": "#94a3b8",
}
_LEVEL_RANK  = {"Elite": 0, "High": 1, "Medium": 2, "Low": 3, "N/A": 99}
_STATUS_COLOR = {"Boa": "#15803d", "Atenção": "#ca8a04", "Crítica": "#dc2626"}
_FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif"


# ── DORA classification (same thresholds as dora_executivo.py) ───────────────

def _dora_band(key: str, value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "N/A"
    v = float(value)
    if key == "lead_time_days":
        return "Elite" if v < 1 else "High" if v <= 7 else "Medium" if v <= 30 else "Low"
    if key == "deploy_freq_interval":
        return "Elite" if v <= 1 else "High" if v <= 5 else "Medium" if v <= 20 else "Low"
    if key == "mttr_hours":
        return "Elite" if v < 1 else "High" if v < 24 else "Medium" if v < 168 else "Low"
    if key == "cfr_percent":
        return "Elite" if v <= 15 else "High" if v <= 30 else "Medium" if v <= 45 else "Low"
    return "N/A"


def _worst_dora(dora_values: dict) -> tuple[str, str]:
    """Return (worst_band, label_of_worst_metric)."""
    names = {
        "lead_time_days": "Lead Time",
        "deploy_freq_interval": "Deploy Freq.",
        "mttr_hours": "MTTR",
        "cfr_percent": "CFR",
    }
    worst_band, worst_name = "N/A", "—"
    for key, val in dora_values.items():
        band = _dora_band(key, val)
        if band == "N/A":
            continue
        if worst_band == "N/A" or _LEVEL_RANK[band] > _LEVEL_RANK[worst_band]:
            worst_band, worst_name = band, names.get(key, key)
    return worst_band, worst_name


# ── Rendering helpers ─────────────────────────────────────────────────────────

def _section_label(text: str) -> None:
    st.markdown(
        f'<div style="font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;'
        f'letter-spacing:.07em;margin:24px 0 10px;">{text}</div>',
        unsafe_allow_html=True,
    )


def _page_card(icon: str, title: str, main: str, main_color: str, detail: str) -> str:
    return (
        f'<div style="background:white;border-radius:14px;padding:20px 22px;'
        f'box-shadow:0 1px 4px rgba(0,0,0,0.07);border:1px solid #f1f5f9;font-family:{_FONT};">'
        f'<div style="font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;'
        f'letter-spacing:.07em;margin-bottom:14px;">{icon} {title}</div>'
        f'<div style="font-size:28px;font-weight:800;color:{main_color};line-height:1.1;'
        f'margin-bottom:6px;">{main}</div>'
        f'<div style="font-size:12px;color:#64748b;line-height:1.5;">{detail}</div>'
        f'</div>'
    )


def _opportunity_sentence(key: str, metrics: dict) -> str:
    m = metrics.get(key, {})
    val = m.get("value")
    unit = m.get("unit", "")
    val_str = f"{val:.1f} {unit}".strip() if val is not None else "sem dado"
    n_items = int(val) if val is not None else "?"
    return {
        "lead_time":  f"Lead Time aumentou para <strong>{val_str}</strong> — cada entrega demora mais para chegar em produção.",
        "throughput": "Throughput (entregas/mês) caiu em relação ao período anterior.",
        "aging":      f"Aging piorou: há <strong>{n_items} itens</strong> em aberto há mais de 30 dias.",
        "mttr":       f"MTTR aumentou para <strong>{val_str}</strong> — incidentes demoram mais para ser resolvidos.",
        "cfr":        f"CFR subiu para <strong>{val_str}</strong> — mais deploys estão gerando incidentes.",
    }.get(key, f"Métrica <strong>{m.get('label', key)}</strong> piorou no período.")


def _alert_card(items: list[tuple[str, str]]) -> None:
    """Render a white card with a list of (emoji, html_text) alert rows."""
    rows_html = ""
    for i, (emoji, text) in enumerate(items):
        border = "" if i == len(items) - 1 else "border-bottom:1px solid #f8fafc;"
        rows_html += (
            f'<div style="display:flex;align-items:flex-start;gap:10px;padding:12px 0;{border}">'
            f'<span style="font-size:16px;flex-shrink:0;margin-top:1px;">{emoji}</span>'
            f'<span style="font-size:13px;color:#334155;line-height:1.5;">{text}</span>'
            f'</div>'
        )
    st.html(
        f'<div style="background:white;border-radius:12px;padding:4px 20px;'
        f'box-shadow:0 1px 4px rgba(0,0,0,0.07);border:1px solid #f1f5f9;font-family:{_FONT};">'
        f'{rows_html}</div>'
    )


# ── Page ──────────────────────────────────────────────────────────────────────

def main():
    render_squad_health()

    # ── Single data load, reused throughout ──────────────────────────────────
    h  = compute_squad_health(DATA_PATH)
    df = load_jira_issues_from_csv(DATA_PATH)

    # Aging open-issue stats (same logic as pages/aging.py)
    open_issues = df[~df["is_resolved"]].copy()
    today = pd.Timestamp(datetime.date.today())
    open_issues["dias_parado"] = (today - open_issues["created"]).dt.days
    total_open = len(open_issues)
    n_red = int((open_issues["dias_parado"] > 30).sum())
    pct_red = n_red / total_open * 100 if total_open > 0 else 0.0

    # DORA values: single-month view, same period dora_executivo.py calls "Último mês"
    # (current_month_dora comes from aggregate_metrics_by_month on the latest DORA month)
    cdora   = h.get("current_month_dora", {})
    cdm_str = h.get("current_dora_month", "")
    deploy_count = int(cdora.get("deployment_count") or 0)
    avg_interval: float | None = None
    if deploy_count > 0 and cdm_str:
        try:
            yr_n, mo_n = int(cdm_str[:4]), int(cdm_str[5:7])
            avg_interval = calendar.monthrange(yr_n, mo_n)[1] / deploy_count
        except (ValueError, IndexError):
            pass

    dora_values = {
        "lead_time_days":       cdora.get("lead_time_days"),
        "deploy_freq_interval":  avg_interval,
        "mttr_hours":           cdora.get("mttr_hours"),
        "cfr_percent":          cdora.get("cfr_percent"),
    }

    # ── Page title ────────────────────────────────────────────────────────────
    st.markdown(
        f'<div style="font-size:28px;font-weight:800;color:#0f172a;letter-spacing:-0.5px;'
        f'font-family:{_FONT};margin-bottom:4px;">Visão Geral</div>'
        f'<div style="font-size:13px;color:#64748b;margin-bottom:20px;">'
        f'Consolidado das métricas de engenharia</div>',
        unsafe_allow_html=True,
    )

    # ── 1. Cards de resumo das páginas ───────────────────────────────────────
    _section_label("Resumo das Páginas")

    worst_band, worst_metric_name = _worst_dora(dora_values)
    dora_color = _LEVEL_COLOR.get(worst_band, "#94a3b8")
    if worst_band == "N/A":
        dora_detail = "Sem dados suficientes"
    elif worst_band == "Elite":
        dora_detail = "Todos os indicadores em faixa Elite"
    else:
        dora_detail = f"Fator de maior atenção: {worst_metric_name}"

    tp_m      = h["metrics"].get("throughput", {})
    tp_status = tp_m.get("status", "N/A")
    tp_color  = _STATUS_COLOR.get(tp_status, "#94a3b8")
    tp_val    = tp_m.get("value")
    tp_detail = (
        f"Média: {tp_val:.0f} itens/mês (últimos 3 meses)"
        if tp_val is not None else "Sem dados"
    )

    aging_color = "#dc2626" if pct_red > 60 else "#ca8a04" if pct_red > 30 else "#15803d"

    c1, c2, c3 = st.columns(3)
    with c1:
        st.html(_page_card("📊", "DORA Executivo", worst_band, dora_color, dora_detail))
        st.page_link("pages/dora_executivo.py", label="Ver Executivo →")
    with c2:
        st.html(_page_card("📈", "Throughput", tp_status, tp_color, tp_detail))
        st.page_link("pages/throughput.py", label="Ver Throughput →")
    with c3:
        st.html(_page_card(
            "⏳", "Aging",
            f"{total_open} abertos",
            aging_color,
            f"{n_red} itens ({pct_red:.0f}%) há mais de 30 dias",
        ))
        st.page_link("pages/aging.py", label="Ver Aging →")

    # ── 2. Maior Oportunidade ─────────────────────────────────────────────────
    _section_label("Maior Oportunidade")

    impacts = h.get("impacts", [])
    negative = sorted(
        [i for i in impacts if i["delta_points"] < 0],
        key=lambda i: i["delta_points"],
    )

    if negative:
        worst_imp = negative[0]
        delta_txt = f"{worst_imp['delta_points']:+.0f} pts no score"
        sentence  = _opportunity_sentence(worst_imp["key"], h["metrics"])
        opp_html = (
            f'<div style="background:white;border-radius:14px;padding:20px 24px;'
            f'box-shadow:0 1px 4px rgba(0,0,0,0.07);border:1px solid #fee2e2;font-family:{_FONT};">'
            f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">'
            f'<span style="font-size:20px;">⚠️</span>'
            f'<span style="font-size:15px;font-weight:800;color:#dc2626;">{worst_imp["label"]}</span>'
            f'<span style="font-size:12px;font-weight:700;color:#dc2626;'
            f'background:rgba(220,38,38,0.08);padding:2px 9px;border-radius:6px;">{delta_txt}</span>'
            f'</div>'
            f'<div style="font-size:13px;color:#334155;line-height:1.6;">{sentence}</div>'
            f'</div>'
        )
    else:
        # No deterioration detected — show metric with most room to improve
        all_scored = sorted(h["metrics"].items(), key=lambda x: x[1]["score"])
        low_key, low_m = all_scored[0] if all_scored else ("N/A", {"label": "—", "score": 0})
        msg = (
            "Sem janela histórica para comparação." if not impacts else
            "Nenhuma deterioração significativa no período comparado."
        )
        opp_html = (
            f'<div style="background:white;border-radius:14px;padding:20px 24px;'
            f'box-shadow:0 1px 4px rgba(0,0,0,0.07);border:1px solid #f1f5f9;font-family:{_FONT};">'
            f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">'
            f'<span style="font-size:20px;">💡</span>'
            f'<span style="font-size:15px;font-weight:800;color:#0f172a;">{low_m["label"]}</span>'
            f'<span style="font-size:12px;color:#64748b;background:#f8fafc;'
            f'padding:2px 9px;border-radius:6px;font-weight:600;">{low_m["score"]:.0f}/100 pts</span>'
            f'</div>'
            f'<div style="font-size:13px;color:#64748b;line-height:1.6;">'
            f'{msg} Maior potencial de melhoria contínua: <strong>{low_m["label"]}</strong>.'
            f'</div>'
            f'</div>'
        )
    st.html(opp_html)

    # ── 3. Alertas ────────────────────────────────────────────────────────────
    _section_label("Alertas")

    alerts: list[tuple[str, str]] = []

    # DORA "Low" bands
    _dora_names = {
        "lead_time_days":      "Lead Time",
        "deploy_freq_interval": "Deployment Frequency",
        "mttr_hours":           "MTTR",
        "cfr_percent":          "CFR (Change Failure Rate)",
    }
    _dora_units = {
        "lead_time_days": "dias", "deploy_freq_interval": "dias/deploy",
        "mttr_hours": "horas",   "cfr_percent": "%",
    }
    for dkey, dname in _dora_names.items():
        val = dora_values.get(dkey)
        if _dora_band(dkey, val) == "Low":
            val_str = f"{val:.1f} {_dora_units[dkey]}" if val is not None else "sem dado"
            alerts.append(("🔴", f"<strong>{dname}</strong> em faixa <strong>Low</strong> ({val_str})"))

    # Throughput critical (not already a DORA metric in the same source)
    if tp_status == "Crítica":
        alerts.append(("🔴", "<strong>Throughput</strong> em estado <strong>Crítico</strong> — entregas mensais muito abaixo do histórico"))

    # Aging: majority of open items in critical band
    if total_open > 0 and pct_red > 60:
        alerts.append((
            "🔴",
            f"<strong>Aging crítico</strong>: {pct_red:.0f}% dos itens abertos "
            f"({n_red} de {total_open}) estão há mais de 30 dias sem avançar",
        ))

    # Any squad health metric still "Crítica" not already covered above
    _already_via_dora = {"lead_time": "lead_time_days", "mttr": "mttr_hours", "cfr": "cfr_percent"}
    for mkey, m in h["metrics"].items():
        if m.get("status") != "Crítica":
            continue
        if mkey == "throughput":
            continue  # handled above
        if mkey == "aging":
            continue  # handled by aging threshold above
        dkey = _already_via_dora.get(mkey)
        if dkey and _dora_band(dkey, dora_values.get(dkey)) == "Low":
            continue  # already surfaced as a DORA alert
        alerts.append(("🟡", f"<strong>{m.get('label', mkey)}</strong> com score Crítico ({m.get('score', 0):.0f}/100)"))

    if not alerts:
        st.html(
            f'<div style="background:white;border-radius:12px;padding:16px 20px;'
            f'box-shadow:0 1px 4px rgba(0,0,0,0.07);border:1px solid #f1f5f9;font-family:{_FONT};">'
            f'<div style="display:flex;align-items:center;gap:8px;">'
            f'<span style="font-size:18px;">✅</span>'
            f'<span style="font-size:14px;font-weight:600;color:#15803d;">'
            f'Nenhum alerta crítico no momento</span>'
            f'</div></div>'
        )
    else:
        _alert_card(alerts)


main()
