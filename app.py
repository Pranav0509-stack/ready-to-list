"""Ready-to-List: streamlit entry point and role switcher."""
import streamlit as st

st.set_page_config(page_title="Ready-to-List", page_icon=":material/gavel:", layout="wide")

pages = st.navigation({
    "Court": [
        st.Page("pages/judge.py", title="Judge dashboard", icon=":material/gavel:", default=True),
        st.Page("pages/court_master.py", title="Court master", icon=":material/fact_check:"),
    ],
    "Proof": [
        st.Page("pages/simulator.py", title="Simulator", icon=":material/monitoring:"),
        st.Page("pages/accuracy.py", title="Model accuracy", icon=":material/target:"),
        st.Page("pages/audit.py", title="Audit log", icon=":material/history:"),
    ],
})
pages.run()
