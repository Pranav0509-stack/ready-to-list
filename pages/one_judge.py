"""One judge, the organisers' 100 real cases inside a 3,000-case docket, planned by day,
week, month and year. The same engine runs any court: a docket per judge, a config per court."""
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import pucar_engine as E
from core.config import ROOT
from core.registry import RULES, scrutinise, timeline
from pages.common import BASE_COLOR, RTL_COLOR, sidebar

sidebar()
st.title("Justice Sehgal's docket")
st.caption("One judge. The organisers' 100 real cheque-dishonour cases (NI Act s.138) sit inside a 3,000-case docket "
           "built with their generator, with new complaints arriving every day. Court day 10:30-12:30 and 13:30-17:00. "
           "The design is per judge and per court: another bench is another docket and another config.")

START = date(2026, 10, 1)
ARMS = {"Today's rules": dict(rtl=False),
        "Scheduling only": dict(rtl=True, levers=["optimiser", "smart_next_date", "fixed_slot_cluster"]),
        "Scheduling + pre-filing": dict(rtl=True, levers=["prefiling", "optimiser", "smart_next_date", "fixed_slot_cluster"]),
        "Ready-to-List (all levers)": dict(rtl=True)}


@st.cache_data(show_spinner="Simulating a year of this court under each approach (about a minute)")
def year_runs(days=250):
    data = E.judge_docket(E.load(), 3000)
    out = {}
    for name, kw in ARMS.items():
        m, _ = E.simulate(data, START, days=days, seed=7, **kw)
        out[name] = {k: v for k, v in m.items()}
    real = data["roster"][data["roster"]["sample"]]
    return out, real


@st.cache_data
def load_data():
    return E.load()


data = load_data()
ref = data["ref"]
roster = data["roster"]
tabs = st.tabs(["The data", "Registry intake", "Lifecycle", "Case file", "Plan", "Results"])

# ------------------------------------------------------------------ the data
with tabs[0]:
    files = {"roster_sample_100.csv": "The docket: one row per case with filing date, advocate, stage, the last "
                                      "hearing's note, the next purpose, and hearings held per stage",
             "hearing_type_reference.csv": "Per hearing type: hearings per case, estimated minutes, days to the next hearing",
             "substantiveness_by_hearing_type.csv": "Per hearing type: the real probability a hearing moves the case",
             "hearing_failure_reasons.csv": "Per hearing type: why hearings did not move the case, by reason",
             "court_calendar.csv": "Working days, weekly offs and holidays, September to December 2026",
             "sample_causelist_2026-09-22.csv": "One real day's cause list: 90 matters"}
    f = st.selectbox("File", list(files))
    st.caption(files[f])
    st.dataframe(pd.read_csv(data["dir"] / f), width="stretch", hide_index=True, height=300)
    prof = ROOT / "docs" / "DATA_PROFILE.md"
    if prof.exists():
        text = prof.read_text()
        key = text.split("## Key findings")[-1] if "## Key findings" in text else ""
        if key:
            st.markdown("**Key findings from every file**" + key)
        with st.expander("The full profile of every file and column"):
            st.markdown(text)

# ------------------------------------------------------------------ registry intake
with tabs[1]:
    st.markdown("**A new cheque-dishonour complaint, scrutinised at e-filing.** The registry's manual checks become "
                "structured fields and computed dates. Nothing is refused: the result says what to cure, and a late "
                "complaint's condonation is heard at admission instead of in a separate track.")
    c1, c2, c3 = st.columns(3)
    cheque = c1.date_input("Cheque date", date(2026, 5, 2), key="r_cheque")
    presented = c1.date_input("Presented to the bank", date(2026, 7, 20), key="r_pres")
    memo = c2.date_input("Return memo received", date(2026, 7, 24), key="r_memo")
    sent = c2.date_input("Demand notice sent", date(2026, 8, 10), key="r_sent")
    received = c3.date_input("Notice received by the drawer", date(2026, 8, 14), key="r_recv")
    filed = c3.date_input("Complaint filed", date(2026, 9, 30), key="r_filed")
    tl = timeline(cheque, presented, memo, sent, received, filed)
    st.dataframe(pd.DataFrame([{"Step": r["step"], "Date": r["date"], "Deadline": r["deadline"],
                                "In time": "Yes" if r["in_time"] else "No", "Rule": r["rule"]} for r in tl["rows"]]),
                 width="stretch", hide_index=True)
    st.caption(f"Cause of action {tl['cause_of_action']:%d %b %Y}; limitation ended {tl['limitation_ends']:%d %b %Y}.")
    d1, d2 = st.columns(2)
    docs = {d["id"]: d1.checkbox(f"{d['label']} ({d['law']})", value=d["id"] not in ("s141_averments", "vakalat"),
                                 key=f"doc_{d['id']}") for d in RULES["documents"]}
    summons = {s["id"]: d2.checkbox(s["label"], value=s["id"] == "address", key=f"sum_{s['id']}")
               for s in RULES["summons_details"]}
    company = d2.toggle("The drawer is a company", key="r_company")
    outside = d2.toggle("The accused lives outside this court's area", value=True, key="r_outside")
    res = scrutinise(tl, docs, summons, company, outside)
    colour = "green" if res["ready"] else "orange"
    st.markdown(f"### :{colour}[{res['status']}]")
    for n in res["notes"]:
        st.markdown(f"- {n}")
    if res["missing"] or res["summons_missing"]:
        st.markdown("**To cure:** " + "; ".join(res["missing"] + res["summons_missing"]))
    with st.expander("What each check prevents, from the data"):
        st.dataframe(pd.DataFrame(RULES["evidence"]), width="stretch", hide_index=True)

