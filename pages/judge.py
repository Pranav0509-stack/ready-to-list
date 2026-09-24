import json
from datetime import datetime, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from core.config import BALANCE, HEARING_TYPES, LOCKED, capacity, to_min
from core.data import df, working_days
from core.readiness import case_frame
from core.scheduler import approve, build_causelist, impact, judge_config
from core.summary import summarise
from pages.common import VIA_COLORS, VIA_LABELS, resources, sidebar

conn, predictor = resources()
jid, day = sidebar()
base_cfg = judge_config(jid)

# Judge config overrides live in session state per court
cfg_key = f"cfg_{jid}"
if cfg_key not in st.session_state:
    st.session_state[cfg_key] = {"blocks": [dict(b) for b in base_cfg["blocks"]],
                                 "clustering": base_cfg["clustering"], "overbooking": base_cfg["overbooking"],
                                 "balance": base_cfg.get("balance", BALANCE),
                                 "fresh_first": base_cfg.get("fresh_first", False)}
overrides = st.session_state[cfg_key]

run_key = (jid, day.isoformat(), json.dumps(overrides, sort_keys=True))
if st.session_state.get("run_key") != run_key:
    with st.spinner("Building the cause list"):
        st.session_state.result = build_causelist(conn, predictor, jid, day, overrides)
    st.session_state.run_key = run_key
    st.session_state.add, st.session_state.remove = [], []
result = st.session_state.result

approved = conn.execute("SELECT COUNT(*) FROM causelists WHERE judge_id=? AND date=?", (jid, day.isoformat())).fetchone()[0]
st.title(f"{base_cfg['name']}, Court {base_cfg['court']}")
st.caption(f"{day:%A %d %B %Y}. The system recommends; you decide. "
           + (f"List approved with {approved} items." if approved else "This is a draft. Nothing is listed until you approve it."))

tab_list, tab_cfg, tab_health = st.tabs(["Today's list", "Config", "Docket health"])

