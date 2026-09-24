"""Samay: streamlit entry point. Sign in, then the judge's page or the court master's page."""
import streamlit as st

st.set_page_config(page_title="Samay", page_icon=":material/gavel:", layout="wide")

pages = st.navigation([
    st.Page("pages/login.py", title="Sign in", default=True),
    st.Page("pages/judge.py", title="Judge"),
    st.Page("pages/court_master.py", title="Court master"),
], position="hidden")
pages.run()
