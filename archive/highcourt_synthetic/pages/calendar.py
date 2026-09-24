"""Court calendar: the rolling-horizon plan across all three courtrooms, a year view per judge,
and the exact timings that keep an advocate out of two rooms at once."""
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.config import CALENDAR, JUDGES, OPTIMIZER, capacity, to_hhmm, to_min
from core.data import df, is_working_day
from core.optimize import explain, load_plan, plan_horizon
from core.readiness import case_frame
from pages.common import VIA_COLORS, resources, sidebar

MUTED = "#9a9893"
TYPE_COLORS = {"Urgent": VIA_COLORS["urgent"], "Ageing quota (5+ yrs)": VIA_COLORS["quota"],
               "Ready": VIA_COLORS["ready"]}
METHODS = ["greedy", "milp", "cpsat", "cpsat_saa"]
METHOD_LABELS = {"greedy": "Greedy (priority fill)", "milp": "MILP", "cpsat": "CP-SAT",
                 "cpsat_saa": "CP-SAT, stochastic (SAA)"}

conn, predictor = resources()
jid, day = sidebar()


def court_label(j):
    return f"Court {JUDGES[j]['court']}, {JUDGES[j]['name']}"


def case_type(urgent, old):
    return "Urgent" if urgent else ("Ageing quota (5+ yrs)" if old else "Ready")


# ---------------------------------------------------------------- normalise a plan

def from_result(res) -> pd.DataFrame:
    """The in-session plan_horizon() result as one row per listed case."""
    seq, plan = res["seq"], res["plan"]
    if plan is None or plan.empty:
        return pd.DataFrame()
    reasons = dict(zip(plan.id, explain(res["inst"], plan)))
    s = seq.copy()
    if "start_min" not in s:
        s["start_min"], s["window"] = np.nan, ""
    return pd.DataFrame({
        "case_id": s.id, "judge_id": s.judge_id, "date": pd.to_datetime(s.date).dt.date, "block": s.block,
        "start_min": s.start_min.astype(float), "expected_minutes": s.expected_minutes.astype(float),
        "window": s.window.fillna(""), "p_show": s.p_show, "p_eff": s.p_eff_plan, "reason": s.id.map(reasons),
        "title": s.title, "purpose": s.next_purpose, "counsel": s.pet_name.fillna("Party in person"),
        "adv": s.pet_adv.fillna(s.pet_name).fillna(""), "urgent": s.urgent.astype(bool), "old": s.old.astype(bool),
        "age_years": s.age_years, "priority": s.priority, "readiness": s.readiness,
    }).reset_index(drop=True)


@st.cache_data(show_spinner=False)
def _enrich(_conn, plan_id, start_iso):
    """Readiness, priority and advocate id for a stored plan (not stored in the plans table)."""
    frames = [case_frame(_conn, j, on=date.fromisoformat(start_iso)) for j in JUDGES]
    cf = pd.concat(frames, ignore_index=True)
    return cf[["id", "pet_adv", "readiness", "priority", "age_years"]].rename(columns={"id": "case_id"})


def from_stored(lp: pd.DataFrame) -> pd.DataFrame:
    lp = lp.copy()
    lp["date"] = pd.to_datetime(lp.date).dt.date
    start = min(lp.date)
    extra = _enrich(conn, lp.plan_id.iloc[0], start.isoformat())
    lp = lp.merge(extra, on="case_id", how="left")
    age = (pd.Timestamp(start) - pd.to_datetime(lp.filing_date)).dt.days / 365.25
    lp["age_years"] = lp.age_years.fillna(age)
    lp["start_min"] = [to_min(s) if isinstance(s, str) and ":" in s else np.nan for s in lp.start]
    return pd.DataFrame({
        "case_id": lp.case_id, "judge_id": lp.judge_id, "date": lp.date, "block": lp.block,
        "start_min": lp.start_min.astype(float), "expected_minutes": lp.expected_minutes.astype(float),
        "window": lp.window.fillna(""), "p_show": lp.p_show, "p_eff": lp.p_effective, "reason": lp.reason,
        "title": lp.title, "purpose": lp.next_purpose, "counsel": lp.counsel.fillna("Party in person"),
        "adv": lp.pet_adv.fillna(lp.counsel).fillna(""), "urgent": lp.urgency_flag.fillna(0).astype(bool),
        "old": lp.age_years >= 5, "age_years": lp.age_years, "priority": lp.priority, "readiness": lp.readiness,
    })


