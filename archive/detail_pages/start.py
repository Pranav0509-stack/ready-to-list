"""Start here: upload a judge's docket (the organisers' roster file, CSV or Excel) and follow it
through every step: check, understand, readiness, plan, results."""
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import pucar_engine as E
from pages.common import BASE_COLOR, RTL_COLOR


def link(page, label, icon):
    """A page link that stays quiet when the page runs outside app.py (tests)."""
    try:
        st.page_link(page, label=label, icon=icon)
    except Exception:
        pass

START = date(2026, 10, 1)
HORIZONS = {"A week": 5, "A month": 21, "A quarter": 60, "A year": 250}

link("pages/samay.py", label="Back to Samay", icon=":material/arrow_back:")
st.title("Plan a judge's docket")
st.caption("Upload the docket file from the hackathon repo (data/roster_sample_100.csv, or the same columns as an "
           "Excel sheet). Everything below is computed from that file and the organisers' reference tables.")


@st.cache_data
def reference():
    return E.load()


ref_data = reference()

# ------------------------------------------------------------------ 1. upload
st.markdown("### 1. Upload the docket")
c1, c2 = st.columns([2, 1])
up = c1.file_uploader("Docket file", type=["csv", "xlsx", "xls"], label_visibility="collapsed")
use_sample = c2.button("Use the organisers' 100-case roster", type="primary" if up is None else "secondary")
if up is not None:
    try:
        df = pd.read_excel(up) if up.name.lower().endswith(("xlsx", "xls")) else pd.read_csv(up)
        st.session_state.docket, st.session_state.docket_name = df, up.name
    except Exception as e:  # unreadable file: say what to do
        st.error(f"Could not read {up.name}: {e}. Save it as CSV (UTF-8) or .xlsx and upload again.")
elif use_sample or "docket" not in st.session_state:
    st.session_state.docket = ref_data["roster"].copy()
    st.session_state.docket_name = "roster_sample_100.csv (organisers' repo)"

df = st.session_state.docket
problems = E.validate_roster(df)
if problems:
    st.error("This file can't be planned yet:\n\n" + "\n".join(f"- {p}" for p in problems))
    st.stop()
st.success(f"{st.session_state.docket_name}: {len(df)} cases, {df.advocate_id.nunique()} advocates, "
           f"{df.columns.size} columns. Every required column is present.")
with st.expander("See the file"):
    st.dataframe(df, width="stretch", hide_index=True, height=260)

data = E.with_roster(ref_data, df)
cases = E.cases_frame(data, START)

# ------------------------------------------------------------------ 2. understand
st.markdown("### 2. What is in it")
k = st.columns(4)
k[0].metric("Cases", len(cases))
k[1].metric("4+ years old", int(cases.old.sum()), help="Protected: a quarter of each day goes to these")
k[2].metric("Median age", f"{cases.age_years.median():.1f} years")
k[3].metric("Advocates with 2+ cases", int((df.advocate_id.value_counts() >= 2).sum()),
            help="Their matters can be heard together: one trip instead of several")
a, b = st.columns(2)
flow = E.CFG["stage_flow"] + [p for p in E.CFG["side_purposes"]]
nxt = cases.purpose.value_counts().reindex(flow).fillna(0)
fig = go.Figure(go.Bar(x=nxt.values, y=[p.replace("_", " ").title() for p in nxt.index], orientation="h",
                       marker_color=RTL_COLOR, hovertemplate="%{y}: %{x} cases<extra></extra>"))
fig.update_layout(title="Next hearing, in the order of a case's life", height=380, margin=dict(l=0, r=0, t=40, b=0),
                  yaxis=dict(autorange="reversed", automargin=True))
a.plotly_chart(fig, width="stretch")
ages = pd.cut(cases.age_years, [0, 1, 2, 3, 4, 5, 20], labels=["<1", "1-2", "2-3", "3-4", "4-5", "5+"]).value_counts().sort_index()
fig = go.Figure(go.Bar(x=ages.index.astype(str), y=ages.values, marker_color=["#2a78d6"] * 4 + ["#eb6834"] * 2,
                       text=ages.values, textposition="outside"))
fig.update_layout(title="Age of cases, years (orange: 4+, protected)", height=380, margin=dict(l=0, r=0, t=40, b=0))
b.plotly_chart(fig, width="stretch")

# ------------------------------------------------------------------ 3. readiness
st.markdown("### 3. Is each case ready for its next hearing?")
st.caption("Read from each case's last hearing note, the way a court master would read the order sheet. "
           "New complaints get the registry's pre-filing check instead (Justice Sehgal's docket, Registry intake).")
sig = cases[["sig_process", "sig_absence", "sig_unready"]]
r = st.columns(4)
r[0].metric("Waiting on summons or warrant", int(sig.sig_process.sum()),
            help="'Await warrant', 'Issue NBW', 'Take steps', 'For return of summons'")
