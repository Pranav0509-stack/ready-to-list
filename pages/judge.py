import calendar as cal
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import priority as PRIO
from core import pucar_engine as E
from pages.shared import (BLUE, LISTING, ORANGE, OUTCOME, START, day_list, docket, page_setup, plans, require,
                          sidebar, why)

page_setup()
u = require("Judge")
sidebar()

st.markdown(f"# {u['name']}")
if docket() is None:
    st.info("The court master has not uploaded the docket yet. The screens open once it is in.")
    st.stop()

R, real, data, scores = plans(docket())
rtl, base = R["Samay"], R["Today's rules"]
real_ids = set(real.case_number)
j = rtl["journey"].assign(real=lambda d: d.case_number.isin(real_ids))
order = {b["name"]: i for i, b in enumerate(E.DAY["blocks"])}
leave = {date.fromisoformat(str(x)) for x in E.CFG["judge_leave"]["dates"]}
holidays = {date.fromisoformat(r.date): r.holiday_name for r in data["calendar"].itertuples() if r.is_holiday == "Yes"}
sitting_days = [d for d in rtl["workdays"] if d not in leave]
minutes = data["ref"].minutes
advocate_of = dict(zip(data["roster"].case_number, data["roster"].advocate_id))
sc = scores.set_index("case_number")

tabs = st.tabs(["Today's list", "Docket", "Priority", "Calendar", "Results", "How Samay decides"])

# ---------------------------------------------------------------- today's list
with tabs[0]:
    left, right = st.columns([1.7, 1])
    with right:
        day = st.selectbox("Court date", sitting_days, format_func=lambda x: x.strftime("%A %d %B %Y"))
        todays = day_list(j, day, order)
        removed = st.session_state.setdefault("removed", set())
        shown = todays[~todays.case_number.isin(removed)]
        planned = float(shown.hearing_type.map(minutes).sum())
        m1, m2 = st.columns(2)
        m1.metric("Listed", len(shown))
        m2.metric("Minutes", f"{planned:.0f} / 330")
        m3, m4 = st.columns(2)
        m3.metric("Your 100 cases", int(shown.real.sum()))
        m4.metric("Deferred, fixed slot", int((shown.listing == "deferred").sum()))
        drop = st.selectbox("Remove a case", ["None"] + shown.case_number.tolist())
        b1, b2 = st.columns(2)
        if drop != "None" and b1.button("Remove", width="stretch"):
            removed.add(drop)
            st.rerun()
        if removed and b2.button("Restore all", width="stretch"):
            st.session_state.removed = set()
            st.rerun()
        approved = st.session_state.get("approved") == day
        if st.button("Approve today's list", type="primary", width="stretch", disabled=approved):
            st.session_state.approved = day
            st.rerun()
        if approved:
            st.success(f"Approved for {day:%d %B}. Time windows go to advocates and parties.")
    with left:
        view = pd.DataFrame({
            "Sitting": shown.block, "Time": shown.start, "Case": shown.case_number,
            "Hearing": shown.hearing_type.str.replace("_", " ").str.title(),
            "Listing": shown.listing.map(LISTING), "Score": shown.score,
            "Advocate": shown.case_number.map(advocate_of), "Why today": [why(r) for r in shown.itertuples()]})
        st.dataframe(view, width="stretch", hide_index=True, height=600)

# ---------------------------------------------------------------- docket
with tabs[1]:
    cs = rtl["cases"].set_index("case_number")
    rows = []
    for cid in real.case_number:
        k, s_ = cs.loc[cid], sc.loc[cid]
        nxt = j[j.case_number == cid]
        rows.append({"Case": cid, "Score": s_.score, "Status": s_.status, "Flags": s_.flag_list,
                     "Next hearing": str(real.set_index("case_number").loc[cid].purpose_of_next_hearing),
                     "Age (years)": s_.age_years, "Advocate": advocate_of[cid],
                     "Listings": int(len(nxt)), "Moved forward": int((nxt.outcome == "substantive").sum()),
                     "Now": "Disposed" if k.disposed else str(k.purpose_now).replace("_", " ").title()})
    dk = pd.DataFrame(rows).sort_values("Score", ascending=False)
    left, right = st.columns([1.7, 1])
    with right:
        status = st.multiselect("Status", ["Eligible", "Conditional"], default=["Eligible", "Conditional"])
        old_only = st.toggle("Only cases over 4 years")
        view = dk[dk.Status.isin(status)]
        if old_only:
            view = view[view["Age (years)"] >= 4]
        cid = st.selectbox("Open a case", view.Case.tolist())
        s_ = sc.loc[cid]
        st.markdown(f"**Score {s_.score}**  \nAge {s_.age_points}, readiness {s_.readiness_points}, disposal "
                    f"{s_.disposal_points}, churn {s_.churn_points}, urgency {s_.urgency_points}  \n{s_.status}. {s_.flag_list}")
        st.code(data["roster"][data["roster"].case_number == cid].iloc[0].last_hearing_summary, language=None)
        hist = j[j.case_number == cid][["date", "start", "hearing_type", "outcome"]].copy()
        hist["hearing_type"] = hist.hearing_type.str.replace("_", " ").str.title()
        hist["outcome"] = hist.outcome.map(OUTCOME)
        st.dataframe(hist.rename(columns={"date": "Date", "start": "Time", "hearing_type": "Hearing",
                                          "outcome": "Outcome"}), width="stretch", hide_index=True, height=180)
    with left:
        st.dataframe(view, width="stretch", hide_index=True, height=600)