def fill_missing_times(p: pd.DataFrame) -> pd.DataFrame:
    """If a day was not sequenced, lay its cases back to back from each block start."""
    if not p.start_min.isna().any():
        return p
    p = p.copy()
    for (j, d, b), g in p[p.start_min.isna()].groupby(["judge_id", "date", "block"]):
        blk = next((x for x in JUDGES[j]["blocks"] if x["name"] == b), JUDGES[j]["blocks"][0])
        t = to_min(blk["start"])
        for i in g.sort_values("priority", ascending=False).index:
            p.at[i, "start_min"] = t
            t += p.at[i, "expected_minutes"]
    return p


# ---------------------------------------------------------------- page

st.title("Court calendar")
st.caption("The planner picks the day for every case over a rolling horizon (MILP or CP-SAT), then CP-SAT sets the "
           "exact time across all three courtrooms so no advocate is due in two rooms at once.")

c1, c2, c3, c4, c5 = st.columns([1.3, 1, 1.4, 1, 1.2], vertical_alignment="bottom")
h_start = c1.date_input("Horizon starts", value=day)
h_opts = [5, 10, 15]
h_default = OPTIMIZER["horizon_working_days"] if OPTIMIZER["horizon_working_days"] in h_opts else 10
h_days = c2.selectbox("Working days", h_opts, index=h_opts.index(h_default))
method = c3.selectbox("Method", METHODS, index=METHODS.index(OPTIMIZER["method"]) if OPTIMIZER["method"] in METHODS
                      else 0, format_func=METHOD_LABELS.get)
prefiling = c4.toggle("Pre-filing check", value=True,
                      help="On: known filing defects are caught and fixed before listing. Off: the planner can only "
                           "discount every case by the average defect risk.")
if c5.button("Plan these days", type="primary", width="stretch"):
    with st.spinner("Planning the horizon and sequencing every day. This takes 20 to 60 seconds."):
        st.session_state["cal_plan"] = plan_horizon(conn, predictor, h_start, method=method, prefiling=prefiling,
                                                    horizon=h_days)

res = st.session_state.get("cal_plan")
if res is not None:
    plan = from_result(res)
    days = list(res["inst"].days)
    info = res["info"]
    bits = [f"Method {METHOD_LABELS.get(info.get('method'), info.get('method'))}", f"status {info.get('status')}",
            f"objective {info.get('objective')}"]
    if info.get("gap_pct") is not None:
        bits.append(f"optimality gap {info['gap_pct']}%")
    bits.append(f"{info.get('seconds')} s to solve")
    bits.append("pre-filing check on" if res["inst"].prefiling else "pre-filing check off")
    st.caption(". ".join(bits) + f". Plan {res.get('plan_id')}.")
else:
    lp = load_plan(conn)
    if lp.empty:
        st.info("No plan yet. Choose the horizon above and press Plan these days.")
        st.stop()
    plan = from_stored(lp)
    days = sorted(plan.date.unique())
    st.caption(f"Showing the latest stored plan ({lp.method.iloc[0]}, "
               f"pre-filing check {'on' if lp.prefiling.iloc[0] else 'off'}, created {lp.created.iloc[0]}). "
               "Press Plan these days for a fresh one with solver details and plan quality.")

if plan.empty:
    st.info("The planner listed no cases in this horizon.")
    st.stop()

plan = fill_missing_times(plan)
plan["end_min"] = plan.start_min + plan.expected_minutes
plan["type"] = [case_type(u, o) for u, o in zip(plan.urgent, plan.old)]
plan["court"] = plan.judge_id.map(court_label)

