import calendar

import pandas as pd
import streamlit as st

from core_metrics import dora_band, prepare_df
from db import engine
from metrics import aggregate_metrics_by_month, calculate_metrics_summary
from squad_health import render_squad_health

MONTH_PT = {
    "01": "JAN", "02": "FEV", "03": "MAR", "04": "ABR",
    "05": "MAI", "06": "JUN", "07": "JUL", "08": "AGO",
    "09": "SET", "10": "OUT", "11": "NOV", "12": "DEZ",
}

METRIC_DEFS = [
    {
        "key": "lead_time_days",
        "icon": "🕐",
        "label": "Lead Time for Changes",
        "subtitle": "(dias úteis)",
        "icon_bg": "#dbeafe",
        "thresholds": {
            "Elite": "< 1 dia", "High": "1–7 dias",
            "Medium": "7–30 dias", "Low": "> 30 dias",
        },
    },
    {
        "key": "deploy_freq_interval",
        "icon": "🚀",
        "label": "Deployment Frequency",
        "subtitle": "(intervalo médio)",
        "icon_bg": "#ede9fe",
        "thresholds": {
            "Elite": "≤ 1 dia", "High": "≤ 5 dias",
            "Medium": "≤ 20 dias", "Low": "> 20 dias",
        },
    },
    {
        "key": "mttr_hours",
        "icon": "🔴",
        "label": "MTTR",
        "subtitle": "(horas)",
        "icon_bg": "#fee2e2",
        "thresholds": {
            "Elite": "< 1h", "High": "< 24h",
            "Medium": "< 168h", "Low": "> 168h",
        },
    },
    {
        "key": "cfr_percent",
        "icon": "🛡️",
        "label": "Change Failure Rate",
        "subtitle": "(CFR %)",
        "icon_bg": "#dbeafe",
        "thresholds": {
            "Elite": "0–15%", "High": "16–30%",
            "Medium": "31–45%", "Low": "> 45%",
        },
    },
]

LEVEL_COLOR = {
    "Elite": "#15803d",
    "High": "#22c55e",
    "Medium": "#ca8a04",
    "Low": "#dc2626",
    "N/A": "#94a3b8",
}

LEVEL_BG = {
    "Elite": "rgba(21,128,61,0.07)",
    "High": "rgba(34,197,94,0.07)",
    "Medium": "rgba(202,138,4,0.07)",
    "Low": "rgba(220,38,38,0.06)",
    "N/A": "transparent",
}

COLS = "2.3fr 0.9fr 1.1fr 0.7fr 0.7fr 0.7fr 1.1fr 1.15fr 1fr"


@st.cache_data(ttl=300)
def _load_issues() -> pd.DataFrame:
    df = pd.read_sql("SELECT * FROM issues_raw", engine)
    return prepare_df(df)


def _miss(v) -> bool:
    return v is None or (isinstance(v, float) and pd.isna(v))


def classify(key: str, v) -> tuple[str, str]:
    level = dora_band(key, v)
    return level, LEVEL_COLOR[level]


def fmt_val(v, key: str) -> str:
    if _miss(v):
        return "—"
    v = float(v)
    if key == "cfr_percent":
        return f"{v:.1f}%"
    if key == "mttr_hours":
        return f"{v:.1f}h"
    if key in ("lead_time_days", "deploy_freq_interval"):
        return f"{v:.1f}d"
    return f"{v:.1f}"


def fmt_month(ym: str) -> str:
    if ym == "-" or not ym:
        return "—"
    try:
        y, m = ym.split("-")
        return f"{MONTH_PT.get(m, m)}/{y}"
    except Exception:
        return ym


def sparkline_svg(values: list, color: str, w: int = 108, h: int = 44) -> str:
    pts = [(i, float(v)) for i, v in enumerate(values) if not _miss(v)]
    if len(pts) < 2:
        return f'<svg width="{w}" height="{h}"></svg>'
    n = len(values)
    ys = [v for _, v in pts]
    lo, hi = min(ys), max(ys)
    px, py = 6, 6

    def cx(i):
        return px + i / max(n - 1, 1) * (w - 2 * px)

    def cy(v):
        return py + (1.0 - (v - lo) / (hi - lo + 1e-9)) * (h - 2 * py)

    coords = [(cx(i), cy(v)) for i, v in pts]
    line = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    area = line + f" L {coords[-1][0]:.1f},{h} L {coords[0][0]:.1f},{h} Z"
    hc = color.lstrip("#")
    r, g, b = int(hc[0:2], 16), int(hc[2:4], 16), int(hc[4:6], 16)
    dots = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{color}" stroke="white" stroke-width="1"/>'
        for x, y in coords
    )
    return (
        f'<svg width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg" style="display:block;">'
        f'<path d="{area}" fill="rgba({r},{g},{b},0.13)"/>'
        f'<path d="{line}" stroke="{color}" stroke-width="2" fill="none"'
        f' stroke-linejoin="round" stroke-linecap="round"/>'
        f'{dots}</svg>'
    )