# ---------------------------------------------------------------- Today's list
with tab_list:
    if result["items"].empty:
        st.info("No cases due on this date.")
        st.stop()
    add, remove = st.session_state.add, st.session_state.remove
    before, after, items, removed_old = impact(result, add=add, remove=remove)
    changed = bool(add or remove)
    m = result["meta"]

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Planned utilisation", f"{after['utilisation']}%",
              f"{after['utilisation'] - before['utilisation']:+} pts" if changed else None, delta_color="off")
    k2.metric("Predicted heard", f"{after['predicted_heard']} of {after['listed']}",
              help="Sum of P(show) from the model, both sides present")
    k3.metric("5+ year cases listed", after["old_listed"],
              f"{after['old_listed'] - before['old_listed']:+}" if changed else None)
    k4.metric("Unready listings held back", m["held_back"],
              help="Due today but readiness below the gate. Under today's rules these would be listed and adjourned. "
                   "They move to the date their prerequisites are done.")

    left, right = st.columns([2.2, 1])
    with left:
        base_day = datetime.combine(day, datetime.min.time())
        t = items.copy()
        t["Start"] = [base_day + timedelta(minutes=to_min(s)) for s in t.exp_start]
        t["Finish"] = t.Start + pd.to_timedelta(t.expected_minutes.clip(lower=2), unit="m")
        t["Type"] = t.via.map(VIA_LABELS)
        t["Case"] = t.title
        fig = px.timeline(t, x_start="Start", x_end="Finish", y="block", color="Type",
                          color_discrete_map={VIA_LABELS[k]: v for k, v in VIA_COLORS.items()},
                          hover_data={"Case": True, "next_purpose": True, "readiness": True, "pet_name": True,
                                      "age_years": ":.1f", "p_show": True, "slot_start": True, "slot_end": True,
                                      "reason": True, "Start": False, "Finish": False, "block": False, "Type": False},
                          labels={"next_purpose": "Purpose", "pet_name": "Counsel", "age_years": "Age (yrs)",
                                  "p_show": "P(show)", "slot_start": "Slot from", "slot_end": "to", "reason": "Why listed",
                                  "block": ""})
        fig.update_traces(marker_line_width=2, marker_line_color="rgba(255,255,255,0.9)")
        for b in result["cfg"]["blocks"]:
            fig.add_vline(x=base_day + timedelta(minutes=to_min(b["end"])), line_dash="dot", line_width=1,
                          line_color="#9a9893")
        fig.update_layout(height=230, margin=dict(l=0, r=0, t=10, b=0), legend_title_text="",
                          legend=dict(orientation="h", y=-0.35), xaxis_title=None)
        fig.update_xaxes(tickformat="%H:%M")
        st.plotly_chart(fig, width="stretch")
        st.caption("Each bar is one case at its expected start. Hover a bar for the reason it was listed. "
                   "Dotted lines mark block ends.")

        view = items[["seq", "exp_start", "slot_start", "slot_end", "title", "next_purpose", "readiness",
                      "pet_name", "age_years", "p_show", "via", "reason"]].rename(columns={
            "seq": "#", "exp_start": "Expected", "slot_start": "Slot from", "slot_end": "Slot to", "title": "Case",
            "next_purpose": "Purpose", "readiness": "Readiness", "pet_name": "Counsel", "age_years": "Age (yrs)",
            "p_show": "P(show)", "via": "Via", "reason": "Why listed"})
        view["Via"] = view["Via"].map(VIA_LABELS)
        st.dataframe(view, hide_index=True, width="stretch", height=360,
                     column_config={"Readiness": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d"),
                                    "Age (yrs)": st.column_config.NumberColumn(format="%.1f"),
                                    "P(show)": st.column_config.NumberColumn(format="%.2f")})

    with right:
        st.subheader("Override")
        pool = result["pool"]
        listed_ids = set(items.id)
        label = lambda cid: f"{cid} · {pool.set_index('id').title.get(cid, cid)[:48]}"
        wait_ids = [c for c in result["waitlist"].id.tolist() + pool.sort_values("priority", ascending=False).id.tolist()
                    if c not in result["items"].id.values]
        wait_ids = list(dict.fromkeys(wait_ids))
        new_add = st.multiselect("Add a case (waitlist first)", wait_ids, default=add, format_func=label)
        new_remove = st.multiselect("Remove a case", result["items"].id.tolist(), default=remove, format_func=label)
        if new_add != add or new_remove != remove:
            st.session_state.add, st.session_state.remove = new_add, new_remove
            st.rerun()

        st.markdown("**Impact meter**")
        over = after["utilisation"] > 100
        c1, c2 = st.columns(2)
        d = lambda k, unit="": f"{after[k] - before[k]:+}{unit}" if changed else None
        c1.metric("Utilisation", f"{after['utilisation']}%", d("utilisation", " pts"), delta_color="inverse")
        c2.metric("Predictability", f"{after['predictability']}%", d("predictability", " pts"))
        c1.metric("Predicted effective", after["predicted_effective"], d("predicted_effective"))
        c2.metric("5+ yr listed", after["old_listed"], d("old_listed"))
        if over:
            st.error(f"Overbooked: {after['utilisation']}% of court time. Expect cases to be sent back at the end "
                     "of the day.")
        if removed_old:
            st.warning(f"Removing {removed_old} case(s) over 5 years old: 5+ year backlog +{removed_old} this week.")
        if not changed:
            st.caption("Add or remove a case to see the impact before you approve.")

        if st.button("Approve list", type="primary", width="stretch"):
            approve(conn, jid, day, items, removed=remove, judge_name=base_cfg["name"])
            st.success(f"Approved {len(items)} items. Litigants notified with their time windows.")
            st.rerun()

        st.divider()
        st.subheader("Case drawer")
        drawer_opts = items.sort_values("age_years", ascending=False).id.tolist()
        if "D0007" not in drawer_opts:
            drawer_opts.insert(0, "D0007")
        cid = st.selectbox("Open case", drawer_opts, format_func=lambda c: f"{c} · {pool.set_index('id').title.get(c, c)[:40]}"
                           if c in pool.id.values else c)
        s = summarise(conn, cid)
        with st.container(border=True):
            st.markdown(f"**{s['title']}**  \nFiled {s['filed']}, now at **{s['stage']}**")
            st.caption("AI-assisted summary, verified by both counsel" if s["verified"]
                       else "AI-assisted summary, awaiting verification by counsel")
            st.markdown("**Agreed facts**\n" + "\n".join(f"- {f}" for f in s["agreed_facts"]))
            st.markdown("**Open issues**\n" + "\n".join(f"- {f}" for f in s["open_issues"]))
            st.markdown(f"**Counsel:** " + ", ".join(f"{r.counsel} ({r.side})" for r in s["parties"].itertuples()))
            if not s["events"].empty:
                st.markdown(f"**Key events.** {s['adjournments']} of {len(s['events'])} hearings were not effective.")
                st.dataframe(s["events"], hide_index=True, width="stretch", height=150)

