"""Design-system badge component."""
import streamlit as st

_SEVERITY_STYLES: dict[str, tuple[str, str]] = {
    "elite":          ("#14532d", "rgba(22,163,74,0.12)"),
    "high_performer": ("#14532d", "rgba(22,163,74,0.12)"),
    "high":           ("#1e3a8a", "rgba(37,99,235,0.12)"),
    "boa":            ("#1e3a8a", "rgba(37,99,235,0.12)"),
    "medium":         ("#78350f", "rgba(217,119,6,0.12)"),
    "atencao":        ("#78350f", "rgba(217,119,6,0.12)"),
    "low":            ("#7f1d1d", "rgba(220,38,38,0.12)"),
    "critico":        ("#7f1d1d", "rgba(220,38,38,0.12)"),
    "n/a":            ("#374151", "rgba(107,114,128,0.12)"),
    "sem_dados":      ("#374151", "rgba(107,114,128,0.12)"),
}

_DEFAULT_STYLE = ("#374151", "rgba(107,114,128,0.12)")


def render_badge(label: str, severity: str) -> None:
    """Render a pill-shaped badge colored by severity level."""
    text_color, bg = _SEVERITY_STYLES.get(severity.lower(), _DEFAULT_STYLE)
    st.markdown(
        f'<span style="display:inline-block;background:{bg};color:{text_color};'
        f'font-size:12px;font-weight:700;padding:3px 10px;border-radius:999px;'
        f'letter-spacing:.03em;">{label}</span>',
        unsafe_allow_html=True,
    )