cal = df(conn, "SELECT date, judge_id, holiday, judge_leave FROM calendar")
off = {(r.judge_id, r.date): ("Holiday" if r.holiday else "Judge on leave")
       for r in cal.itertuples() if r.holiday or r.judge_leave}

tab_year, tab_fort, tab_day, tab_prio, tab_q = st.tabs(["Year", "Fortnight", "Day view", "Priority order",
                                                        "Plan quality"])

# ---------------------------------------------------------------- Year
with tab_year:
    year = 2026
    hol = {pd.Timestamp(k).date(): v for k, v in CALENDAR["holidays"].items()}
    vac = []
    for v in CALENDAR["vacations"]:
        vac.append((pd.Timestamp(v["start"]).date(), pd.Timestamp(v["end"]).date(), v["name"]))
    leave = {date.fromisoformat(r.date) for r in cal[(cal.judge_id == jid) & (cal.judge_leave == 1)].itertuples()}
    wsat = {pd.Timestamp(d).date() for d in CALENDAR.get("working_saturdays", [])}
    rows = []
    d = date(year, 1, 1)
    while d.year == year:
        v = next((n for a, b, n in vac if a <= d <= b), None)
        if d in hol:
            kind, note = "Holiday", hol[d]
        elif v:
            kind, note = "Vacation", v
        elif d.weekday() in CALENDAR["weekend_days"] and d not in wsat:
            kind, note = "Weekend", d.strftime("%A")
        elif d in leave:
            kind, note = "Judge on leave", JUDGES[jid]["name"]
        elif is_working_day(d):
            kind, note = "Sitting day", "Working Saturday" if d in wsat else ""
        else:
            kind, note = "Holiday", ""
        rows.append({"date": d, "month": d.strftime("%b"), "dom": d.day, "kind": kind, "note": note})
        d += timedelta(days=1)
    yr = pd.DataFrame(rows)
    sitting = int((yr.kind == "Sitting day").sum())
    target = CALENDAR["annual_sitting_days_target"]

    k1, k2, k3 = st.columns(3)
    k1.metric(f"Sitting days in {year}", sitting, f"{sitting - target:+} vs target {target}", delta_color="off", delta_arrow="off")
    k2.metric("Holidays and vacation days on weekdays",
              int(yr[yr.kind.isin(["Holiday", "Vacation"]) & (pd.to_datetime(yr.date).dt.weekday < 5)].shape[0]))
    k3.metric(f"{JUDGES[jid]['name']} on leave", int((yr.kind == "Judge on leave").sum()))

    kind_colors = {"Sitting day": VIA_COLORS["ready"], "Weekend": "#d9d7d2", "Holiday": VIA_COLORS["quota"],
                   "Vacation": VIA_COLORS["manual"], "Judge on leave": "#e87ba4"}
    months = [date(year, m, 1).strftime("%b") for m in range(1, 13)]
    fig = go.Figure()
    for kind, color in kind_colors.items():
        g = yr[yr.kind == kind]
        if g.empty:
            continue
        fig.add_scatter(x=g.dom, y=g.month, mode="markers", name=kind,
                        marker=dict(symbol="square", size=15, color=color, line=dict(width=0)),
                        customdata=np.stack([pd.to_datetime(g.date).dt.strftime("%a %d %b %Y"), g.note], axis=1),
                        hovertemplate="%{customdata[0]}<br>" + kind + "<br>%{customdata[1]}<extra></extra>")
    fig.update_yaxes(categoryorder="array", categoryarray=months[::-1], title=None, showgrid=False)
    fig.update_xaxes(range=[0.4, 31.6], dtick=1, title="Day of month", showgrid=False, side="top")
    fig.update_layout(height=470, margin=dict(l=0, r=0, t=30, b=0), legend=dict(orientation="h", y=-0.05),
                      title=f"{court_label(jid)}: {year}")
    st.plotly_chart(fig, width="stretch")

    ours = yr[yr.kind == "Sitting day"].groupby("month").size().reindex(months).fillna(0).astype(int)
    official = CALENDAR.get("sitting_days_2026", {})
    mt = pd.DataFrame({"Month": months, "Sitting days here": ours.values,
                       "Official calendar": [official.get(m, None) for m in range(1, 13)]})
    with st.expander("Sitting days by month"):
        st.dataframe(mt, width="stretch", hide_index=True)
    st.caption(f"Target {target} sitting days a year. Only the holidays in the demo window (October to December) are "
               "loaded, so this count can exceed the target until the organisers' calendar is added. Leave days "
               "come from the synthetic court.")

