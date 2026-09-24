"""Shared pieces for Samay's pages: styling, the sidebar, the judges and their dockets, the plans."""
import io
import json
import shutil
import tempfile
import zipfile
from datetime import date
from pathlib import Path

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
BLUE, ORANGE, INK, MUTED = "#1B2A41", "#C08A2D", "#111925", "#56627A"
LISTING_COLOR = {"first": "#1B2A41", "second": "#3D6FD9", "deferred": "#C08A2D"}
REFERENCE_FILES = ["hearing_type_reference.csv", "substantiveness_by_hearing_type.csv", "hearing_failure_reasons.csv",
                   "court_calendar.csv", "sample_causelist_2026-09-22.csv"]

CSS = """
<style>
section.stMain > div.block-container {padding-top: 1rem; padding-bottom: 0.4rem; height: 100vh; overflow: hidden;}
section.stMain {overflow: hidden;}
section.stSidebar div.block-container {padding-top: 0.6rem;}
h1 {margin-bottom: 0; font-size: 2rem; letter-spacing: -0.01em;}
div[data-testid="stMetric"] {background: #FFFFFF; border: 1px solid #C9D3E3; border-radius: 8px; padding: 10px 14px;}
div[data-testid="stMetricLabel"] p {font-size: 12.5px; color: #56627A; letter-spacing: 0.01em;}
div[data-testid="stMetricValue"] {font-size: 1.7rem; font-family: 'Spectral', Georgia, serif; font-weight: 600;}
div[data-testid="stTabs"] button p {font-size: 15px; font-weight: 500;}
div[data-testid="stTabs"] button[aria-selected="true"] p {color: #9A6212;}
div[data-testid="stTabs"] div[data-baseweb="tab-highlight"] {background-color: #9A6212;}
.samay-logo {font-family: 'Spectral', Georgia, serif; font-size: 40px; font-weight: 600; color: #9A6212;
             line-height: 1; letter-spacing: -0.01em; margin: 0;}
.samay-sub {font-size: 12.5px; color: #56627A; margin: 4px 0 12px 0;}
.who {font-size: 15px; color: #111925; margin-bottom: 0;}
.role {font-size: 12.5px; color: #56627A; margin-bottom: 10px;}
.eyebrow {font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; color: #56627A; font-weight: 600; margin-bottom: 2px;}
.sub {color: #56627A; font-size: 14px; margin-top: -4px;}
.list {height: 400px; overflow-y: auto; border: 1px solid #C9D3E3; border-radius: 8px; background: #FFFFFF;}
.list table {width: 100%; border-collapse: collapse; font-size: 14px;}
.list th {position: sticky; top: 0; background: #ECEEEA; color: #56627A; font-weight: 600; font-size: 12px;
          letter-spacing: 0.04em; text-transform: uppercase; text-align: left; padding: 9px 12px; border-bottom: 1px solid #C9D3E3;}
.list td {padding: 9px 12px; border-bottom: 1px solid #E5E9EF; vertical-align: top; color: #111925;}
.list tr:hover td {background: #F5F6F3;}
.list .time {font-family: 'Spectral', Georgia, serif; font-size: 16px; font-weight: 600; white-space: nowrap;}
.list .case {font-weight: 600; white-space: nowrap;}
.list .why {color: #56627A; font-size: 13px;}
.list .sitting td {background: #F5F6F3; color: #9A6212; font-weight: 600; font-size: 12px; letter-spacing: 0.06em; text-transform: uppercase;}
.chip {display: inline-block; font-size: 11.5px; font-weight: 600; padding: 2px 8px; border-radius: 999px; white-space: nowrap;}
.chip.first {background: #E5E9EF; color: #1B2A41;} .chip.second {background: #DBEAFE; color: #1D4ED8;}
.chip.deferred {background: #F7ECD9; color: #9A6212;} .chip.bail {background: #E1F0E8; color: #2F7454;}
.chip.score {background: #ECEEEA; color: #111925;}
.chip.done {background: #E1F0E8; color: #2F7454; margin-left: 4px;}
.list tr.locked td {background: #F3F8F5; color: #56627A;}
section.stSidebar div[role="radiogroup"] {gap: 2px; margin: 6px 0 14px 0;}
section.stSidebar div[role="radiogroup"] {flex-direction: column; width: 100%;}
section.stSidebar div[role="radiogroup"] label {padding: 8px 12px; border-radius: 8px; width: 100%; max-width: 100%; cursor: pointer; margin: 0;}
section.stSidebar div[role="radiogroup"] label > div > div:first-child:not([data-testid]) {display: none;}
section.stSidebar div[role="radiogroup"] label p {font-size: 15px; font-weight: 500; color: #1B2A41;}
section.stSidebar div[role="radiogroup"] label:hover {background: #E5E9EF;}
section.stSidebar div[role="radiogroup"] label[data-selected="true"] {background: #1B2A41;}
section.stSidebar div[role="radiogroup"] label[data-selected="true"] p {color: #FFFFFF;}
.factor {border: 1px solid #C9D3E3; border-radius: 8px; background: #FFFFFF; padding: 10px 12px; margin-bottom: 8px;}
.factor .top {display: flex; justify-content: space-between; align-items: baseline;}
.factor .name {font-weight: 600; font-size: 14px;}
.factor .pts {font-family: 'Spectral', Georgia, serif; font-size: 20px; font-weight: 600;}
.factor .bar {height: 5px; background: #E5E9EF; border-radius: 3px; margin: 6px 0;}
.factor .bar div {height: 5px; background: #C08A2D; border-radius: 3px;}
.factor .ev {font-size: 13px; color: #56627A; line-height: 1.45;}
.card {background: #FFFFFF; border: 1px solid #C9D3E3; border-radius: 10px; padding: 14px 16px; height: 100%;}
.card h3 {font-family: 'Spectral', Georgia, serif; font-size: 20px; margin: 0 0 2px 0; font-weight: 600;}
.card .muted {color: #56627A; font-size: 13px; margin-bottom: 10px;}
.card .kv {display: grid; grid-template-columns: 1fr 1fr; gap: 8px 12px;}
.card .k {font-size: 12px; color: #56627A;} .card .v {font-family: 'Spectral', Georgia, serif; font-size: 22px; font-weight: 600;}
.card .empty {color: #94A3B8; font-size: 14px; padding: 18px 0;}
.cal-head {font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; color: #56627A; text-align: center; font-weight: 600;}
div[data-testid="stColumn"] button[kind="secondary"] {padding: 4px 6px; min-height: 44px;}
div[data-testid="stColumn"] button[kind="secondary"] p {font-size: 12px; line-height: 1.25; text-align: left;}
.daypill {display:inline-block; font-size: 11px; padding: 1px 7px; border-radius: 999px; margin-right: 6px;}
.daypill.leave {background:#F6E3E6; color:#A8253B;} .daypill.holiday {background:#F7ECD9; color:#9A6212;}
.daypill.sit {background:#E1F0E8; color:#2F7454;} .daypill.off {background:#E5E9EF; color:#56627A;}
</style>
"""