# ---------------------------------------------------------------- Config
with tab_cfg:
    st.subheader("Bench configuration")
    st.caption("Changes rebuild today's draft list.")
    new = {k: v for k, v in overrides.items()}
    new["blocks"] = []
    for i, b in enumerate(overrides["blocks"]):
        c1, c2, c3 = st.columns([2, 1, 1])
        c1.markdown(f"**{b['name']}**  \n{', '.join(b['purposes'])}")
        s_ = c2.time_input("Start", datetime.strptime(b["start"], "%H:%M").time(), key=f"{jid}_s{i}", step=900)
        e_ = c3.time_input("End", datetime.strptime(b["end"], "%H:%M").time(), key=f"{jid}_e{i}", step=900)
        new["blocks"].append({**b, "start": s_.strftime("%H:%M"), "end": e_.strftime("%H:%M")})
    new["clustering"] = st.toggle("Cluster each advocate's matters into one window", overrides["clustering"])
    new["overbooking"] = st.slider("Overbooking above the 95% fill target", 0.0, 0.3, float(overrides["overbooking"]), 0.05)
    new["balance"] = st.slider("Throughput or disposal", 0.0, 0.5, float(overrides["balance"]), 0.05,
                               help="0 hears final arguments and old cases first. 0.5 packs in the most short hearings. "
                                    f"Default {BALANCE}: it beat today's rules on every criterion in the simulator.")
    new["fresh_first"] = st.toggle("Fresh matters first", overrides["fresh_first"],
                                   help="Adds priority to cases under a year old. The ageing quota still applies.")
    if new["fresh_first"]:
        st.info("Fresh matters first is on. The locked ageing quota still reserves "
                f"{int(LOCKED['ageing_quota'] * 100)}% of the day for 5+ year cases, so they are not pushed out. "
                "Open the simulator to see the effect on your 4+ and 5+ year backlog.")
    if base_cfg.get("cover_sheet_required"):
        st.caption("Cover sheet required: arguments in a 4+ year case are listed only once both counsel verify the summary.")

    st.markdown("**Protected rules**")
    lock = "Protected: cannot be disabled."
    st.toggle(f":material/lock: Ageing quota: {int(LOCKED['ageing_quota'] * 100)}% of daily capacity to 5+ year cases",
              True, disabled=True, help=lock)
    st.toggle(":material/lock: Urgent bypass: bail, habeas corpus and stay always listed", True, disabled=True, help=lock)
    st.toggle(f":material/lock: Readiness gate at {LOCKED['readiness_gate']} (urgent and quota cases exempt)", True, disabled=True,
              help=lock)
    if new != overrides:
        st.session_state[cfg_key] = new
        st.rerun()

    st.markdown("**Hearing-type reference table**")
    st.dataframe(pd.DataFrame(HEARING_TYPES).T.rename_axis("purpose").reset_index(), hide_index=True)
    with st.expander("What the prediction model learned"):
        st.caption(f"Logistic regression on {predictor.n_train:,} past hearings.")
        st.dataframe(predictor.coefficients(), hide_index=True)

# ---------------------------------------------------------------- Docket health
with tab_health:
    cf = case_frame(conn, jid, on=day)
    c1, c2 = st.columns(2)
    with c1:
        bins = pd.cut(cf.age_years, [0, 1, 3, 5, 10, 100], labels=["<1 yr", "1-3 yrs", "3-5 yrs", "5-10 yrs", "10+ yrs"])
        ag = bins.value_counts().sort_index().reset_index()
        ag.columns = ["Age", "Cases"]
        fig = px.bar(ag, x="Age", y="Cases", text="Cases", color_discrete_sequence=["#2a78d6"])
        fig.update_layout(height=280, margin=dict(l=0, r=0, t=30, b=0), title="Pending cases by age")
        st.plotly_chart(fig, width="stretch")
    with c2:
        wd = working_days(day, 20)
        due_counts = df(conn, "SELECT next_date, next_purpose FROM cases WHERE judge_id=? AND status='pending'", (jid,))
        due_counts["mins"] = due_counts.next_purpose.map(lambda p: HEARING_TYPES[p]["duration"] * 0.7)
        load = due_counts.groupby("next_date").mins.sum()
        fc = pd.DataFrame({"date": wd, "minutes": [load.get(d.isoformat(), 0) for d in wd]})
        cap = capacity(jid)
        fig = go.Figure(go.Bar(x=fc.date, y=fc.minutes, marker_color="#2a78d6", name="Expected minutes",
                               hovertemplate="%{x|%d %b}: %{y:.0f} min<extra></extra>"))
        fig.add_hline(y=cap, line_dash="dash", line_color="#9a9893", annotation_text=f"Capacity {cap} min")
        fig.update_layout(height=280, margin=dict(l=0, r=0, t=30, b=0), title="4-week load forecast", showlegend=False)
        st.plotly_chart(fig, width="stretch")

    hist = df(conn, """SELECT h.case_id, c.title, c.next_purpose,
                              SUM(h.outcome<>'effective') adjournments, COUNT(*) hearings
                       FROM hearings h JOIN cases c ON c.id=h.case_id
                       WHERE c.judge_id=? AND c.status='pending' GROUP BY h.case_id""", (jid,))
    hist["Counter"] = pd.cut(hist.adjournments, [-1, 1, 3, 100], labels=["Green: 0-1", "Amber: 2-3", "Red: 4+"])
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Repeat adjournments**")
        st.dataframe(hist.sort_values("adjournments", ascending=False).head(15)[
                         ["Counter", "case_id", "title", "adjournments", "hearings"]],
                     hide_index=True, width="stretch")
    with c2:
        st.markdown("**Stuck at one stage** (3+ hearings at the current purpose)")
        stuck = df(conn, """SELECT h.case_id, c.title, c.next_purpose stage, COUNT(*) hearings_at_stage
                            FROM hearings h JOIN cases c ON c.id=h.case_id AND h.purpose=c.next_purpose
                            WHERE c.judge_id=? AND c.status='pending' GROUP BY h.case_id
                            HAVING COUNT(*)>=3 ORDER BY hearings_at_stage DESC LIMIT 15""", (jid,))
        st.dataframe(stuck, hide_index=True, width="stretch")