# ---------------------------------------------------------------- Fortnight
with tab_fort:
    agg = plan.groupby(["judge_id", "date"]).agg(n=("case_id", "size"), mins=("expected_minutes", "sum"),
                                                 eff=("p_eff", "sum"))
    judges = list(JUDGES)
    z, text, hover = [], [], []
    for j in judges:
        cap = capacity(j)
        zr, tr, hr = [], [], []
        for d in days:
            reason = off.get((j, d.isoformat()))
            if reason:
                zr.append(None)
                tr.append("No sitting")
                hr.append(f"{court_label(j)}<br>{d:%a %d %b}<br>No sitting: {reason.lower()}")
                continue
            if (j, d) in agg.index:
                a = agg.loc[(j, d)]
                u = 100 * a.mins / cap
                zr.append(u)
                tr.append(f"{int(a.n)} cases<br>{u:.0f}%<br>{a.eff:.1f} eff.")
                hr.append(f"{court_label(j)}<br>{d:%a %d %b}<br>{int(a.n)} cases listed<br>"
                          f"{a.mins:.0f} of {cap} min planned ({u:.0f}%)<br>{a.eff:.1f} predicted effective hearings")
            else:
                zr.append(0)
                tr.append("0 cases")
                hr.append(f"{court_label(j)}<br>{d:%a %d %b}<br>Nothing listed")
        z.append(zr)
        text.append(tr)
        hover.append(hr)
    xl = [f"{d:%a %d %b}" for d in days]
    yl = [court_label(j) for j in judges]
    fig = go.Figure(go.Heatmap(z=z, x=xl, y=yl, text=text, texttemplate="%{text}", customdata=hover,
                               hovertemplate="%{customdata}<extra></extra>", zmin=0, zmax=110,
                               colorscale=[[0, "#eaf2fb"], [1, "#2a78d6"]], xgap=3, ygap=3,
                               colorbar=dict(title="Planned<br>use of court<br>time (%)", thickness=12)))
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(height=320, margin=dict(l=0, r=0, t=10, b=0), plot_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, width="stretch")
    st.caption("Each cell: cases listed, planned minutes as a share of the court's sitting minutes, and predicted "
               "effective hearings (sum of P(moves forward)). Blank cells are holidays or judge leave.")

    tot = plan.groupby("date").agg(n=("case_id", "size"), eff=("p_eff", "sum"))
    pick = st.selectbox("Day to open in Day view", days, key="cal_day",
                        index=days.index(day) if day in days else 0,
                        format_func=lambda d: f"{d:%A %d %B}: {int(tot.n.get(d, 0))} cases, "
                                              f"{tot.eff.get(d, 0):.0f} predicted effective")