def page_setup():
    st.markdown(CSS, unsafe_allow_html=True)


def logo(size=40):
    return (f"<div class='samay-logo' style='font-size:{size}px'>Samay</div>")


def user():
    return st.session_state.get("user")


def sidebar(nav=None):
    """Mark, who is signed in, the page menu (when given), sign out. Returns the chosen page."""
    u = user()
    choice = None
    with st.sidebar:
        st.markdown(logo() + "<div class='samay-sub'>Court scheduling</div>", unsafe_allow_html=True)
        if u:
            st.markdown(f"<div class='who'><b>{u['name']}</b></div><div class='role'>{u['role']}</div>",
                        unsafe_allow_html=True)
            if nav:
                st.markdown("<div class='nav-wrap'>", unsafe_allow_html=True)
                choice = st.radio("Go to", nav, key="nav", label_visibility="collapsed")
                st.markdown("</div>", unsafe_allow_html=True)
            if st.button("Sign out", width="stretch"):
                st.session_state.pop("user", None)
                st.switch_page("pages/login.py")
    return choice


def require(role):
    u = user()
    if not u:
        st.switch_page("pages/login.py")
    if u["role"] != role:
        st.switch_page("pages/judge.py" if u["role"] == "Judge" else "pages/court_master.py")
    return u


# ---------------------------------------------------------------- dockets and reference files

def dockets() -> dict:
    """Dockets by judge. Justice Sehgal's is pre-loaded from the hackathon repository's roster."""
    d = st.session_state.get("dockets")
    if d is None:
        roster = E.default_data_dir() / "roster_sample_100.csv"
        d = {"Justice Sehgal": pd.read_csv(roster)}
        st.session_state.dockets = d
        st.session_state["docket_name_Justice Sehgal"] = "roster_sample_100.csv (hackathon repository)"
    return d


def docket(judge):
    return dockets().get(judge)


def reference_dir() -> str:
    """Folder the engine reads its reference tables from: the defaults, with any uploaded replacements."""
    if "ref_dir" not in st.session_state:
        tmp = Path(tempfile.mkdtemp(prefix="samay_ref_"))
        for f in REFERENCE_FILES + ["roster_sample_100.csv"]:
            src = E.default_data_dir() / f
            if src.exists():
                shutil.copy(src, tmp / f)
        st.session_state.ref_dir = str(tmp)
    return st.session_state.ref_dir


