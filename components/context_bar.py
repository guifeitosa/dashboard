"""Context bar component — shows active team/period filters at the top of each page."""
import streamlit as st


def render_context_bar(
    team: str | None = None,
    period: str | None = None,
    comparison_period: str | None = None,
) -> None:
    """Render the slim context bar showing active team and optional period filters.

    Parameters
    ----------
    team              : Active team label. If None, reads from st.session_state global_team.
    period            : Main period string (e.g. "JUN/2026") — optional.
    comparison_period : Comparison period label (e.g. "MAR/2026") — optional.
                        Only shown when period is also set.
    """
    if team is None:
        team = st.session_state.get("global_team", "Todos")

    parts = [f"<strong>Mostrando:</strong> {team}"]
    if period:
        parts.append(f"<strong>Período:</strong> {period}")
    if period and comparison_period:
        parts.append(f"<strong>Comparando com:</strong> {comparison_period}")

    text = "&nbsp;&nbsp;·&nbsp;&nbsp;".join(parts)
    st.markdown(
        f'<div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;'
        f'padding:7px 16px;margin-bottom:14px;font-size:13px;color:#3730a3;'
        f"font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;\">"
        f'🔍 {text}</div>',
        unsafe_allow_html=True,
    )