# ------------------------------------------------------------------ lifecycle
with tabs[2]:
    st.markdown("**The life of a cheque-dishonour complaint, as the data shows it.** Each box: cases of the 100 now "
                "waiting for that hearing, the share of such hearings that move the case, minutes, and hearings a "
                "case usually needs at that stage.")
    now = roster.purpose_of_next_hearing.map(E.norm).value_counts()
    flow, side = E.CFG["stage_flow"], E.CFG["side_return"]

    def node(t):
        r = ref.loc[t]
        return (f'{t} [label="{t.replace("_", " ").title()}\\n{int(now.get(t, 0))} cases now | '
                f'{r.p_sub:.0%} move | {int(r.minutes)} min\\n{r.mean_hearings:.1f} hearings per case"];')
    dot = ["digraph G {", "rankdir=LR; node [shape=box, style=rounded, fontname=Helvetica, fontsize=10];"]
    dot += [node(t) for t in ref.index]
    dot += [f"{a} -> {b};" for a, b in zip(flow, flow[1:])]
    dot += ['ADMISSION -> DELAY_CONDONATION_HEARING [style=dashed, label="late complaint"];',
            'DELAY_CONDONATION_HEARING -> COGNIZANCE;',
            'APPEARANCE -> WARRANT [style=dashed, label="not served / absent"];', 'WARRANT -> PLEA;',
            'APPEARANCE -> BAIL [style=dashed];', 'BAIL -> PLEA;',
            'EVIDENCE_COMPLAINANT -> REPORTS [style=dashed, label="mediation"];',
            'EVIDENCE_ACCUSED -> APPLICATION_REVIEW [style=dashed];', "}"]
    st.graphviz_chart("\n".join(dot), width="stretch")
    tbl = ref[["minutes", "gap_days", "mean_hearings", "p_sub"]].copy()
    for g in E.CFG["reason_groups"]:
        tbl[f"fails: {g}"] = (1 - ref.p_sub) * ref[f"share_{g}"]
    tbl["cases now (of 100)"] = [int(now.get(t, 0)) for t in tbl.index]
    st.dataframe(tbl.rename(columns={"minutes": "Minutes", "gap_days": "Days to next", "mean_hearings":
                                     "Hearings per case", "p_sub": "Moves the case"}).style.format(precision=2),
                 width="stretch")

# ------------------------------------------------------------------ simulated year
runs, real = year_runs()

# ------------------------------------------------------------------ case file
with tabs[3]:
    cid = st.selectbox("Case (the organisers' 100)", real.case_number.tolist())
    row = roster[roster.case_number == cid].iloc[0]
    age = (pd.Timestamp(START) - pd.Timestamp(row.filing_date)).days / 365.25
    a, b = st.columns([1, 1])
    a.markdown(f"**{cid}**, filed {row.filing_date} ({age:.1f} years), advocate {row.advocate_id}  \n"
               f"Now at **{row.current_stage}**, next: **{row.purpose_of_next_hearing}**, "
               f"{row.total_hearings_held} hearings held so far")
    a.code(row.last_hearing_summary, language=None)
    sig = E.signals(row.last_hearing_summary)
    a.caption("Read from the note: " + ", ".join(k.replace("sig_", "") + " risk" for k, v in sig.items() if v) or "no risk signal")
    held = row[[c for c in roster.columns if c.startswith("hearings_") and c != "hearings_held"]]
    held.index = [i.replace("hearings_", "").replace("_", " ") for i in held.index]
    fig = go.Figure(go.Bar(x=held.values, y=held.index, orientation="h", marker_color="#2a78d6"))
    fig.update_layout(height=360, margin=dict(l=0, r=0, t=10, b=0), xaxis_title="Hearings held at each stage")
    b.plotly_chart(fig, width="stretch")
    st.markdown("**Its next year, simulated**")
    cols = st.columns(2)
    for col, arm in zip(cols, ["Today's rules", "Ready-to-List (all levers)"]):
        j = runs[arm]["journey"]
        j = j[j.case_number == cid]
        col.markdown(f"*{arm}*: {len(j)} listings, {(j.outcome == 'substantive').sum()} moved the case")
        col.dataframe(j[["date", "start", "hearing_type", "outcome"]], width="stretch", hide_index=True, height=240)