# ---------------------------------------------------------------- priority
with tabs[2]:
    left, right = st.columns([1, 1.7])
    with left:
        st.markdown("**Weights, out of 100**")
        w = {}
        for k, v in PRIO.WEIGHTS.items():
            w[k] = st.slider(k.replace("_", " ").title(), 0, 60, v, 5, key=f"w_{k}")
        w = PRIO.rescale_weights(w)
        st.markdown("Applied: " + ", ".join(f"{k} {v:.0f}" for k, v in w.items()) + ". Age never below 20.")
        live = PRIO.score_roster(real, data["ref"], START, weights=w)
        bands = pd.cut(live.score, [0, 20, 40, 60, 80, 100],
                       labels=["0 to 20", "20 to 40", "40 to 60", "60 to 80", "80 to 100"]).value_counts().sort_index()
        fig = go.Figure(go.Bar(x=bands.index.astype(str), y=bands.values, marker_color=BLUE, text=bands.values,
                               textposition="outside"))
        fig.update_layout(height=230, margin=dict(l=0, r=0, t=10, b=0), yaxis_title="Cases")
        st.plotly_chart(fig, width="stretch")
    with right:
        st.dataframe(live.sort_values("score", ascending=False).rename(columns={
            "case_number": "Case", "score": "Score", "age_points": "Age", "readiness_points": "Readiness",
            "disposal_points": "Disposal", "churn_points": "Churn", "urgency_points": "Urgency",
            "age_years": "Years", "status": "Status", "flag_list": "Flags"}).drop(columns=["process_pending"]),
            width="stretch", hide_index=True, height=600)

# ---------------------------------------------------------------- calendar
with tabs[3]:
    used = dict(zip(rtl["daily"].date, rtl["daily"].minutes_used))
    count = j.groupby("date").size().to_dict()
    months = sorted({(d.year, d.month) for d in rtl["workdays"]})
    cols = st.columns(len(months))
    for col, (y, mth) in zip(cols, months):
        col.markdown(f"**{cal.month_name[mth]} {y}**")
        grid = []
        for week in cal.monthcalendar(y, mth):
            row = []
            for dnum in week[:5]:
                if dnum == 0:
                    row.append("")
                    continue
                d = date(y, mth, dnum)
                if d in leave:
                    row.append(f"{dnum}  leave")
                elif d in holidays:
                    row.append(f"{dnum}  holiday")
                elif d in count:
                    row.append(f"{dnum}  {count[d]} cases, {used.get(d, 0):.0f} min")
                else:
                    row.append(f"{dnum}")
            grid.append(row)
        col.dataframe(pd.DataFrame(grid, columns=["Mon", "Tue", "Wed", "Thu", "Fri"]), hide_index=True,
                      width="stretch", height=len(grid) * 35 + 40)
    daily = rtl["daily"]
    fig = go.Figure()
    fig.add_bar(x=daily.date, y=daily.minutes_used, name="Minutes used", marker_color=BLUE)
    fig.add_scatter(x=daily.date, y=[330] * len(daily), name="Sitting minutes", line=dict(color="#94A3B8", dash="dash"))
    fig.update_layout(height=250, margin=dict(l=0, r=0, t=10, b=0), legend=dict(orientation="h", y=-0.3))
    st.plotly_chart(fig, width="stretch")

