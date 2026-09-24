import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.config import OPTIMIZER, ROOT
from core.optimize import build_instance, evaluate, solve
from pages.common import resources, sidebar

conn, predictor = resources()
jid, day = sidebar()

st.title("Optimisation lab")
st.caption("One model of the court, solved four ways. Stage 1 picks the day for every case across all three "
           "courts; stage 2 (constraint programming) sets the exact time. Every weight lives in "
           "config/optimizer.yaml; nothing here is hardcoded.")

METHODS = {"greedy": "Greedy (priority fill)", "milp": "MILP (SCIP)", "cpsat": "CP-SAT",
           "cpsat_saa": "CP-SAT, stochastic (SAA)"}
METRICS = [("effective", "Effective hearings", "Hearings that move a case forward, expected over 200 simulated weeks"),
           ("predictability_pct", "Predictability %", "Listed cases that are actually heard"),
           ("wasted_listings", "Wasted listings", "Listings that do not move the case: trips for nothing"),
           ("utilisation_pct", "Utilisation %", "Court minutes used"),
           ("overtime_min_per_court_day", "Overtime, min per court-day", "Minutes past the block end"),
           ("clash_days", "Advocate clashes", "Advocate-days listed in two courtrooms"),
           ("advocate_trips", "Advocate trips", "Days an advocate must attend"),
           ("load_spread_pct", "Load spread %", "Busiest minus quietest court-day, planned utilisation"),
           ("old_cases_listed", "5+ year cases listed", "Fairness to the oldest cases"),
           ("due_left_unlisted", "Due but unlisted", "Cases due in the horizon left out")]

tab_run, tab_cmp, tab_why = st.tabs(["Run one plan", "Every combination", "Which method, when"])

with tab_run:
    c1, c2, c3, c4 = st.columns(4)
    method = c1.selectbox("Method", list(METHODS), index=list(METHODS).index(OPTIMIZER["method"]),
                          format_func=METHODS.get)
    prefiling = c2.toggle("Pre-filing check", True, help="Off: critical defects surface only in court")
    horizon = c3.select_slider("Working days", [3, 5, 10], OPTIMIZER["horizon_working_days"]
                               if OPTIMIZER["horizon_working_days"] in (3, 5, 10) else 5)
    limit = c4.select_slider("Time limit, s", [5, 10, 20, 30], 10)
    profile = st.segmented_control("Objective profile", list(OPTIMIZER["profiles"]), default="balanced")
    base = {**OPTIMIZER["weights"], **OPTIMIZER["profiles"].get(profile or "balanced", {})}
    with st.expander("Objective weights"):
        cols = st.columns(3)
        weights = {k: cols[n % 3].number_input(k, value=float(v), step=0.5, key=f"w_{k}_{profile}")
                   for n, (k, v) in enumerate(base.items())}
    if st.button("Solve", type="primary"):
        with st.spinner(f"Solving with {METHODS[method]}"):
            inst = build_instance(conn, predictor, day, horizon=horizon, prefiling=prefiling)
            plan, info = solve(inst, method, weights=weights, time_limit=limit)
            st.session_state.lab = {"info": info, "ev": evaluate(inst, plan), "n": len(inst.cases),
                                    "x": sum(len(v) for v in inst.feasible.values())}
    lab = st.session_state.get("lab")
    if lab:
        i = lab["info"]
        st.caption(f"{METHODS[i['method']]}: {i['status']}, objective {i['objective']}, "
                   + (f"best bound {i['bound']}, gap {i.get('gap_pct')}%, " if i.get("bound") is not None else "")
                   + f"{i['seconds']} s. {lab['n']:,} candidate cases, {lab['x']:,} day decisions.")
        cols = st.columns(5)
        for n, (k, label, help_) in enumerate(METRICS):
            cols[n % 5].metric(label, f"{lab['ev'][k]:,.1f}" if isinstance(lab["ev"][k], float) else lab["ev"][k],
                               help=help_)

