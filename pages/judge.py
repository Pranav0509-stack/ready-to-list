from datetime import date, datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import priority as PRIO
from core import pucar_engine as E
from pages.shared import (BLUE, COURT_OF, LISTING, LISTING_COLOR, ORANGE, OUTCOME, START, day_list, docket,
                          hearing_label, hearing_table, page_setup, plans, reference_dir, require, sidebar, why)

page_setup()
u = require("Judge")
sidebar()
name = u["name"]

head = st.columns([3, 1])
head[0].markdown(f"<div class='eyebrow'>{COURT_OF.get(name, '')}</div>", unsafe_allow_html=True)
head[0].markdown(f"# {name}")
head[1].markdown(f"<div style='text-align:right;color:#64748B;padding-top:14px'>{COURT_OF.get(name, '')}, "
                 f"sitting 10:30 to 12:30 and 13:30 to 17:00</div>", unsafe_allow_html=True)
if docket(name) is None:
    st.info("The court master has not uploaded your docket yet.")
    st.stop()

R, real, data, scores = plans(docket(name), name, reference_dir())
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

tabs = st.tabs(["Today", "Calendar", "Cases", "Priority", "Insight", "How Samay decides"])


def timeline_chart(rows, height=190):
    """The day's hearings on a clock, one bar per hearing, coloured by listing number."""
    if rows.empty:
        return None
    base_day = datetime(2026, 1, 1)
    fig = go.Figure()
    for key, label in LISTING.items():
        g = rows[rows.listing == key]
        if g.empty:
            continue
        starts = [base_day + timedelta(minutes=E.to_min(s)) for s in g.start]
        durs = [float(minutes[t]) for t in g.hearing_type]
        fig.add_bar(base=starts, x=[d * 60000 for d in durs], y=g.block, orientation="h", name=label,
                    marker_color=LISTING_COLOR[key], marker_line_color="#FFFFFF", marker_line_width=1.5,
                    customdata=list(zip(g.case_number, g.hearing_type.map(hearing_label), g.score)),
                    hovertemplate="%{customdata[0]}<br>%{customdata[1]}<br>score %{customdata[2]}<extra></extra>")
    for b in E.DAY["blocks"]:
        for edge in (b["start"], b["end"]):
            fig.add_vline(x=base_day + timedelta(minutes=E.to_min(edge)), line_color="#CBD5E1", line_width=1)
    fig.update_layout(barmode="overlay", height=height, margin=dict(l=0, r=0, t=6, b=0),
                      legend=dict(orientation="h", y=1.25, x=0), xaxis=dict(type="date", tickformat="%H:%M", range=[
                          base_day + timedelta(minutes=E.to_min("10:15")), base_day + timedelta(minutes=E.to_min("17:15"))]),
                      yaxis=dict(autorange="reversed", title=None), plot_bgcolor="#FFFFFF")
    return fig


# ---------------------------------------------------------------- today
with tabs[0]:
    top = st.columns([2.2, 1, 1, 1, 1])
    day = top[0].selectbox("Court date", sitting_days, format_func=lambda x: x.strftime("%A %d %B %Y"))
    todays = day_list(j, day, order)
    removed = st.session_state.setdefault("removed", set())
    shown = todays[~todays.case_number.isin(removed)]
    planned = float(shown.hearing_type.map(minutes).sum())
    top[1].metric("Listed", len(shown))
    top[2].metric("Planned minutes", f"{planned:.0f} / 330")
    top[3].metric("Expected to move forward", f"{(shown.outcome == 'substantive').sum()}")
    top[4].metric("Deferred, fixed slot", int((shown.listing == "deferred").sum()))
    fig = timeline_chart(shown)
    if fig:
        st.plotly_chart(fig, width="stretch")
    left, right = st.columns([2.6, 1])
    with left:
        st.markdown(hearing_table(shown, advocate_of, height=380), unsafe_allow_html=True)
    with right:
        drop = st.selectbox("Remove a case from today", ["None"] + shown.case_number.tolist())
        b1, b2 = st.columns(2)
        if drop != "None" and b1.button("Remove", width="stretch"):
            removed.add(drop)
            st.rerun()
        if removed and b2.button("Restore", width="stretch"):
            st.session_state.removed = set()
            st.rerun()
        approved = st.session_state.get("approved") == (name, day)
        if st.button("Approve today's list", type="primary", width="stretch", disabled=approved):
            st.session_state.approved = (name, day)
            st.rerun()
        if approved:
            st.success(f"Approved for {day:%d %B}. Time windows go to advocates and parties.")
        st.markdown(f"**Grouped for you**  \n{shown.case_number.map(advocate_of).nunique()} advocates for "
                    f"{len(shown)} matters; {shown.hearing_type.nunique()} kinds of hearing, called together.")

