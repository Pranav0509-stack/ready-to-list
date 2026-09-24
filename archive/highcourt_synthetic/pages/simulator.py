import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.config import BALANCE
from core.simulate import STYLES, run, summary
from pages.common import BASE_COLOR, RTL_COLOR

st.title("Simulator")
st.caption("One judge's roster of 3,000 cases over 60 working days (about 3 months). Same cases, same advocates, "
           "same random seed. Today's rules list about 60 a day with a flat 60-day next date. Ready-to-List runs "
           "the full flow. Each advocate is an agent that decides whether to confirm, prepare and turn up.")

c1, c2, c3, c4 = st.columns(4)
style = c1.selectbox("Judge", list(STYLES), help="Sehgal: weekly carry-over. Dimakar: arbitration, cover sheets "
                                                 "mandatory. Joshi: fresh matters first.")
dil = c2.slider("Diligent advocates", 0.0, 1.0, 0.4, 0.05)
chronic = c3.slider("Chronic adjourners", 0.0, 1.0 - dil, min(0.2, 1.0 - dil), 0.05)
overbook = c4.slider("Overbooking", 0.0, 0.3, 0.0, 0.05, help="Planned minutes above the 95% fill target")
c5, c6 = st.columns([3, 1])
balance = c5.slider("Throughput or disposal", 0.0, 0.5, float(BALANCE), 0.05,
                    help="0 hears final arguments and old cases first. 0.5 packs in the most short hearings. "
                         f"Default {BALANCE}.")
days = c6.select_slider("Working days", [20, 40, 60, 90], 60)


@st.cache_data(show_spinner="Running both courts")
def both(style, dil, chronic, overbook, days, balance):
    b, ba = run(days, style, False, dil, chronic, overbook)
    r, ra = run(days, style, True, dil, chronic, overbook, balance=balance)
    return b, ba, r, ra


b, ba, r, ra = both(style, dil, chronic, overbook, days, balance)
s = summary(b, r, ba, ra)

h1, h2, h3, h4 = st.columns(4)
h1.metric("Effective hearings a day", f"{s['effective_per_day'][1]:.1f}",
          f"{s['effective_per_day'][2]:+.0f}% vs {s['effective_per_day'][0]:.1f} today")
h2.metric("Cases disposed", f"{s['disposed'][1]:.0f}", f"{s['disposed'][1] - s['disposed'][0]:+.0f} vs today")
h3.metric("Wasted advocate trips", f"{s['wasted_trips'][1]:,.0f}",
          f"{s['wasted_trips'][2]:+.0f}% vs {s['wasted_trips'][0]:,.0f}", delta_color="inverse")
h4.metric("Change in 5+ year cases", f"{s['backlog_5y_change'][1]:+.0f}",
          f"today's rules {s['backlog_5y_change'][0]:+.0f}", delta_color="off", delta_arrow="off")

st.markdown("#### The five judging criteria")
crit = pd.DataFrame([
    ["Utilisation", "Court time used", f"{s['utilisation'][0]:.0f}%", f"{s['utilisation'][1]:.0f}%"],
    ["Predictability", "Listed cases actually heard", f"{s['predictability'][0]:.0f}%", f"{s['predictability'][1]:.0f}%"],
    ["Substantiveness", "Heard cases that moved forward", f"{s['substantiveness'][0]:.0f}%", f"{s['substantiveness'][1]:.0f}%"],
    ["Backlog-age impact", "Change in 5+ year cases", f"{s['backlog_5y_change'][0]:+.0f}", f"{s['backlog_5y_change'][1]:+.0f}"],
    ["Backlog-age impact", "Change in 4+ year cases", f"{s['backlog_4y_change'][0]:+.0f}", f"{s['backlog_4y_change'][1]:+.0f}"],
    ["Next date", "Average gap, calendar days", f"{s['next_date_gap'][0]:.0f}", f"{s['next_date_gap'][1]:.0f}"],
    ["Daily list", "Listed / heard / effective", f"{s['listed_per_day'][0]:.0f} / {s['heard_per_day'][0]:.0f} / {s['effective_per_day'][0]:.0f}",
     f"{s['listed_per_day'][1]:.0f} / {s['heard_per_day'][1]:.0f} / {s['effective_per_day'][1]:.0f}"],
], columns=["Criterion", "Measure", "Today's rules", "Ready-to-List"])
st.dataframe(crit, hide_index=True, width="stretch")


def line(metric, title, y_title):
    fig = go.Figure()
    fig.add_scatter(x=b.day, y=b[metric], name="Today's rules", line=dict(color=BASE_COLOR, width=2))
    fig.add_scatter(x=r.day, y=r[metric], name="Ready-to-List", line=dict(color=RTL_COLOR, width=2))
    fig.update_layout(title=title, height=300, margin=dict(l=0, r=0, t=40, b=0), hovermode="x unified",
                      legend=dict(orientation="h", y=-0.2), xaxis_title="Working day", yaxis_title=y_title)
    return fig


g1, g2 = st.columns(2)
g1.plotly_chart(line("effective", "Effective hearings per day", "Hearings"), width="stretch")
bucket = g2.segmented_control("Backlog bucket", ["3+ years", "4+ years", "5+ years"], default="5+ years",
                              label_visibility="collapsed")
col = {"3+ years": "backlog_3y", "4+ years": "backlog_4y", "5+ years": "backlog_5y"}[bucket or "5+ years"]
g2.plotly_chart(line(col, f"Pending cases {bucket or '5+ years'} old", "Cases"), width="stretch")
g3, g4 = st.columns(2)
g3.plotly_chart(line("predictability", "Predictability: listed cases actually heard", "%"), width="stretch")

trips = pd.concat([ba.assign(arm="Today's rules"), ra.assign(arm="Ready-to-List")])
tt = trips.groupby(["arm", "type"])[["wasted"]].mean().reset_index()
fig = go.Figure()
for arm, color in [("Today's rules", BASE_COLOR), ("Ready-to-List", RTL_COLOR)]:
    d = tt[tt.arm == arm].set_index("type").reindex(["diligent", "busy", "chronic"])
    fig.add_bar(x=d.index, y=d.wasted, name=arm, marker_color=color,
                hovertemplate="%{x}: %{y:.1f} wasted trips<extra>" + arm + "</extra>")
fig.update_layout(title="Wasted trips per advocate, by type", barmode="group", height=300,
                  margin=dict(l=0, r=0, t=40, b=0), legend=dict(orientation="h", y=-0.2), yaxis_title="Trips")
g4.plotly_chart(fig, width="stretch")

st.caption("Ready-to-List lists fewer cases because it stops listing hearings that were never going to happen. "
           "Unready cases move to the date their prerequisites are done, so nobody makes the trip for nothing.")
