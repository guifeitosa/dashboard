"""Design-system card components."""
from __future__ import annotations

import streamlit as st

from components.badges import render_badge

_FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif"

_KPI_COLORS: dict[str, str] = {
    "default": "#0f172a",
    "success": "#15803d",
    "warning": "#d97706",
    "danger":  "#dc2626",
}

_SEVERITY_COLOR: dict[str, str] = {
    "critical": "#dc2626",
    "high":     "#ca8a04",
    "medium":   "#0369a1",
    "low":      "#64748b",
    "info":     "#64748b",
}


def render_metric_card(
    title: str,
    icon: str | None,
    value: str,
    subtitle: str,
    badge_label: str,
    badge_severity: str,
    delta: str | None = None,
    delta_positive: bool | None = None,
    link_label: str = "Ver mais →",
    link_page: str | None = None,
) -> None:
    """Render a summary card for the Home page (DORA, Throughput, Aging sections)."""
    icon_part = f"{icon} " if icon else ""
    delta_html = ""
    if delta:
        if delta_positive is True:
            delta_color = "#15803d"
        elif delta_positive is False:
            delta_color = "#dc2626"
        else:
            delta_color = "#64748b"
        delta_html = (
            f'<div style="font-size:11px;font-weight:600;color:{delta_color};'
            f'margin-top:4px;">{delta}</div>'
        )

    st.html(
        f'<div style="background:white;border-radius:14px;padding:20px 22px;'
        f'box-shadow:0 1px 4px rgba(0,0,0,0.07);border:1px solid #f1f5f9;'
        f'font-family:{_FONT};min-height:120px;">'
        f'<div style="font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;'
        f'letter-spacing:.07em;margin-bottom:10px;">{icon_part}{title}</div>'
        f'<div style="font-size:26px;font-weight:800;color:#0f172a;line-height:1.1;'
        f'margin-bottom:6px;">{value}</div>'
        f'<div style="font-size:12px;color:#64748b;line-height:1.5;margin-bottom:8px;">'
        f'{subtitle}</div>'
        f'{delta_html}'
        f'</div>'
    )
    render_badge(badge_label, badge_severity)
    if link_page:
        try:
            st.page_link(link_page, label=link_label)
        except Exception:
            pass


def render_insight_card(
    title: str,
    description: str,
    recommendation: str,
    severity: str,
    why_it_matters: list[str] | None = None,
) -> None:
    """Render a diagnostic insight card with optional collapsible 'Por que isso importa'."""
    sev_color = _SEVERITY_COLOR.get(severity, "#64748b")

    st.html(
        f'<div style="background:white;border-radius:12px;padding:16px 20px;'
        f'box-shadow:0 1px 4px rgba(0,0,0,0.07);border-left:4px solid {sev_color};'
        f'margin-bottom:2px;font-family:{_FONT};">'
        f'<div style="font-size:14px;font-weight:700;color:{sev_color};margin-bottom:8px;">'
        f'⚠ {title}</div>'
        f'<div style="font-size:13px;color:#374151;margin-bottom:8px;">{description}</div>'
        f'<div style="font-size:12px;color:#15803d;border-top:1px solid #f1f5f9;padding-top:8px;">'
        f'✅ <strong>O que você pode fazer:</strong> {recommendation}</div>'
        f'</div>'
    )
    if why_it_matters:
        with st.expander("Por que isso importa"):
            for bullet in why_it_matters:
                st.markdown(f"- {bullet}")


def render_kpi_card(
    label: str,
    value: str,
    subtitle: str = "",
    color: str = "default",
) -> None:
    """Render a compact KPI metric card (Throughput, Aging, WIP, Fluxo pages)."""
    value_color = _KPI_COLORS.get(color, _KPI_COLORS["default"])
    sub_el = (
        f'<div style="font-size:11px;color:#64748b;margin-top:5px;line-height:1.4;">'
        f'{subtitle}</div>'
        if subtitle else ""
    )
    st.html(
        f'<div style="background:white;border-radius:12px;padding:18px 22px;'
        f'box-shadow:0 1px 4px rgba(0,0,0,0.07);border:1px solid #f1f5f9;'
        f'font-family:{_FONT};">'
        f'<div style="font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;'
        f'letter-spacing:.07em;margin-bottom:6px;">{label}</div>'
        f'<div style="font-size:28px;font-weight:800;color:{value_color};line-height:1.1;">'
        f'{value}</div>'
        f'{sub_el}'
        f'</div>'
    )