# ---------------------------------------------------------------- calendar
with tabs[1]:
    import calendar as cal
    workset = set(rtl["workdays"])
    count = j.groupby("date").size().to_dict()
    months = sorted({(d.year, d.month) for d in rtl["workdays"]})
    left, right = st.columns([1.45, 1])
    with left:
        pick = st.segmented_control("Month", [f"{cal.month_name[m]} {y}" for y, m in months],
                                    default=f"{cal.month_name[months[0][1]]} {months[0][0]}", label_visibility="collapsed")
        y, mth = next(((yy, mm) for yy, mm in months if f"{cal.month_name[mm]} {yy}" == pick), months[0])
        sel = st.session_state.setdefault("cal_day", sitting_days[0])
        hdr = st.columns(7)
        for c, dn in zip(hdr, ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]):
            c.markdown(f"<div class='cal-head'>{dn}</div>", unsafe_allow_html=True)
        for week in cal.monthcalendar(y, mth):
            cols = st.columns(7)
            for c, dnum in zip(cols, week):
                if dnum == 0:
                    c.markdown("")
                    continue
                d = date(y, mth, dnum)
                if d in leave:
                    label, kind = f"**{dnum}** leave", "leave"
                elif d in holidays:
                    label, kind = f"**{dnum}** holiday", "holiday"
                elif d.weekday() >= 5 or d not in workset:
                    label, kind = f"**{dnum}**", "off"
                else:
                    label, kind = f"**{dnum}** {count.get(d, 0)} cases", "sit"
                if c.button(label, key=f"cal_{d.isoformat()}", width="stretch",
                            type="primary" if d == sel else "secondary"):
                    st.session_state.cal_day = d
                    st.rerun()
        st.markdown("<span class='daypill sit'>Sitting</span><span class='daypill holiday'>Holiday</span>"
                    "<span class='daypill leave'>Judge on leave</span><span class='daypill off'>Plain date: no sitting</span>",
                    unsafe_allow_html=True)
    with right:
        d = st.session_state.cal_day
        st.markdown(f"<div class='eyebrow'>{d:%A}</div>", unsafe_allow_html=True)
        st.markdown(f"### {d:%d %B %Y}")
        if d in leave:
            st.markdown("Judge on leave. The cases due today move to the next sitting day with room; none takes the flat 60-day gap.")
        elif d in holidays:
            st.markdown(f"Holiday: {holidays[d]}. No sitting.")
        elif d.weekday() >= 5 or d not in workset:
            st.markdown("No sitting.")
        else:
            dl = day_list(j, d, order)
            k1, k2, k3 = st.columns(3)
            k1.metric("Hearings", len(dl))
            k2.metric("Minutes", f"{float(dl.hearing_type.map(data['ref'].minutes).sum()):.0f} / 330")
            k3.metric("Your 100", int(dl.case_number.isin(real_ids).sum()))
            st.markdown(hearing_table(dl, advocate_of, height=430, show_why=False), unsafe_allow_html=True)

# ---------------------------------------------------------------- cases
with tabs[2]:
    cs = rtl["cases"].set_index("case_number")
    rows = []
    for cid in real.case_number:
        k, s_ = cs.loc[cid], sc.loc[cid]
        nxt = j[j.case_number == cid]
        rows.append({"Case": cid, "Score": s_.score, "Status": s_.status, "Flags": s_.flag_list,
                     "Next hearing": hearing_label(real.set_index("case_number").loc[cid].purpose_of_next_hearing),
                     "Age (years)": s_.age_years, "Advocate": advocate_of[cid],
                     "Listings": int(len(nxt)), "Moved forward": int((nxt.outcome == "substantive").sum()),
                     "Now": "Disposed" if k.disposed else hearing_label(k.purpose_now)})
    dk = pd.DataFrame(rows).sort_values("Score", ascending=False)
    f = st.columns([1.2, 1, 1, 1.5])
    status = f[0].multiselect("Status", ["Eligible", "Conditional"], default=["Eligible", "Conditional"])
    old_only = f[1].toggle("Over 4 years")
    deferred_only = f[2].toggle("Churning")
    view = dk[dk.Status.isin(status)]
    if old_only:
        view = view[view["Age (years)"] >= 4]
    if deferred_only:
        view = view[view.Flags.str.contains("Churning")]
    cid = f[3].selectbox("Open a case", view.Case.tolist())
    left, right = st.columns([1.7, 1])
    with left:
        st.dataframe(view, width="stretch", hide_index=True, height=560)
    with right:
        s_ = sc.loc[cid]
        st.markdown(f"**{cid}**, score **{s_.score}**  \nAge {s_.age_points}, readiness {s_.readiness_points}, "
                    f"disposal {s_.disposal_points}, churn {s_.churn_points}, urgency {s_.urgency_points}  \n"
                    f"{s_.status}. {s_.flag_list}")
        st.code(data["roster"][data["roster"].case_number == cid].iloc[0].last_hearing_summary, language=None)
        hist = j[j.case_number == cid][["date", "start", "hearing_type", "outcome"]].copy()
        hist["hearing_type"] = hist.hearing_type.map(hearing_label)
        hist["outcome"] = hist.outcome.map(OUTCOME)
        st.dataframe(hist.rename(columns={"date": "Date", "start": "Time", "hearing_type": "Hearing",
                                          "outcome": "Outcome"}), width="stretch", hide_index=True, height=200)

