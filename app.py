import streamlit as st

from core_metrics import compute_aging, prepare_df
from db import engine

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


@st.cache_data(ttl=300)
def _aging_critical_count() -> int:
    """Items open for more than 30 days — mirrors compute_aging's '>30d' band."""
    import pandas as pd
    df = pd.read_sql("SELECT * FROM issues_raw", engine)
    df = prepare_df(df)
    aging = compute_aging(df)
    return aging["bands"]["30–60d"] + aging["bands"]["60+d"]


critical = _aging_critical_count()
aging_title = f"Aging 🔴 {critical}" if critical > 0 else "Aging"

home       = st.Page("pages/home.py",          title="Home",        icon="🏠", default=True)
dora_exec  = st.Page("pages/dora_executivo.py", title="Executivo",   icon="📊")
throughput = st.Page("pages/throughput.py",     title="Throughput",  icon="📈")
aging      = st.Page("pages/aging.py",          title=aging_title,   icon="⏳")
fluxo      = st.Page("pages/fluxo.py",          title="Fluxo",       icon="🌊")

pg = st.navigation({
    "Visão Geral":       [home],
    "DORA Metrics":      [dora_exec],
    "Fluxo de Trabalho": [throughput, aging, fluxo],
})
pg.run()
