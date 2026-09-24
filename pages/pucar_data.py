from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import pucar_engine as E
from pages.common import BASE_COLOR, RTL_COLOR, sidebar

sidebar()
st.title("On the organisers' data")
st.caption("The hackathon's own data: a Kerala court pilot, anonymised. Real probability that each of 14 hearing "
           "types moves the case forward, real reasons hearings fail, their estimated minutes and gaps, their "
           "calendar, and their roster scaled with their own generator. Court day 10:30-12:30 and 13:30-17:00.")

c1, c2, c3, c4 = st.columns(4)
n_cases = c1.select_slider("Roster size", [100, 1000, 3000], 3000)
days = c2.select_slider("Working days", [20, 40, 60], 60)
capacity = c3.select_slider("Court minutes a day", [330, 420], 330,
                            help="330 = your timings; 420 = the 7-hour day in the organisers' README")
seeds = c4.select_slider("Random seeds averaged", [1, 3, 5], 3)


@st.cache_data(show_spinner="Running today's rules, Ready-to-List and each lever switched off")
def run(n_cases, days, capacity, seeds):
    from scripts.run_pucar import run as run_all
    return run_all(n_cases, days, capacity, seeds=seeds)


res, daily, sched = run(n_cases, days, capacity, seeds)
base, rtl = res.set_index("arm").loc["Today's rules"], res.set_index("arm").loc["Ready-to-List"]

h = st.columns(5)
spec = [("utilisation_pct", "Utilisation", "%", False), ("reach_rate_pct", "Reach rate", "%", False),
        ("substantiveness_pct", "Substantiveness", "%", False), ("backlog_4y_heard_pct", "4+ year cases heard", "%", False),
        ("predictability_days", "Days from listing to hearing", "", True)]
for col, (k, label, unit, lower_better) in zip(h, spec):
    col.metric(label, f"{rtl[k]:.0f}{unit}", f"{rtl[k] - base[k]:+.0f} vs today", delta_color="inverse" if lower_better else "normal")
g = st.columns(4)
g[0].metric("Substantive hearings a day", f"{rtl.substantive_per_day:.1f}", f"{rtl.substantive_per_day - base.substantive_per_day:+.1f}")
g[1].metric("Cases disposed", f"{rtl.disposed:.0f}", f"{rtl.disposed - base.disposed:+.0f}")
g[2].metric("Wasted listings", f"{rtl.wasted_listings:,.0f}", f"{rtl.wasted_listings - base.wasted_listings:+,.0f}", delta_color="inverse")
g[3].metric("Next-date gap, days", f"{rtl.next_date_gap_days:.0f}", f"vs {base.next_date_gap_days:.0f} flat", delta_color="off")

tabs = st.tabs(["Which lever does what", "Day by day", "Proposed cause list", "Why hearings fail (their data)"])

with tabs[0]:
    st.markdown("Each Ready-to-List lever switched off in turn, plus the two halves on their own. Without a pre-filing "
                "check, the readiness levers do its job: process tracking, the T-2 intent check, reading the last "
                "hearing's note, and fixed slots.")
    order = res.sort_values("substantive_per_day")
    colors = [BASE_COLOR if a == "Today's rules" else RTL_COLOR if a == "Ready-to-List" else "#9a9893" for a in order.arm]
    fig = go.Figure(go.Bar(x=order.substantive_per_day, y=order.arm, orientation="h", marker_color=colors,
                           text=order.substantive_per_day.round(1), textposition="outside",
                           hovertemplate="%{y}: %{x:.1f} a day<extra></extra>"))
    fig.update_layout(height=420, margin=dict(l=0, r=40, t=10, b=0), xaxis_title="Substantive hearings a day")
    st.plotly_chart(fig, width="stretch")
    st.dataframe(res.round(1), width="stretch", hide_index=True)

with tabs[1]:
    d = daily[daily.arm.isin(["Today's rules", "Ready-to-List"])]
    for metric, title in [("heard", "Substantive hearings a day"), ("listed", "Listed a day")]:
        fig = go.Figure()
        for arm, colr in [("Today's rules", BASE_COLOR), ("Ready-to-List", RTL_COLOR)]:
            s = d[d.arm == arm]
            fig.add_scatter(x=s.day, y=s[metric], name=arm, line=dict(color=colr, width=2))
        fig.update_layout(title=title, height=280, margin=dict(l=0, r=0, t=40, b=0), hovermode="x unified",
                          legend=dict(orientation="h", y=-0.25), xaxis_title="Working day")
        st.plotly_chart(fig, width="stretch")

with tabs[2]:
    dates = sorted(sched.date.unique())
    pick = st.selectbox("Date", dates)
    st.dataframe(sched[sched.date == pick], width="stretch", hide_index=True)
    st.caption("The simulated outcome column is one draw of what happened in the simulation, not a prediction.")

with tabs[3]:
    ref = E.load()["ref"]
    groups = list(E.CFG["reason_groups"])
    fail = (1 - ref.p_sub)
    fig = go.Figure()
    colors = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
    for g, colr in zip(groups, colors):
        fig.add_bar(y=ref.index, x=100 * fail * ref[f"share_{g}"], name=g.capitalize(), orientation="h",
                    marker_color=colr, hovertemplate="%{y}: %{x:.0f}% of hearings<extra>" + g + "</extra>")
    fig.add_bar(y=ref.index, x=100 * ref.p_sub, name="Substantive", orientation="h", marker_color="#c9c7c0")
    fig.update_layout(barmode="stack", height=480, margin=dict(l=0, r=0, t=10, b=0),
                      legend=dict(orientation="h", y=-0.12), xaxis_title="% of hearings of this type")
    st.plotly_chart(fig, width="stretch")
    st.caption("From their substantiveness and failure-reason files. Process (summons and warrants not back) is the "
               "biggest preventable cause: 67% of failed WARRANT hearings. Ready-to-List does not list a case until "
               "its process is back.")
