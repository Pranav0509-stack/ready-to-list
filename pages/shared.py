"""Shared pieces for Samay's pages: fixed-viewport styling, the sidebar, the uploaded docket, the plans."""
from datetime import date

import pandas as pd
import streamlit as st

from core import priority as PRIO
from core import pucar_engine as E

START = date(2026, 10, 1)
DAYS = 60
LISTING = {"first": "1st listing", "second": "2nd listing", "deferred": "Deferred (3rd+)"}
OUTCOME = {"substantive": "Moved forward", "absence": "A party absent", "unready": "Not ready",
           "process": "Summons or warrant not back", "court": "Court could not reach it", "unclear": "Adjourned"}
BLUE, ORANGE = "#1E3A8A", "#B45309"

CSS = """
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,700&display=swap">
<style>
.samay-logo {font-family: 'Fraunces', 'Noto Serif', Georgia, serif; font-size: 40px; font-weight: 700; letter-spacing: -0.02em;
             color: #1E3A8A; line-height: 1; margin: 0 0 4px 0;}
.samay-sub {font-size: 13px; color: #475569; margin-bottom: 14px;}
section.stMain > div.block-container {padding-top: 1.2rem; padding-bottom: 0.5rem; height: 100vh; overflow: hidden;}
section.stMain {overflow: hidden;}
div[data-testid="stSidebarHeader"] {display: none;}
section.stSidebar div.block-container {padding-top: 1.5rem;}
h1 {margin-bottom: 0;}
div[data-testid="stMetric"] {background: #FFFFFF; border: 1px solid #CBD5E1; border-radius: 6px; padding: 8px 12px;}
div[data-testid="stMetricLabel"] p {font-size: 13px; color: #475569;}
div[data-testid="stTabs"] button p {font-size: 15px;}
</style>
"""


def page_setup():
    st.markdown(CSS, unsafe_allow_html=True)


def user():
    return st.session_state.get("user")


def sidebar():
    u = user()
    with st.sidebar:
        st.markdown('<div class="samay-logo">Samay</div><div class="samay-sub">Court scheduling</div>',
                    unsafe_allow_html=True)
        if u:
            st.markdown(f"**{u['name']}**  \n{u['role']}")
            if st.button("Sign out", width="stretch"):
                for k in ("user",):
                    st.session_state.pop(k, None)
                st.switch_page("pages/login.py")


def require(role):
    u = user()
    if not u:
        st.switch_page("pages/login.py")
    if u["role"] != role:
        st.switch_page("pages/judge.py" if u["role"] == "Judge" else "pages/court_master.py")
    return u


def docket():
    """The docket the court master uploaded, or None."""
    return st.session_state.get("docket")


@st.cache_data(show_spinner="Planning the quarter")
def plans(df: pd.DataFrame):
    data = E.with_roster(E.load(), df)
    data = E.judge_docket(data, 3000)
    out = {}
    for label, kw in {"Today's rules": dict(rtl=False), "Samay": dict(rtl=True)}.items():
        m, _ = E.simulate(data, START, days=DAYS, seed=7, **kw)
        out[label] = m
    real = data["roster"][data["roster"]["sample"]]
    scores = PRIO.score_roster(real, data["ref"], START)
    return out, real, data, scores


def day_list(journey, day, order):
    return (journey[journey.date == day].assign(_o=lambda d: d.block.map(order))
            .sort_values(["_o", "start"]).reset_index(drop=True))


def why(row):
    if row.hearing_type == "BAIL":
        return "Liberty lane: bail is always listed"
    if row.listing == "deferred":
        return f"Deferred case, score {row.score:.0f}: priority and a fixed slot"
    return f"Score {row.score:.0f}, ready, fits the sitting"