# ---------------------------------------------------------------- Day view
with tab_day:
    dd = plan[plan.date == pick].sort_values(["judge_id", "start_min"]).copy()
    st.subheader(f"{pick:%A %d %B %Y}")
    if dd.empty:
        st.info("Nothing listed on this day.")
    else:
        courts_per = dd.groupby("adv").judge_id.nunique()
        matters_per = dd.groupby("adv").size()
        names = dd.groupby("adv").counsel.first()
        advs = sorted([a for a in courts_per.index if a and names[a] != "Party in person"],
                      key=lambda a: (-courts_per[a], -matters_per[a], names[a]))
        adv = st.selectbox("Advocate", [""] + advs, key=f"cal_adv_{pick.isoformat()}",
                           format_func=lambda a: "Everyone" if not a else
                           f"{names[a]}: {matters_per[a]} matters in {courts_per[a]} court"
                           f"{'s' if courts_per[a] > 1 else ''}",
                           help="Advocates in two or more courtrooms today are listed first.")

        base = datetime.combine(pick, datetime.min.time())
        court_order = [court_label(j) for j in JUDGES if j in set(dd.judge_id)]
        fig = go.Figure()
        for t, color in TYPE_COLORS.items():
            g = dd[dd.type == t]
            if g.empty:
                continue
            op = [1.0 if (not adv or a == adv) else 0.15 for a in g.adv]
            cd = np.stack([g.title, g.purpose, g.counsel, g.window, (100 * g.p_show).round().astype(int),
                           g.reason, g.start_min.map(to_hhmm), g.end_min.map(to_hhmm)], axis=1)
            fig.add_bar(y=g.court, x=g.expected_minutes.clip(lower=2) * 60_000,
                        base=[base + timedelta(minutes=float(m)) for m in g.start_min], orientation="h",
                        name=t, marker=dict(color=color, opacity=op, line=dict(width=1, color="rgba(255,255,255,0.9)")),
                        customdata=cd,
                        hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[6]} to %{customdata[7]}<br>"
                                      "Purpose: %{customdata[1]}<br>Counsel: %{customdata[2]}<br>"
                                      "Window: %{customdata[3]}<br>P(heard): %{customdata[4]}%<br>"
                                      "Why: %{customdata[5]}<extra>" + t + "</extra>")
        for yi, j in enumerate([j for j in JUDGES if j in set(dd.judge_id)]):
            for b in JUDGES[j]["blocks"]:
                for edge in (b["start"], b["end"]):
                    x = base + timedelta(minutes=to_min(edge))
                    fig.add_shape(type="line", x0=x, x1=x, y0=yi - 0.45, y1=yi + 0.45, xref="x", yref="y",
                                  line=dict(color=MUTED, width=1, dash="dot"))
        fig.add_scatter(x=[None], y=[None], mode="lines", name="Block start or end",
                        line=dict(color=MUTED, width=1, dash="dot"), hoverinfo="skip")
        fig.update_xaxes(type="date", tickformat="%H:%M", title=None)
        fig.update_yaxes(categoryorder="array", categoryarray=court_order, autorange="reversed", title=None)
        fig.update_layout(barmode="overlay", height=120 + 70 * len(court_order), margin=dict(l=0, r=0, t=10, b=0),
                          legend=dict(orientation="h", y=-0.25), bargap=0.3)
        st.plotly_chart(fig, width="stretch")
        st.caption("One bar per case from its planned start to its expected end. Hover a bar for the reason. "
                   "Pick an advocate to highlight their matters.")

        if adv:
            it = dd[dd.adv == adv].sort_values("start_min").reset_index(drop=True)
            gaps, prev = [], None
            for r in it.itertuples():
                if prev is None:
                    gaps.append("First matter")
                else:
                    gap = r.start_min - prev.end_min
                    move = "moves to " + r.court.split(",")[0] if r.judge_id != prev.judge_id else "same courtroom"
                    gaps.append(f"{gap:.0f} min after previous, {move}")
                prev = r
            st.markdown(f"**Itinerary for {names[adv]}**")
            st.dataframe(pd.DataFrame({"Time": it.start_min.map(to_hhmm) + " to " + it.end_min.map(to_hhmm),
                                       "Court": it.court, "Case": it.title, "Purpose": it.purpose,
                                       "Travel gap": gaps}), width="stretch", hide_index=True)
            if it.judge_id.nunique() > 1:
                st.caption(f"The sequencer keeps at least {OPTIMIZER['sequencing']['travel_minutes']} minutes "
                           "between matters in different courtrooms.")

        st.markdown("**The day's list**")
        lst = dd.sort_values(["judge_id", "start_min"]).copy()
        lst["#"] = lst.groupby("judge_id").cumcount() + 1
        st.dataframe(pd.DataFrame({"Court": lst.court, "#": lst["#"], "Time": lst.start_min.map(to_hhmm),
                                   "Window": lst.window, "Case": lst.title, "Purpose": lst.purpose,
                                   "Counsel": lst.counsel, "Type": lst.type, "Why": lst.reason}),
                     width="stretch", hide_index=True, height=420)

