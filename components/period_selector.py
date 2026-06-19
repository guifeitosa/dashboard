"""Period selector component with comparison period support."""
from __future__ import annotations

import datetime

import streamlit as st

_MONTH_ABBR_PT = [
    "JAN", "FEV", "MAR", "ABR", "MAI", "JUN",
    "JUL", "AGO", "SET", "OUT", "NOV", "DEZ",
]

_OPTIONS = [
    "Mês anterior",
    "Quarter anterior",
    "Semestre anterior",
    "Mesmo mês do ano anterior",
]

_DEFAULT_OPTION = "Quarter anterior"


def compute_comparison_period(base_period: str, option: str) -> tuple[str, str]:
    """Return (comparison_period, comparison_label) for a given base period and option.

    Pure function — no Streamlit dependency, fully testable.

    Parameters
    ----------
    base_period : "YYYY-MM" string for the main (current) period.
    option      : one of _OPTIONS strings.

    Returns
    -------
    (comparison_period, comparison_label) where comparison_period is "YYYY-MM"
    and comparison_label is a human-readable string like "vs Q1/2026".
    """
    yr = int(base_period[:4])
    mo = int(base_period[5:7])

    months_back = {
        "Mês anterior":                1,
        "Quarter anterior":            3,
        "Semestre anterior":           6,
        "Mesmo mês do ano anterior":   12,
    }.get(option, 3)

    total = yr * 12 + (mo - 1) - months_back
    comp_yr = total // 12
    comp_mo = (total % 12) + 1
    comp_period = f"{comp_yr:04d}-{comp_mo:02d}"

    if option == "Mês anterior":
        label = f"vs {_MONTH_ABBR_PT[comp_mo - 1]}/{comp_yr}"
    elif option == "Quarter anterior":
        q = (comp_mo - 1) // 3 + 1
        label = f"vs Q{q}/{comp_yr}"
    elif option == "Semestre anterior":
        h = 1 if comp_mo <= 6 else 2
        label = f"vs H{h}/{comp_yr}"
    else:
        label = f"vs {_MONTH_ABBR_PT[comp_mo - 1]}/{comp_yr}"

    return comp_period, label


def render_period_selector(key_prefix: str) -> dict:
    """Render a comparison-period selector and return the resolved dict.

    Persists the selected option in st.session_state so the choice survives
    navigation between pages.

    Returns
    -------
    {
        "period":           "YYYY-MM",   # current month (today's date)
        "comparison":       "YYYY-MM",   # computed comparison month
        "comparison_label": "vs Q1/2026" # human-readable label
    }
    """
    today = datetime.date.today()
    base_period = today.strftime("%Y-%m")

    state_key = f"{key_prefix}_comparison_option"
    if state_key not in st.session_state:
        st.session_state[state_key] = _DEFAULT_OPTION

    selected = st.selectbox(
        "Comparar com",
        options=_OPTIONS,
        key=state_key,
    )

    comparison, label = compute_comparison_period(base_period, selected)

    return {
        "period": base_period,
        "comparison": comparison,
        "comparison_label": label,
    }