# ---------------------------------------------------------------- priority
with tabs[3]:
    left, right = st.columns([1, 1.8])
    with left:
        st.markdown("**Weights, out of 100**")
        w = {}
        for k, v in PRIO.WEIGHTS.items():
            w[k] = st.slider(k.replace("_", " ").title(), 0, 60, v, 5, key=f"w_{k}")
        w = PRIO.rescale_weights(w)
        st.markdown("Applied: " + ", ".join(f"{k.replace('_', ' ')} {v:.0f}" for k, v in w.items())
                    + ". Age never below 20.")
        st.markdown("<div class='eyebrow'>How the score is used</div>", unsafe_allow_html=True)
        st.markdown("Each sitting day: cases due are ranked by score, weighted by how likely the hearing moves "
                    "the case and by its minutes. Bail first, a quarter of the minutes reserved for cases over "
                    "four years, Conditional cases held until the process returns. The packer fills 95% of the day.")
        live = PRIO.score_roster(real, data["ref"], START, weights=w)
        bands = pd.cut(live.score, [0, 20, 40, 60, 80, 100],
                       labels=["0 to 20", "20 to 40", "40 to 60", "60 to 80", "80 to 100"]).value_counts().sort_index()
        fig = go.Figure(go.Bar(x=bands.index.astype(str), y=bands.values, marker_color=BLUE, text=bands.values,
                               textposition="outside"))
        fig.update_layout(height=220, margin=dict(l=0, r=0, t=10, b=0), yaxis_title="Cases", plot_bgcolor="#FFFFFF")
        st.plotly_chart(fig, width="stretch")
    with right:
        st.dataframe(live.sort_values("score", ascending=False).rename(columns={
            "case_number": "Case", "score": "Score", "age_points": "Age", "readiness_points": "Readiness",
            "disposal_points": "Disposal", "churn_points": "Churn", "urgency_points": "Urgency",
            "age_years": "Years", "status": "Status", "flag_list": "Flags"}).drop(columns=["process_pending"]),
            width="stretch", hide_index=True, height=560)

# ---------------------------------------------------------------- insight
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
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Cases disposed this quarter", f"{rtl['disposed']:.0f}", f"{rtl['disposed'] - base['disposed']:+.0f} vs today's rules")
    k2.metric("Listed cases reached", f"{rtl['reach_rate_pct']:.0f}%", f"{rtl['reach_rate_pct'] - base['reach_rate_pct']:+.0f} pts")
    k3.metric("Heard cases that move forward", f"{rtl['substantiveness_pct']:.0f}%",
              f"{rtl['substantiveness_pct'] - base['substantiveness_pct']:+.0f} pts")
    k4.metric("Wasted listings", f"{rtl['wasted_listings']:.0f}", f"{rtl['wasted_listings'] - base['wasted_listings']:+.0f}",
              delta_color="inverse")
    left, right = st.columns([1.2, 1])
    with left:
        st.dataframe(pd.DataFrame([{"Measure": l, "Today's rules": fmt(base, k, un), "Samay": fmt(rtl, k, un)}
                                   for l, k, un in spec]), width="stretch", hide_index=True, height=320)
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
                          margin=dict(l=0, r=0, t=40, b=0), legend=dict(orientation="h", y=-0.15),
                          hovermode="x unified", plot_bgcolor="#FFFFFF")
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

## Every parameter Samay takes, and its value for this judge