def add_deploy_freq_interval(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    def _interval(row):
        cnt = row.get("deployment_count")
        if _miss(cnt) or cnt == 0:
            return None
        ym = str(row.get("year_month", ""))
        try:
            yr, mo = int(ym[:4]), int(ym[5:7])
            return calendar.monthrange(yr, mo)[1] / cnt
        except Exception:
            return None

    df["deploy_freq_interval"] = df.apply(_interval, axis=1)
    return df


def main():
    # set_page_config is handled in app.py (navigation entry point)
    render_squad_health()

    df = _load_issues()
    summary = calculate_metrics_summary(df)

    months = sorted(summary["year_month"].unique().tolist())

    selected_team = st.session_state.get("global_team", "Todos")
    st.sidebar.title("Filtros")
    if len(months) > 1:
        selected_start = st.sidebar.select_slider("Período inicial", options=months, value=months[0])
        selected_end = st.sidebar.select_slider("Período final", options=months, value=months[-1])
    else:
        selected_start = selected_end = months[0]
        st.sidebar.write(f"Período: {fmt_month(months[0])}")

    if selected_start > selected_end:
        st.warning("Período inválido: início deve ser anterior ao fim.")
        return

    filtered = summary[
        (summary["year_month"] >= selected_start) & (summary["year_month"] <= selected_end)
    ].copy()
    if selected_team != "Todos":
        filtered = filtered[filtered["team"] == selected_team]

    filtered = filtered.sort_values(["team", "year_month"])

    if filtered.empty:
        st.error("Sem dados para o filtro selecionado.")
        return

    available_months = sorted(filtered["year_month"].unique().tolist())

    if selected_team == "Todos":
        rows = [{"year_month": m, **aggregate_metrics_by_month(filtered, m)} for m in available_months]
        monthly_df = pd.DataFrame(rows) if rows else pd.DataFrame()
    else:
        monthly_df = filtered.copy()

    monthly_df = add_deploy_freq_interval(monthly_df)

    def _row(month: str) -> dict | None:
        if month == "-" or not month:
            return None
        sub = monthly_df[monthly_df["year_month"] == month]
        return None if sub.empty else sub.iloc[0].to_dict()

    def _has_key_data(month: str) -> bool:
        r = _row(month)
        if not r:
            return False
        return not _miss(r.get("lead_time_days")) or not _miss(r.get("mttr_hours"))

    candidates = [m for m in available_months if m <= selected_end]
    with_data = [m for m in candidates if _has_key_data(m)]
    current_month = with_data[-1] if with_data else (candidates[-1] if candidates else selected_end)

    trend_months = [m for m in available_months if m <= current_month][-4:]
    raw_hist = [m for m in available_months if m < current_month][-3:]
    hist_months = ["-"] * (3 - len(raw_hist)) + raw_hist

    current_row = _row(current_month)

    def _hist_avg() -> dict | None:
        metric_keys = [md["key"] for md in METRIC_DEFS]
        result: dict = {}
        has_any = False
        for k in metric_keys:
            vals = [
                float(r[k])
                for hm in hist_months
                if (r := _row(hm)) is not None and not _miss(r.get(k))
            ]
            result[k] = sum(vals) / len(vals) if vals else None
            if vals:
                has_any = True
        return result if has_any else None

    compare_row = _hist_avg()
    h_labels = [fmt_month(m) for m in hist_months]

    # ── Page title ──────────────────────────────────────────────────────────
    st.markdown(
        f"""
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
            max-width:1380px;margin:0 auto 20px;">
  <div style="display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:20px;">
    <div>
      <div style="font-size:28px;font-weight:800;color:#0f172a;letter-spacing:-0.5px;">DORA Metrics</div>
      <div style="font-size:13px;color:#64748b;margin-top:2px;">Visão Executiva</div>
    </div>
    <div style="background:white;border:1px solid #e2e8f0;border-radius:8px;padding:8px 14px;
                font-size:13px;color:#475569;font-weight:500;">
      &#128197; {fmt_month(current_month)}
    </div>
  </div>
""",
        unsafe_allow_html=True,
    )

    # ── Table header ────────────────────────────────────────────────────────
    hdr = "font-size:10px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.08em;"
    sub = "font-size:10px;font-weight:600;color:#cbd5e1;text-align:center;"

    st.markdown(
        f"""
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
            max-width:1380px;margin:0 auto;">
  <div style="display:grid;grid-template-columns:{COLS};gap:10px;padding:4px 20px 0;align-items:end;">
    <div style="{hdr}">M&#233;trica</div>
    <div style="{hdr}">Status</div>
    <div style="{hdr}">&#218;ltimo M&#234;s</div>
    <div style="grid-column:span 3;text-align:center;border-bottom:1.5px solid #e2e8f0;padding-bottom:4px;">
      <span style="{hdr}">Hist&#243;rico (&#218;ltimos 3 Meses)</span>
    </div>
    <div></div>
    <div style="{hdr}">Tend&#234;ncia</div>
    <div style="{hdr}">Faixa DORA</div>
  </div>
  <div style="display:grid;grid-template-columns:{COLS};gap:10px;padding:3px 20px 10px;">
    <div></div><div></div><div></div>
    <div style="{sub}">{h_labels[0]}</div>
    <div style="{sub}">{h_labels[1]}</div>
    <div style="{sub}">{h_labels[2]}</div>
    <div></div><div></div><div></div>
  </div>
""",
        unsafe_allow_html=True,
    )

    # ── Metric rows ─────────────────────────────────────────────────────────
    for m in METRIC_DEFS:
        key = m["key"]

        cur = current_row.get(key) if current_row else None
        cur_val = None if _miss(cur) else float(cur)

        hist_vals = [
            (None if _miss(v := (_row(hm) or {}).get(key)) else float(v))
            for hm in hist_months
        ]
        spark_vals = [
            (None if _miss(v := (_row(tm) or {}).get(key)) else float(v))
            for tm in trend_months
        ]

        cmp = (compare_row or {}).get(key)
        cmp_val = None if _miss(cmp) else float(cmp)

        level, lc = classify(key, cur_val)
        lvl_bg = LEVEL_BG[level]
        lvl_threshold = m["thresholds"].get(level, "")

        # Arrow shows quality direction (↑ = improving, ↓ = worsening),
        # not numerical direction. All 4 metrics: lower value = better quality.
        if cur_val is None or cmp_val is None:
            t_arrow, t_label, tc = "&#8212;", "Sem dado", "#94a3b8"
        elif cur_val < cmp_val:
            t_arrow, t_label, tc = "&#8593;", "Melhorando", "#15803d"
        elif cur_val > cmp_val:
            t_arrow, t_label, tc = "&#8595;", "Piorando", "#dc2626"
        else:
            t_arrow, t_label, tc = "&#8212;", "Est&#225;vel", "#94a3b8"

        svg = sparkline_svg(spark_vals, lc)
        hist_cells = "".join(
            f'<div style="text-align:center;font-size:13px;color:#64748b;font-weight:500;">{fmt_val(v, key)}</div>'
            for v in hist_vals
        )

        st.markdown(
            f"""
<div style="display:grid;grid-template-columns:{COLS};gap:10px;align-items:center;
  background:linear-gradient(to right,#ffffff 55%,{lvl_bg} 100%);
  border-radius:12px;padding:14px 20px;margin-bottom:8px;
  box-shadow:0 1px 4px rgba(0,0,0,0.07);border:1px solid #f1f5f9;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <div style="display:flex;align-items:center;gap:12px;">
    <div style="width:42px;height:42px;border-radius:10px;background:{m['icon_bg']};
      flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:20px;">{m['icon']}</div>
    <div>
      <div style="font-weight:700;font-size:14px;color:#0f172a;line-height:1.3;">{m['label']}</div>
      <div style="font-size:11px;color:#94a3b8;margin-top:1px;">{m['subtitle']}</div>
    </div>
  </div>
  <div style="display:flex;align-items:center;gap:5px;">
    <span style="color:{lc};font-size:16px;line-height:1;">&#9679;</span>
    <span style="font-weight:700;color:{lc};font-size:13px;">{level}</span>
  </div>
  <div>
    <div style="font-size:26px;font-weight:800;color:#0f172a;line-height:1.1;letter-spacing:-0.5px;">{fmt_val(cur_val, key)}</div>
    <div style="font-size:11px;color:#94a3b8;margin-top:2px;">{lvl_threshold}</div>
  </div>
  {hist_cells}
  <div style="display:flex;align-items:center;justify-content:center;">{svg}</div>
  <div style="display:flex;align-items:center;gap:5px;">
    <span style="color:{tc};font-size:20px;font-weight:700;line-height:1;">{t_arrow}</span>
    <span style="font-weight:700;color:{tc};font-size:13px;">{t_label}</span>
  </div>
  <div>
    <div style="font-size:19px;font-weight:800;color:{lc};line-height:1.2;">{level}</div>
    <div style="font-size:11px;color:#94a3b8;margin-top:2px;">{lvl_threshold}</div>
  </div>
</div>
""",
            unsafe_allow_html=True,
        )

    # ── FAIXAS DORA footer (uses st.html to avoid markdown parser interference) ──
    def dot(c):
        return f"<span style=\"color:{c};font-size:13px;\">&#9679;</span>"

    ec, hc, mc, lc_ = (
        LEVEL_COLOR["Elite"], LEVEL_COLOR["High"],
        LEVEL_COLOR["Medium"], LEVEL_COLOR["Low"],
    )

    footer_html = (
        '<div style="margin-top:16px;background:white;border-radius:12px;padding:20px 24px;'
        'box-shadow:0 1px 4px rgba(0,0,0,0.06);border:1px solid #f1f5f9;'
        'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,sans-serif;">'
        '<div style="font-size:10px;font-weight:700;color:#94a3b8;text-transform:uppercase;'
        'letter-spacing:.08em;margin-bottom:14px;">Faixas DORA</div>'
        '<div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:24px;">'

        '<div>'
        '<div style="font-weight:700;font-size:13px;color:#0f172a;margin-bottom:8px;">'
        'Lead Time for Changes'
        '<span style="font-weight:400;color:#94a3b8;font-size:11px;"> (dias &#250;teis)</span>'
        '</div>'
        '<div style="font-size:13px;line-height:1.9;color:#334155;">'
        + dot(ec) + ' Elite: &lt; 1 dia<br>'
        + dot(hc) + ' High: 1 a 7 dias<br>'
        + dot(mc) + ' Medium: 7 dias a 1 m&#234;s<br>'
        + dot(lc_) + ' Low: &gt; 1 m&#234;s'
        + '</div></div>'

        '<div>'
        '<div style="font-weight:700;font-size:13px;color:#0f172a;margin-bottom:8px;">'
        'Deployment Frequency'
        '<span style="font-weight:400;color:#94a3b8;font-size:11px;"> (intervalo m&#233;dio)</span>'
        '</div>'
        '<div style="font-size:13px;line-height:1.9;color:#334155;">'
        + dot(ec) + ' Elite: &#8804; 1 dia<br>'
        + dot(hc) + ' High: &#8804; 5 dias<br>'
        + dot(mc) + ' Medium: &#8804; 20 dias<br>'
        + dot(lc_) + ' Low: &gt; 20 dias'
        + '</div></div>'

        '<div>'
        '<div style="font-weight:700;font-size:13px;color:#0f172a;margin-bottom:8px;">'
        'MTTR'
        '<span style="font-weight:400;color:#94a3b8;font-size:11px;"> (horas)</span>'
        '</div>'
        '<div style="font-size:13px;line-height:1.9;color:#334155;">'
        + dot(ec) + ' Elite: &lt; 1h<br>'
        + dot(hc) + ' High: &lt; 24h<br>'
        + dot(mc) + ' Medium: &lt; 1 semana<br>'
        + dot(lc_) + ' Low: &gt; 1 semana'
        + '</div></div>'

        '<div>'
        '<div style="font-weight:700;font-size:13px;color:#0f172a;margin-bottom:8px;">'
        'Change Failure Rate'
        '<span style="font-weight:400;color:#94a3b8;font-size:11px;"> (CFR %)</span>'
        '</div>'
        '<div style="font-size:13px;line-height:1.9;color:#334155;">'
        + dot(ec) + ' Elite: 0% &#8211; 15%<br>'
        + dot(hc) + ' High: 16% &#8211; 30%<br>'
        + dot(mc) + ' Medium: 31% &#8211; 45%<br>'
        + dot(lc_) + ' Low: &gt; 45%'
        + '</div></div>'

        '</div></div>'
    )
    st.html(footer_html)


main()
