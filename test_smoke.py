"""Smoke tests: every page loads without exception for 'Todos' and each team.

Run with the demo DB so data is deterministic:
    $env:DASHBOARD_DB_PATH = "metrics_demo.db"
    python -m pytest test_smoke.py -v
"""
import os

# Must be set before any project module is imported so db.py picks it up.
os.environ.setdefault("DASHBOARD_DB_PATH", "metrics_demo.db")

import pytest
from streamlit.testing.v1 import AppTest

PAGES = [
    "pages/home.py",
    "pages/dora_executivo.py",
    "pages/throughput.py",
    "pages/aging.py",
    "pages/fluxo.py",
]

TEAMS = ["Todos", "Time Alpha", "Time Beta", "Time Gamma"]

_TIMEOUT = 20


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.split("/")[-1].replace(".py", ""))
@pytest.mark.parametrize("team", TEAMS)
def test_page_no_exception(page: str, team: str) -> None:
    at = AppTest.from_file(page, default_timeout=_TIMEOUT)
    at.session_state["global_team"] = team
    at.run()
    assert not at.exception, (
        f"[{page}] team={team!r} raised: {at.exception}"
    )