Each judge has a configuration: sitting hours, leave, weights and rules. Change the file, not the code, for another bench.
""")
        cfg, dayc = E.CFG, E.DAY
        params = []
        for b in dayc["blocks"]:
            params.append(("Court day", f"{b['name']} sitting", f"{b['start']} to {b['end']}: " + ", ".join(hearing_label(p) for p in b["purposes"])))
        params += [("Court day", "Opening minutes (pronouncements, mentions)", dayc["opening_minutes"]),
                   ("Court day", "Changeover, same hearing type", f"{dayc['changeover_same']} min"),
                   ("Court day", "Changeover, different hearing type", f"{dayc['changeover_switch']} min"),
                   ("Court day", "An adjournment still costs", f"{dayc['adjourned_minutes']} min"),
                   ("Court day", "Sitting filled to", f"{dayc['fill_target']:.0%} of net minutes"),
                   ("Court day", "Spread around reference minutes", f"lognormal sigma {dayc['duration_sigma']}"),
                   ("Judge", "Leave days", ", ".join(str(x) for x in cfg["judge_leave"]["dates"])),
                   ("Judge", "Casual leave allowed a year", cfg["judge_leave"]["casual_leave_days_per_year"])]
        params += [("Priority score", f"{k.replace('_', ' ').title()} weight", v) for k, v in PRIO.WEIGHTS.items()]
        params += [("Priority score", "Full points for age at", f"{scores.attrs['full_points_age']} years (mean + 2 sd, fixed)"),
                   ("Priority score", "Attendance floor", f"{PRIO.ATTENDANCE_FLOOR:.0%} of readiness when nobody required attends"),
                   ("Priority score", "Age weight can never fall below", PRIO.MIN_AGE_WEIGHT),
                   ("Protected rules", "Liberty lane", ", ".join(cfg["urgent_purposes"])),
                   ("Protected rules", "Share of each sitting for cases over 4 years", f"{cfg['ageing_quota']:.0%}"),
                   ("Listings", "2nd listing boost", cfg["escalation"]["second"]["priority_boost"]),
                   ("Listings", "Deferred (3rd+) boost", f"{cfg['escalation']['deferred']['priority_boost']}, held until the last failure is cured"),
                   ("Statute", "NI Act s.143 six-month window", f"{cfg['statutory_clock']['days']} days, boost {cfg['statutory_clock']['priority_boost']}")]
        for g, v in cfg["next_date"]["after_failure_days"].items():
            params.append(("Next date", f"After a failure: {g}", f"{v} days"))
        params.append(("Next date", "Today's rules, for comparison", f"flat {cfg['next_date']['baseline_gap_days']} days"))
        lv = cfg["levers"]
        params += [("Readiness levers", "Process status known", f"{lv['process_tracking']['status_accuracy']:.0%} of the time"),
                   ("Readiness levers", "T-2 confirmation removes", f"{lv['intent_check']['removed']:.0%} of not-ready failures"),
                   ("Readiness levers", "Fixed slots and clustering remove", f"{lv['fixed_slot_cluster']['removed']:.0%} of absences"),
                   ("Readiness levers", "Last-order signal multiplies risk by", lv["text_signals"]["boost"]),
                   ("Pre-filing", "Removes at admission", f"{lv['prefiling']['unready_removed']:.0%} of not-ready, {lv['prefiling']['process_removed']:.0%} of process failures"),
                   ("Pre-filing", "Summons failures removed with full contact details", f"{lv['prefiling']['summons_process_removed']:.0%}"),
                   ("Pre-filing", "E-summons return", f"{lv['prefiling']['summons_return_days'][0]} to {lv['prefiling']['summons_return_days'][1]} working days, against 3 to 25 by post")]
        st.dataframe(pd.DataFrame(params, columns=["Group", "Parameter", "Value"]), width="stretch", hide_index=True,
                     height=min(900, 38 + 35 * len(params)))
        st.markdown("**Per hearing type, from the data**")
        ref = data["ref"]
        st.dataframe(pd.DataFrame({"Hearing type": [hearing_label(t) for t in ref.index],
                                   "Moves the case": (ref.p_sub * 100).round(1).astype(str) + "%",
                                   "Minutes": ref.minutes.astype(int), "Days to next": ref.gap_days.astype(int),
                                   "Median hearings per case": ref["Median Hearings per Case"].astype(int),
                                   "Fails: process": (100 * (1 - ref.p_sub) * ref.share_process).round(0).astype(int).astype(str) + "%",
                                   "Fails: absence": (100 * (1 - ref.p_sub) * ref.share_absence).round(0).astype(int).astype(str) + "%",
                                   "Fails: not ready": (100 * (1 - ref.p_sub) * ref.share_unready).round(0).astype(int).astype(str) + "%"}),
                     width="stretch", hide_index=True, height=38 + 35 * len(ref))