# ---------------------------------------------------------------- results
with tabs[4]:
    spec = [("Listed cases the court reaches", "reach_rate_pct", "%"),
            ("Heard cases that move forward", "substantiveness_pct", "%"),
            ("Cases over 4 years heard at least once", "backlog_4y_heard_pct", "%"),
            ("Days from listing to a real hearing", "predictability_days", " days"),
            ("Average gap to the next date", "next_date_gap_days", " days"),
            ("Hearings that moved a case, per day", "substantive_per_day", ""),
            ("Wasted listings in the quarter", "wasted_listings", ""),
            ("Cases disposed in the quarter", "disposed", "")]
    fmt = lambda m, k, unit: f"{m[k]:.1f}" if k == "substantive_per_day" else f"{m[k]:.0f}{unit}"
    left, right = st.columns([1.3, 1])
    with left:
        st.dataframe(pd.DataFrame([{"Measure": l, "Today's rules": fmt(base, k, un), "Samay": fmt(rtl, k, un)}
                                   for l, k, un in spec]), width="stretch", hide_index=True, height=330)
        rows = []
        for label, m in R.items():
            jj = m["journey"]
            for key, lab in LISTING.items():
                g = jj[jj.listing == key]
                if len(g):
                    rows.append({"Approach": label, "Listing": lab, "Hearings": len(g),
                                 "Moved forward": f"{(g.outcome == 'substantive').mean():.0%}"})
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True, height=230)
    with right:
        fig = go.Figure()
        for label, colr in [("Today's rules", ORANGE), ("Samay", BLUE)]:
            m = R[label]
            jj = m["journey"]
            d = jj[jj.case_number.isin(real_ids)].groupby("date").outcome.apply(lambda s: (s == "substantive").sum())
            d = d.reindex(m["workdays"], fill_value=0).cumsum()
            fig.add_scatter(x=list(d.index), y=d.values, name=label, line=dict(color=colr, width=2))
        fig.update_layout(title="Hearings that moved your 100 cases, cumulative", height=560,
                          margin=dict(l=0, r=0, t=40, b=0), legend=dict(orientation="h", y=-0.15), hovermode="x unified")
        st.plotly_chart(fig, width="stretch")

# ---------------------------------------------------------------- how Samay decides
with tabs[5]:
    box = st.container(height=640)
    with box:
        st.markdown("""
## The decision, each evening

For one judge and one sitting day, Samay decides which due cases to list, in which sitting, in what order and time window, and the next date for each case not listed or not moved.

## Step 1: score every case on its own details (0 to 100)

| Factor | Points | How it is measured |
|---|---|---|
| Case age | 35 | Years since filing over the full-points age (8 years for this roster: mean age plus two standard deviations, computed once, then fixed) |
| Hearing readiness | 25 | The measured share of this hearing type that moves a case (plea 90%, warrant 13.5%), reduced by at most 30% when required people were absent last time |
| Disposal proximity | 15 | Position in the 11 stages, Admission 0 to Judgement 10 |
| Hearing churn | 15 | Hearings held against the median expected by this stage; full points at twice the expected |
| Court-set urgency | 10 | "Last chance" 10 points, "for judgment" 8, from the last order |

The points are added, never multiplied, so an old case at a slow stage is not pushed to zero. Beside the score: Conditional status when a summons, warrant or notice is out; the liberty lane for bail; Backlog 4y+ and Ageing 5y+ flags; the age weight can never fall below 20.

## Step 2: estimate each hearing

The chance a hearing moves the case is the measured rate for its type, multiplied 2.5 times when the last order signals a risk ("Await warrant", "Absent: Accused", "not ready"), with each type's average held at the measured rate. If it fails, the reason follows the real mix for that type: process not back (which persists until the return), a party absent, not ready, court, unclear. Minutes are the reference minutes for the type with spread; an adjournment still costs 2 minutes. No machine learning is trained on 100 cases; as the court master records outcomes, the same rates are recomputed.

## Step 3: build the day

1. Take every case due on or before today.
2. Hold back any case whose summons or warrant has not returned, and any deferred case (3rd or later listing) whose last failure is not yet cured.
3. List bail first.
4. Reserve a quarter of each sitting's minutes for cases over 4 years old.
5. Rank the rest by score times the chance of moving forward, per minute; add 10 for a 2nd listing, 25 for a deferred listing, 15 while the case is inside the six-month window of NI Act s.143.
6. Pack each sitting to 95% of its minutes with a CP-SAT solver: morning 10:30 to 12:30 for admission to plea, bail, reports and applications; afternoon 13:30 to 17:00 for examination, evidence, arguments and judgement. The first 15 minutes go to pronouncements and mentions; each change of case costs a minute, a change of hearing type three.
7. Keep the next five ready cases per sitting on standby.
8. Group each advocate's matters and each hearing type together; give every case a one-hour window from its expected start.

## Step 4: after the day

After a hearing that moved the case, the next date is the reference gap for the next purpose (appearance 21 days, evidence 14, reports 45). After a failure, the gap for its reason: process 21 days or when the return is due, absence 7, not ready 10, court 3. Never a flat 60 days. On a judge's leave day, the listed cases move to the next sitting with room.

## What the court master supplies

The docket file (Excel or CSV), each day's outcomes, and the registry scrutiny of new complaints: statutory dates computed at e-filing, documents, summons details, jurisdiction. A late complaint files its condonation petition with the complaint and it is heard at admission.
""")