with tab_cmp:
    path = ROOT / "data" / "benchmark.csv"
    if not path.exists():
        st.info("No benchmark yet. Run `.venv/bin/python -m scripts.benchmark` (about 10 minutes).")
    else:
        b = pd.read_csv(path)
        st.caption(f"{len(b)} runs: pre-filing on/off x {b.method.nunique()} methods x {b.profile.nunique()} "
                   f"objective profiles, {int(b.horizon.iloc[0])} working days, 3 courts, "
                   f"{int(b.time_limit.iloc[0])} s per solve. Each plan is scored on 200 simulated weeks.")
        metric = st.selectbox("Measure", [m[0] for m in METRICS], format_func=dict((m[0], m[1]) for m in METRICS).get)
        prof = st.segmented_control("Profile", sorted(b.profile.unique()), default="balanced", key="cmp_prof")
        sub = b[b.profile == (prof or "balanced")]
        fig = go.Figure()
        for pf, color, name in [(True, "#2a78d6", "Pre-filing check on"), (False, "#eb6834", "Pre-filing check off")]:
            s = sub[sub.prefiling == pf].set_index("method").reindex(list(METHODS))
            fig.add_bar(x=[METHODS[m] for m in s.index], y=s[metric], name=name, marker_color=color,
                        hovertemplate="%{x}: %{y:.1f}<extra>" + name + "</extra>")
        fig.update_layout(barmode="group", height=340, margin=dict(l=0, r=0, t=10, b=0),
                          legend=dict(orientation="h", y=-0.2), yaxis_title=dict((m[0], m[1]) for m in METRICS)[metric])
        st.plotly_chart(fig, width="stretch")
        show = b[["prefiling", "profile", "method", "status", "gap_pct", "seconds"] + [m[0] for m in METRICS]]
        st.dataframe(show.round(1), width="stretch", hide_index=True)

with tab_why:
    st.markdown("""
#### What we optimise

| Goal (manual) | Term in the objective | Hard rule or weight |
|---|---|---|
| Throughput and substantiveness | priority x P(effective) per listing | weight `throughput` |
| Wasted listings, predictability | 1 - P(show) per listing | weight `wasted` |
| Judge time used | idle minutes; blocks filled to 95% of expected minutes | weight `idle`, cap in constraints |
| Fairness | priority x days waited; the ageing quota for 5+ year cases | weight `wait`; quota is **locked** |
| Urgent matters | heard within 2 working days | **locked** constraint |
| Advocate trips and clashes | days attended; two courtrooms on one day | weights `trips`, `clash` |
| Load balance | busiest minus quietest court-day | weight `balance` |
| Overtime (stochastic only) | expected minutes past block end over sampled futures | weight `overtime` |

#### Which method, when

| Situation | Best method | Why |
|---|---|---|
| Choosing the day (assignment, knapsack) | **CP-SAT or MILP**, they tie | Linear objective and capacity rows: MILP's LP bound and CP-SAT's search both close the gap to under 1% here |
| Setting the time inside a day, across courtrooms | **CP-SAT** | No-overlap per room and per advocate with travel time is native to CP (interval variables); a MILP needs big-M pairs with a weak relaxation |
| Show-ups very uncertain, overtime costly | **Stochastic CP-SAT (SAA)** | Plans against sampled futures: cut overtime from 18 to 8 min per court-day in our high-noise test, for 3% fewer effective hearings |
| Pre-filing check off | **Fix the information, not the solver** | Hidden defects are case-specific; every method lost about 12% of effective hearings and none recovered it |
| Live re-planning on the day | **Greedy + waitlist** | Milliseconds; good enough for filling a freed slot |

We use a two-stage hybrid: CP-SAT (MILP as a cross-check) picks the day over a rolling 10-day horizon every evening,
CP-SAT sequences each day, and greedy fills same-day gaps from the waitlist.

Evidence: CP beats MIP on larger scheduling (sequencing) instances ([Ku and Beck 2016](https://tidel.mie.utoronto.ca/pubs/JSP_CandOR_2016.pdf));
two-stage stochastic programming is the standard for appointments with no-shows ([Denton and Gupta 2003](https://experts.umn.edu/en/publications/a-sequential-bounding-approach-for-optimal-appointment-scheduling/));
CP-SAT's optional intervals and NoOverlap ([OR-Tools docs](https://github.com/google/or-tools/blob/stable/ortools/sat/docs/scheduling.md)).
""")
