"""Ready-to-List: streamlit entry point and role switcher."""
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="Ready-to-List", page_icon=":material/gavel:", layout="wide")

HERE = Path(__file__).parent


def page(path, title, icon, **kw):
    return st.Page(path, title=title, icon=icon, **kw) if (HERE / path).exists() else None


sections = {
    "Demo": [page("pages/walkthrough.py", "Full flow, one case", ":material/route:")],
    "Court": [
        page("pages/judge.py", "Judge dashboard", ":material/gavel:", default=True),
        page("pages/court_master.py", "Court master", ":material/fact_check:"),
        page("pages/calendar.py", "Court calendar", ":material/calendar_month:"),
        page("pages/filing.py", "Pre-filing check", ":material/upload_file:"),
        page("pages/case_types.py", "Case types and judge time", ":material/category:"),
    ],
    "Proof": [
        page("pages/optimizer_lab.py", "Optimisation lab", ":material/tune:"),
        page("pages/simulator.py", "Simulator", ":material/monitoring:"),
        page("pages/accuracy.py", "Model accuracy", ":material/target:"),
        page("pages/audit.py", "Audit log", ":material/history:"),
    ],
}
pages = st.navigation({k: [p for p in v if p] for k, v in sections.items()})
pages.run()