# ------------------------------------------------------------------ plan
with tabs[4]:
    c1, c2 = st.columns([1, 2])
    arm = c1.selectbox("Approach", list(ARMS), index=3)
    horizon = c2.segmented_control("Horizon", ["Day", "Week", "Month", "Year"], default="Week")
    m = runs[arm]
    j, wd = m["journey"], m["workdays"]
    j = j.assign(real=j.case_number.isin(set(real.case_number)))
    if horizon == "Day":
        d = st.selectbox("Date", wd[:40], format_func=lambda x: x.strftime("%a %d %b %Y"))
        day = j[j.date == d].sort_values(["block", "start"])
        st.caption(f"{len(day)} matters, {(day.outcome == 'substantive').sum()} moved the case "
                   f"({day.real.sum()} of the organisers' 100).")
        st.dataframe(day[["block", "start", "case_number", "hearing_type", "outcome", "from_waitlist", "real"]],
                     width="stretch", hide_index=True)
    elif horizon == "Week":
        w0 = st.selectbox("Week starting", [d for d in wd[:60] if d.weekday() == 0] or wd[:1],
                          format_func=lambda x: x.strftime("%d %b %Y"))
        wk = j[(j.date >= w0) & (j.date < w0 + timedelta(days=7))]
        grid = pd.crosstab(wk.hearing_type, wk.date.map(lambda x: x.strftime("%a %d %b")))
        st.dataframe(grid, width="stretch")
        st.caption(f"{len(wk)} listings this week, {(wk.outcome == 'substantive').sum()} moved the case.")
    elif horizon == "Month":
        dfm = j.groupby("date").agg(listed=("case_number", "size"),
                                    moved=("outcome", lambda s: (s == "substantive").sum())).reset_index()
        dfm["month"] = dfm.date.map(lambda x: x.strftime("%b %Y"))
        mon = st.selectbox("Month", dfm.month.unique().tolist())
        mm = dfm[dfm.month == mon]
        fig = go.Figure()
        fig.add_bar(x=mm.date, y=mm.listed, name="Listed", marker_color="#c9c7c0")
        fig.add_bar(x=mm.date, y=mm.moved, name="Moved the case", marker_color=RTL_COLOR)
        fig.update_layout(barmode="overlay", height=320, margin=dict(l=0, r=0, t=10, b=0),
                          legend=dict(orientation="h", y=-0.2))
        st.plotly_chart(fig, width="stretch")
    else:
        fig = go.Figure()
        for name, colr in [("Today's rules", BASE_COLOR), ("Scheduling only", "#9a9893"),
                           ("Ready-to-List (all levers)", RTL_COLOR)]:
            cs = runs[name]["cases"]
            disp = cs[cs.disposed].disposed_day.value_counts().sort_index().cumsum()
            fig.add_scatter(x=[runs[name]["workdays"][i] for i in disp.index], y=disp.values, name=name,
                            line=dict(color=colr, width=2))
        fig.update_layout(title="Cases disposed, cumulative", height=340, margin=dict(l=0, r=0, t=40, b=0),
                          legend=dict(orientation="h", y=-0.2), hovermode="x unified")
        st.plotly_chart(fig, width="stretch")
        stage = pd.DataFrame({name: runs[name]["cases"].query("not disposed").purpose_now.value_counts()
                              for name in ["Today's rules", "Ready-to-List (all levers)"]}).fillna(0).astype(int)
        st.markdown("**Where the pending cases stand after a year**")
        st.dataframe(stage, width="stretch")

# ------------------------------------------------------------------ results
with tabs[5]:
    rows = []
    for name, m in runs.items():
        cs = m["cases"]
        nw = cs[cs.new_filing]
        rows.append({"Approach": name, "Disposed in a year": int(cs.disposed.sum()),
                     "Of the 100 real cases": int(cs[cs.case_number.isin(set(real.case_number))].disposed.sum()),
                     "Substantive a day": round(m["substantive_per_day"], 1),
                     "Moves the case %": round(m["substantiveness_pct"]),
                     "Reach %": round(m["reach_rate_pct"]),
                     "New complaints past cognizance": f"{(~nw.purpose_now.isin(['ADMISSION', 'DELAY_CONDONATION_HEARING', 'COGNIZANCE'])).sum()} of {len(nw)}",
                     "Delay condonation listings": int((m["journey"].hearing_type == "DELAY_CONDONATION_HEARING").sum()),
                     "Wasted listings": int(m["wasted_listings"])})
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    st.markdown("- **Scheduling alone** (the registry's own list-building) fits the list to the day and sets real next "
                "dates: more disposals and far fewer wasted listings.\n"
                "- **Pre-filing** changes what happens to new complaints: with limitation computed at filing, almost "
                "all get past cognizance within the year instead of stalling in delay condonation.\n"
                "- **Readiness levers** (process tracking, confirmation, the last note) take disposals furthest.")
    st.caption("One random seed in the app; the submission's outputs average several seeds.")
