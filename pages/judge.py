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
page = sidebar(["Today", "Calendar", "Cases", "Priority", "Insight"])
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

done = st.session_state.setdefault("done", {})   # (judge, case) -> (day, "Heard" or "Disposed"), fixed by the judge
disposed_on = {c: d for (jn, c), (d, kind) in done.items() if jn == name and kind == "Disposed"}


def listing(d):
    """The day's list, without cases the judge has closed on an earlier day."""
    dl = day_list(j, d, order)
    return dl[~dl.case_number.map(lambda c: c in disposed_on and disposed_on[c] < d)].reset_index(drop=True)


def done_marks(d, rows):
    return {c: kind for c in rows.case_number for (dd, kind) in [done.get((name, c), (None, None))] if dd == d}


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
if page == "Today":
    top = st.columns([2.2, 1, 1, 1, 1])
    day = top[0].selectbox("Court date", sitting_days, format_func=lambda x: x.strftime("%A %d %B %Y"))
    todays = listing(day)
    removed = st.session_state.setdefault("removed", set())
    marks = done_marks(day, todays)
    shown = todays[~todays.case_number.isin(removed - set(marks))]
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
        st.markdown(hearing_table(shown, advocate_of, height=380, done=marks), unsafe_allow_html=True)
    with right:
        open_ = [c for c in shown.case_number if c not in marks]
        fix = st.selectbox("Hearing over: mark and fix", ["None"] + open_)
        f1, f2 = st.columns(2)
        if fix != "None" and f1.button("Heard", width="stretch"):
            done[(name, fix)] = (day, "Heard")
            st.rerun()
        if fix != "None" and f2.button("Case disposed", width="stretch"):
            done[(name, fix)] = (day, "Disposed")
            st.rerun()
        if marks and st.button(f"Undo last ({list(marks)[-1]})", width="stretch"):
            done.pop((name, list(marks)[-1]))
            st.rerun()
        drop = st.selectbox("Remove a case from today", ["None"] + open_)
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
if page == "Calendar":
    import calendar as cal
    workset = set(rtl["workdays"])
    count = {d: len(listing(d)) for d in rtl["workdays"]}
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
            dl = listing(d)
            k1, k2, k3 = st.columns(3)
            k1.metric("Hearings", len(dl))
            k2.metric("Minutes", f"{float(dl.hearing_type.map(data['ref'].minutes).sum()):.0f} / 330")
            k3.metric("Your 100", int(dl.case_number.isin(real_ids).sum()))
            st.markdown(hearing_table(dl, advocate_of, height=430, show_why=False, done=done_marks(d, dl)),
                        unsafe_allow_html=True)

# ---------------------------------------------------------------- cases
if page == "Cases":
    cs = rtl["cases"].set_index("case_number")
    rows = []
    for cid in real.case_number:
        k, s_ = cs.loc[cid], sc.loc[cid]
        nxt = j[j.case_number == cid]
        rows.append({"Case": cid, "Score": s_.score, "Status": s_.status, "Flags": s_.flag_list,
                     "Next hearing": hearing_label(real.set_index("case_number").loc[cid].purpose_of_next_hearing),
                     "Age (years)": s_.age_years, "Advocate": advocate_of[cid],
                     "Listings": int(len(nxt)), "Moved forward": int((nxt.outcome == "substantive").sum()),
                     "Now": (f"Disposed, fixed {disposed_on[cid]:%d %b}" if cid in disposed_on
                             else "Disposed" if k.disposed else hearing_label(k.purpose_now))})
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
if page == "Priority":
    left, mid, right = st.columns([0.8, 2.2, 1.2])
    with left:
        st.markdown("<div class='eyebrow'>Weights, out of 100</div>", unsafe_allow_html=True)
        w = {}
        for k, v in PRIO.WEIGHTS.items():
            w[k] = st.slider(k.replace("_", " ").title(), 0, 60, v, 5, key=f"w_{k}")
        w = PRIO.rescale_weights(w)
        st.markdown("<div class='sub'>Applied: " + ", ".join(f"{k} {v:.0f}" for k, v in w.items())
                    + ". Age never below 20.</div>", unsafe_allow_html=True)
        if st.button("Reset weights", width="stretch"):
            for k in PRIO.WEIGHTS:
                st.session_state.pop(f"w_{k}", None)
            st.rerun()
    live = PRIO.score_roster(real, data["ref"], START, weights=w)
    live = live[~live.case_number.isin(disposed_on)].sort_values("score", ascending=False).reset_index(drop=True)
    live.insert(0, "Rank", range(1, len(live) + 1))
    with mid:
        table = live.rename(columns={
            "case_number": "Case", "score": "Score", "age_points": "Age", "readiness_points": "Ready",
            "disposal_points": "Stage", "churn_points": "Churn", "urgency_points": "Urgent",
            "status": "Status"})[["Rank", "Case", "Score", "Age", "Ready", "Stage", "Churn", "Urgent", "Status"]]
        ev = st.dataframe(table, width="stretch", hide_index=True, height=560, on_select="rerun",
                          selection_mode="single-row", key="prio_table",
                          column_config={"Rank": st.column_config.NumberColumn("#", width=36),
                                         "Case": st.column_config.TextColumn("Case", width=104),
                                         "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100,
                                                                                  format="%.1f", width=110),
                                         **{c: st.column_config.NumberColumn(c, width=54, format="%.1f")
                                            for c in ["Age", "Ready", "Stage", "Churn", "Urgent"]},
                                         "Status": st.column_config.TextColumn("Status", width=86)})
    with right:
        pick = ev.selection.rows[0] if ev.selection.rows else 0
        cid = live.case_number.iloc[pick]
        row = live.iloc[pick]
        r = real.set_index("case_number").loc[cid]
        st.markdown(f"<div class='eyebrow'>Rank {row.Rank} of {len(live)} · {row.status}</div>", unsafe_allow_html=True)
        st.markdown(f"### {cid} · {row.score:.1f}")
        ref = data["ref"]
        parts = PRIO.explain(filing_date=r.filing_date, current_stage=r.current_stage,
                             next_purpose=r.purpose_of_next_hearing, summary=r.last_hearing_summary,
                             total_hearings=r.total_hearings_held, as_of=START,
                             progress_rate={t: float(x.p_sub) for t, x in ref.iterrows()},
                             ref_median={t: float(x["Median Hearings per Case"]) for t, x in ref.iterrows()},
                             fp_age=scores.attrs["full_points_age"], weights=w)
        html = "".join(f"<div class='factor'><div class='top'><span class='name'>{n}</span>"
                       f"<span class='pts'>{pts:.1f}<span class='sub'> / {mx:.0f}</span></span></div>"
                       f"<div class='bar'><div style='width:{100 * pts / mx if mx else 0:.0f}%'></div></div>"
                       f"<div class='ev'>{e}</div></div>" for n, pts, mx, e in parts)
        if row.flag_list:
            html += f"<div class='sub' style='margin-top:4px'>{row.flag_list}</div>"
        st.markdown(f"<div style='height:520px;overflow-y:auto'>{html}</div>", unsafe_allow_html=True)

# ---------------------------------------------------------------- insight
if page == "Insight":
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
