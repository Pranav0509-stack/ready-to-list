"""Shared pieces for Samay's pages: styling, the sidebar, the judges and their dockets, the plans."""
from datetime import date

import pandas as pd
import streamlit as st

from core import priority as PRIO
from core import pucar_engine as E

START = date(2026, 10, 1)
DAYS = 60
JUDGES = ["Justice Sehgal", "Justice Dimakar", "Justice Joshi"]
COURT_OF = {"Justice Sehgal": "Court 12", "Justice Dimakar": "Court 7", "Justice Joshi": "Court 3"}
LISTING = {"first": "1st listing", "second": "2nd listing", "deferred": "Deferred (3rd+)"}
OUTCOME = {"substantive": "Moved forward", "absence": "A party absent", "unready": "Not ready",
           "process": "Summons or warrant not back", "court": "Court could not reach it", "unclear": "Adjourned"}
BLUE, ORANGE, INK, MUTED = "#1E3A8A", "#B45309", "#0F172A", "#64748B"
LISTING_COLOR = {"first": "#1E3A8A", "second": "#3B82F6", "deferred": "#B45309"}

CSS = """
<style>
section.stMain > div.block-container {padding-top: 1.1rem; padding-bottom: 0.4rem; height: 100vh; overflow: hidden;}
section.stMain {overflow: hidden;}
section.stSidebar div.block-container {padding-top: 1rem;}
h1 {margin-bottom: 0; font-size: 2rem;}
div[data-testid="stMetric"] {background: #FFFFFF; border: 1px solid #CBD5E1; border-radius: 6px; padding: 8px 12px;}
div[data-testid="stMetricLabel"] p {font-size: 12.5px; color: #475569;}
div[data-testid="stMetricValue"] {font-size: 1.6rem;}
div[data-testid="stTabs"] button p {font-size: 15px;}
.samay-logo {font-family: 'Times New Roman', Times, serif; font-size: 42px; font-weight: 700; color: #B45309;
             line-height: 1; letter-spacing: -0.01em; margin: 0;}
.samay-sub {font-size: 12.5px; color: #475569; margin: 4px 0 10px 0;}
.who {font-size: 15px; color: #0F172A; margin-bottom: 2px;}
.role {font-size: 12.5px; color: #64748B; margin-bottom: 10px;}
</style>
"""


def page_setup():
    st.markdown(CSS, unsafe_allow_html=True)


def logo(size=42):
    return (f"<div class='samay-logo' style='font-family:\"Times New Roman\", Times, serif; color:#B45309; "
            f"font-size:{size}px; font-weight:700; line-height:1'>Samay</div>")


def user():
    return st.session_state.get("user")


def sidebar():
    u = user()
    with st.sidebar:
        st.markdown(logo() + "<div class='samay-sub'>Court scheduling</div>", unsafe_allow_html=True)
        if u:
            st.markdown(f"<div class='who'><b>{u['name']}</b></div><div class='role'>{u['role']}</div>",
                        unsafe_allow_html=True)
            if st.button("Sign out", width="stretch"):
                st.session_state.pop("user", None)
                st.switch_page("pages/login.py")


def require(role):
    u = user()
    if not u:
        st.switch_page("pages/login.py")
    if u["role"] != role:
        st.switch_page("pages/judge.py" if u["role"] == "Judge" else "pages/court_master.py")
    return u


def dockets() -> dict:
    return st.session_state.setdefault("dockets", {})


def docket(judge):
    return dockets().get(judge)


@st.cache_data(show_spinner="Planning the quarter")
def plans(df: pd.DataFrame, judge: str):
    data = E.with_roster(E.load(), df)
    data = E.judge_docket(data, 3000)
    out = {}
    for label, kw in {"Today's rules": dict(rtl=False), "Samay": dict(rtl=True)}.items():
        m, _ = E.simulate(data, START, days=DAYS, seed=7 + JUDGES.index(judge) if judge in JUDGES else 7, **kw)
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


def hearing_label(t):
    return str(t).replace("_", " ").title().replace("S351 Bnss", "s.351 BNSS")
