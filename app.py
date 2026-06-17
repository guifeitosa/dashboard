import datetime

import pandas as pd
import streamlit as st

from loader import load_jira_issues_from_csv

st.set_page_config(
    page_title="Engine Metrics",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Global CSS applied to all pages
st.markdown(
    """
<style>
[data-testid="stAppViewContainer"] { background: #f1f5f9; }
[data-testid="block-container"] { padding-top: 1.2rem; padding-bottom: 2rem; }
section[data-testid="stSidebar"] > div { background: white; }
</style>
""",
    unsafe_allow_html=True,
)


@st.cache_data(ttl=3600)
def _aging_critical_count() -> int:
    """Items open for more than 30 days (same criterion as the Aging page)."""
    df = load_jira_issues_from_csv("data/jira_issues_synthetic.csv")
    open_issues = df[~df["is_resolved"]].copy()
    today = pd.Timestamp(datetime.date.today())
    open_issues["dias_parado"] = (today - open_issues["created"]).dt.days
    return int((open_issues["dias_parado"] > 30).sum())


critical = _aging_critical_count()
aging_title = f"Aging 🔴 {critical}" if critical > 0 else "Aging"

home       = st.Page("pages/home.py",          title="Home",        icon="🏠", default=True)
dora_exec  = st.Page("pages/dora_executivo.py", title="Executivo",   icon="📊")
throughput = st.Page("pages/throughput.py",     title="Throughput",  icon="📈")
aging      = st.Page("pages/aging.py",          title=aging_title,   icon="⏳")

pg = st.navigation({
    "Visão Geral":       [home],
    "DORA Metrics":      [dora_exec],
    "Fluxo de Trabalho": [throughput, aging],
})
pg.run()