def read_any(name: str, raw: bytes) -> list:
    """Read an uploaded file of any common type into (filename, DataFrame) pairs. ZIPs and folders
    expand to their files."""
    low = name.lower()
    out = []
    if low.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            for info in z.infolist():
                if info.is_dir() or "__MACOSX" in info.filename:
                    continue
                out += read_any(Path(info.filename).name, z.read(info))
        return out
    try:
        if low.endswith((".xlsx", ".xls")):
            for sheet, df in pd.read_excel(io.BytesIO(raw), sheet_name=None).items():
                out.append((f"{name}:{sheet}", df))
        elif low.endswith(".json"):
            data = json.loads(raw.decode("utf-8"))
            out.append((name, pd.DataFrame(data if isinstance(data, list) else data.get("cases", data))))
        elif low.endswith((".csv", ".txt", ".tsv")):
            sep = "\t" if low.endswith(".tsv") else ","
            out.append((name, pd.read_csv(io.BytesIO(raw), sep=sep)))
    except Exception as e:
        out.append((name, e))
    return out


def classify(name: str, df: pd.DataFrame) -> str:
    """What an uploaded table is: a docket, one of the reference tables, or unknown."""
    cols = set(df.columns)
    if set(E.REQUIRED_COLUMNS) <= cols:
        return "docket"
    base = Path(name.split(":")[0]).name.lower()
    for ref in REFERENCE_FILES:
        if ref.replace(".csv", "") in base:
            return ref
    if {"Hearing Purpose", "Time it takes for hearing (mins) - estimated"} <= cols:
        return "hearing_type_reference.csv"
    if {"hearingType", "Substantive Hearings (percentage probability)"} <= cols:
        return "substantiveness_by_hearing_type.csv"
    if {"hearingType", "total_no"} <= cols:
        return "hearing_failure_reasons.csv"
    if {"date", "is_working_day"} <= cols:
        return "court_calendar.csv"
    return "unknown"


@st.cache_data(show_spinner="Planning the quarter")
def plans(df: pd.DataFrame, judge: str, ref_dir: str):
    data = E.with_roster(E.load(ref_dir), df)
    data = E.judge_docket(data, 3000)
    out = {}
    seed = 7 + (JUDGES.index(judge) if judge in JUDGES else 0)
    for label, kw in {"Today's rules": dict(rtl=False), "Samay": dict(rtl=True)}.items():
        m, _ = E.simulate(data, START, days=DAYS, seed=seed, **kw)
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
        return "Deferred case: held until cured, now with priority and a fixed slot"
    if row.listing == "second":
        return "Second listing: readiness checked, priority raised"
    return "Ready, fits the sitting"


def hearing_label(t):
    return str(t).replace("_", " ").title().replace("S351 Bnss", "s.351 BNSS")


def chip(listing, hearing_type=None):
    if hearing_type == "BAIL":
        return "<span class='chip bail'>Bail</span>"
    return f"<span class='chip {listing}'>{LISTING[listing]}</span>"


def hearing_table(rows: pd.DataFrame, advocate_of: dict, height=400, show_why=True, show_outcome=None, done=None):
    """The cause list as a styled table with sitting headers and chips. `show_outcome` maps case -> text."""
    html = [f"<div class='list' style='height:{height}px'><table><thead><tr><th>Time</th><th>Case</th><th>Hearing</th>"
            f"<th>Listing</th><th>Score</th><th>Advocate</th>" + ("<th>Why today</th>" if show_why else "")
            + ("<th>Outcome</th><th>Next date</th>" if show_outcome is not None else "") + "</tr></thead><tbody>"]
    ncol = 6 + int(show_why) + (2 if show_outcome is not None else 0)
    for block, g in rows.groupby("block", sort=False):
        blk = next(b for b in E.DAY["blocks"] if b["name"] == block)
        html.append(f"<tr class='sitting'><td colspan='{ncol}'>{block} sitting, {blk['start']} to {blk['end']}, "
                    f"{len(g)} matters</td></tr>")
        for r in g.itertuples():
            cells = [f"<td class='time'>{r.start}</td>", f"<td class='case'>{r.case_number}"
                     + (f" <span class='chip done'>{done[r.case_number]}</span>" if done and r.case_number in done else "")
                     + "</td>",
                     f"<td>{hearing_label(r.hearing_type)}</td>", f"<td>{chip(r.listing, r.hearing_type)}</td>",
                     f"<td><span class='chip score'>{r.score:.0f}</span></td>", f"<td>{advocate_of.get(r.case_number, '')}</td>"]
            if show_why:
                cells.append(f"<td class='why'>{why(r)}</td>")
            if show_outcome is not None:
                o = show_outcome.get(r.case_number, ("", ""))
                cells.append(f"<td>{o[0]}</td><td class='why'>{o[1]}</td>")
            html.append(("<tr class='locked'>" if done and r.case_number in done else "<tr>") + "".join(cells) + "</tr>")
    html.append("</tbody></table></div>")
    return "".join(html)