# ---------------------------------------------------------------- Priority order
with tab_prio:
    po = plan.assign(_p=plan.priority.fillna(-1)).sort_values(["urgent", "_p"], ascending=[False, False]).copy()
    po["Rank"] = range(1, len(po) + 1)
    view = pd.DataFrame({"Rank": po.Rank, "Case": po.case_id + "  " + po.title, "Judge": po.court,
                         "Age (yrs)": po.age_years, "Purpose": po.purpose, "Readiness": po.readiness,
                         "Date": po.date, "Time": po.start_min.map(to_hhmm), "Counsel": po.counsel, "Why": po.reason})
    q = st.text_input("Filter", placeholder="Case number, title or counsel")
    if q:
        m = (view.Case.str.contains(q, case=False, regex=False) | view.Counsel.str.contains(q, case=False, regex=False))
        view = view[m]
    st.caption(f"{len(view)} of {len(po)} planned cases. Urgent matters first, then the planner's priority "
               "(hearing type, age and readiness).")
    cc = {"Age (yrs)": st.column_config.NumberColumn(format="%.1f"),
          "Date": st.column_config.DateColumn(format="ddd D MMM")}
    if view.Readiness.notna().any():
        cc["Readiness"] = st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d")
    else:
        view = view.drop(columns="Readiness")
    st.dataframe(view, width="stretch", hide_index=True, height=560, column_config=cc)

# ---------------------------------------------------------------- Plan quality
with tab_q:
    ev = res.get("eval") if res is not None else None
    if not ev:
        st.info("Plan quality comes from a Monte Carlo replay of a fresh plan. Press Plan these days to see it.")
    else:
        st.caption("Each planned day is played forward 200 times against the hidden truth: who turns up, how long "
                   "hearings run, and which filings carry a defect nobody caught.")
        tiles = [
            ("Listed", f"{ev['listed']:,}", "Cases placed on a list in the horizon."),
            ("Effective hearings", f"{ev['effective']:.0f}", "Expected hearings where the case moved forward."),
            ("Predictability", f"{ev['predictability_pct']:.0f}%", "Share of listed cases actually heard."),
            ("Substantiveness", f"{ev['substantiveness_pct']:.0f}%", "Share of heard cases that moved forward."),
            ("Wasted listings", f"{ev['wasted_listings']:.0f}", "Listings that did not produce an effective hearing."),
            ("Utilisation", f"{ev['utilisation_pct']:.0f}%", "Sitting minutes actually used for hearings."),
            ("Overtime", f"{ev['overtime_min_per_court_day']:.1f} min",
             "Minutes past the end of a block, per court-day."),
            ("Advocate trips", f"{ev['advocate_trips']:,}", "Days an advocate has to come to court."),
            ("Clash days", f"{ev['clash_days']:,}", "Advocate-days with matters in two courtrooms."),
            ("Load spread", f"{ev['load_spread_pct']:.0f} pts",
             "Gap in planned use between the busiest and quietest court-day."),
            ("Old cases listed", f"{ev['old_cases_listed']:,}", "Cases 5 or more years old placed on a list."),
            ("Due but unlisted", f"{ev['due_left_unlisted']:,}",
             "Cases due in the horizon that the plan left out."),
            ("Urgent on time", f"{ev['urgent_on_time_pct']:.0f}%",
             "Urgent matters listed within 2 working days."),
        ]
        for i in range(0, len(tiles), 4):
            cols = st.columns(4)
            for col, (label, val, why) in zip(cols, tiles[i:i + 4]):
                col.metric(label, val, help=why)
                col.caption(why)