r[1].metric("A party was absent", int(sig.sig_absence.sum()))
r[2].metric("Not ready or time sought", int(sig.sig_unready.sum()))
r[3].metric("No risk signal", int((~sig.any(axis=1)).sum()))
state = cases.assign(
    readiness=["Waiting on process" if p else "Absence risk" if ab else "Not ready" if u else "Ready"
               for p, ab, u in zip(sig.sig_process, sig.sig_absence, sig.sig_unready)])
with st.expander("Each case's readiness"):
    st.dataframe(state[["id", "purpose", "age_years", "advocate", "readiness"]].rename(columns={
        "id": "Case", "purpose": "Next hearing", "age_years": "Age (years)", "advocate": "Advocate",
        "readiness": "Readiness"}).round(1), width="stretch", hide_index=True, height=260)

# ------------------------------------------------------------------ 4. plan
st.markdown("### 4. Plan the hearings")
c1, c2 = st.columns([2, 1])
horizon = c1.segmented_control("Horizon", list(HORIZONS), default="A quarter")
embed = c2.toggle("Inside a full 3,000-case docket", value=len(df) < 1000,
                  help="A judge hears these cases alongside the rest of the roster. The extra cases are generated "
                       "from this file with the organisers' generator, so the court's day is as full as a real one.")
days = HORIZONS[horizon or "A quarter"]


@st.cache_data(show_spinner="Planning with today's rules and with Samay")
def plan(docket: pd.DataFrame, days: int, embed: bool):
    d = E.with_roster(E.load(), docket)
    d = E.judge_docket(d, 3000) if embed else {**d, "roster": d["roster"].assign(sample=True)}
    real = set(d["roster"][d["roster"]["sample"]].case_number)
    out = {}
    for name, kw in {"Today's rules": dict(rtl=False), "Samay": dict(rtl=True)}.items():
        m, _ = E.simulate(d, START, days=days, seed=7, **kw)
        out[name] = {k: v for k, v in m.items()}
    return out, real


runs, real = plan(df, days, embed)
m = runs["Samay"]
j = m["journey"]
mine = j[j.case_number.isin(real)]
wd = m["workdays"]
first_week = [d for d in wd if d < wd[0] + timedelta(days=7)]
st.markdown(f"**Next week for your {len(real)} cases**: {len(mine[mine.date.isin(first_week)])} hearings, each with "
            "a time window, grouped by advocate and kind of hearing.")
wk = mine[mine.date.isin(first_week)].sort_values(["date", "block", "start"])
wk = wk.assign(Date=wk.date.map(lambda x: x.strftime("%a %d %b")))
st.dataframe(wk[["Date", "block", "start", "case_number", "hearing_type"]].rename(columns={
    "block": "Sitting", "start": "Expected start", "case_number": "Case", "hearing_type": "Hearing"}),
    width="stretch", hide_index=True, height=260)

# ------------------------------------------------------------------ 5. results
st.markdown(f"### 5. What changes over {horizon.lower() if horizon else 'a quarter'}")
base, rtl = runs["Today's rules"], runs["Samay"]


def disposed(run):
    cs = run["cases"]
    return int(cs[cs.case_number.isin(real)].disposed.sum())


cols = st.columns(5)
spec = [("Reach", "reach_rate_pct", "%"), ("Hearings that moved a case", "substantiveness_pct", "%"),
        ("Substantive hearings a day", "substantive_per_day", ""), ("Days from listing to a real hearing",
                                                                     "predictability_days", "")]
for col, (label, key, unit) in zip(cols, spec):
    lower = key == "predictability_days"
    col.metric(label, f"{rtl[key]:.0f}{unit}" if unit else f"{rtl[key]:.1f}",
               f"{rtl[key] - base[key]:+.1f} vs today", delta_color="inverse" if lower else "normal")
cols[4].metric(f"Of your {len(real)} cases disposed", disposed(rtl), f"{disposed(rtl) - disposed(base):+d} vs today")

fig = go.Figure()
for name, colr in [("Today's rules", BASE_COLOR), ("Samay", RTL_COLOR)]:
    run = runs[name]
    jj = run["journey"]
    daily = jj[jj.case_number.isin(real)].groupby("date").outcome.apply(lambda s: (s == "substantive").sum())
    daily = daily.reindex(run["workdays"], fill_value=0).cumsum()
    fig.add_scatter(x=list(daily.index), y=daily.values, name=name, line=dict(color=colr, width=2))
fig.update_layout(title="Hearings that moved your cases forward, cumulative", height=320,
                  margin=dict(l=0, r=0, t=40, b=0), legend=dict(orientation="h", y=-0.2), hovermode="x unified")
st.plotly_chart(fig, width="stretch")
st.caption("One simulated run. Hearings succeed or fail at the organisers' real rates for each hearing type and for the "
           "real reasons. The scored outputs in the submission average several runs.")
