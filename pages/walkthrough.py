"""The whole flow on one page, on a dummy filing: pre-filing check, re-upload, planning,
the judge's approval, the hearing, and the next date."""
import pandas as pd
import plotly.express as px
import streamlit as st

from core.config import JUDGES, OPTIMIZER, ROOT, next_stage
from core.data import df
from core.defects import check_filing, queue_position, refile, route_judge, submit_filing
from core.nextdate import confirm_next_date, next_date, record_outcome
from core.optimize import explain, plan_horizon
from core.scheduler import approve
from pages.common import VIA_COLORS, VIA_LABELS, resources, sidebar

conn, predictor = resources()
jid, day = sidebar()
SAMPLES = ROOT / "samples"
W = st.session_state.setdefault("walk", {})

st.title("Full flow, one case")
st.caption("Follow one writ petition from filing to its next date. Each step calls the same services the other "
           "screens use, on the demo court. Press Reset demo data in the sidebar to start again.")

steps = ["1. File", "2. Fix and re-upload", "3. Plan the fortnight", "4. Judge approves", "5. Hearing", "6. Next date"]
done = W.get("step", 0)
st.progress(done / len(steps), text=f"Step {min(done + 1, len(steps))} of {len(steps)}: {steps[min(done, len(steps) - 1)]}")

# ---------------------------------------------------------------- 1. file the defective petition
with st.container(border=True):
    st.markdown("#### 1. The advocate files a writ petition")
    c1, c2 = st.columns([1, 2])
    sample = c1.selectbox("Dummy filing", sorted(p.name for p in SAMPLES.glob("*.pdf")),
                          index=sorted(p.name for p in SAMPLES.glob("*.pdf")).index("02_wpc_defective.pdf"))
    res = check_filing(SAMPLES / sample, filed_on=day)
    target = route_judge(res["case_type"], jid)
    purpose = "bail" if res["case_type"] == "Bail Appl." else "admission"
    q = queue_position(conn, target, res["case_type"], purpose, res["filing_score"], purpose == "bail", day,
                       res.get("score_if_fixed"))
    c1.metric("Filing score", f"{res['filing_score']:.0f}")
    c1.caption(f"{res['pages']} pages, {res['case_type']}, goes to {JUDGES[target]['name']}.")
    for d in res["defects"]:
        color = {"critical": "red", "major": "orange"}.get(d["class"], "gray")
        c2.markdown(f":{color}[{d['class'].capitalize()}], page {d['page']}: **{d['title']}**. {d['message']}  \n"
                    f":gray[Fix: {d['fix']}]")
    if not res["defects"]:
        c2.markdown(":green[:material/check_circle:] No defects found.")
    a, b = c2.columns(2)
    a.metric("Submit now", f"#{q['as_filed']['rank']}", f"listing {q['as_filed']['date']:%d %b}", delta_color="off")
    b.metric("Fix critical defects first", f"#{q['if_fixed']['rank']}", f"listing {q['if_fixed']['date']:%d %b}",
             delta_color="off")
    if done == 0 and st.button("Submit anyway", help="Never disabled: a defect costs queue position, never access"):
        W["case"] = submit_filing(conn, {"name": sample}, res, target, res["case_type"], purpose, True, day)
        W["judge"], W["step"] = target, 1
        st.rerun()
    if W.get("case"):
        st.success(f"Filed as {W['case']} with the defect memo attached. Nobody was turned away.")

# ---------------------------------------------------------------- 2. re-upload the fixed version
if done >= 1:
    with st.container(border=True):
        st.markdown("#### 2. That evening the advocate fixes the critical defects and re-uploads")
        fixed = st.selectbox("Fixed version", sorted(p.name for p in SAMPLES.glob("*.pdf")),
                             index=sorted(p.name for p in SAMPLES.glob("*.pdf")).index("03_wpc_fixed.pdf"))
        if done == 1 and st.button("Re-upload fixed version", type="primary"):
            W["refile"] = refile(conn, W["case"], check_filing(SAMPLES / fixed, filed_on=day), on=day,
                                 pdf_meta={"name": fixed})
            W["step"] = 2
            st.rerun()
        if W.get("refile"):
            r = W["refile"]
            st.markdown(f"Version {r['version']}: {r['fixed']} defects marked fixed, score {r['filing_score']:.0f}. "
                        f"Queue #{r['rank']}, likely listing {r['date']:%d %b}.")

# ---------------------------------------------------------------- 3. plan
if done >= 2:
    with st.container(border=True):
        st.markdown("#### 3. Overnight, the planner picks a day and a time for every case in all three courts")
        st.caption(f"Stage 1 decides the day with {OPTIMIZER['method'].upper()} over "
                   f"{OPTIMIZER['horizon_working_days']} working days. Stage 2 uses CP-SAT to set times so no "
                   "advocate is due in two courtrooms at once.")
        if done == 2 and st.button("Run the planner", type="primary"):
            with st.spinner("Planning 3 courts over the horizon (about a minute)"):
                W["plan"] = plan_horizon(conn, predictor, day, time_limit=15)
            W["step"] = 3
            st.rerun()
        if W.get("plan"):
            seq = W["plan"]["seq"]
            mine = seq[seq.id == W["case"]]
            i = W["plan"]["info"]
            st.caption(f"{i['method']}: {i['status']}, gap {i.get('gap_pct')}%, {i['seconds']} s; "
                       f"{len(seq):,} hearings placed.")
            if mine.empty:
                st.warning(f"{W['case']} is not in this horizon: higher-priority matters fill it. It stays in the "
                           "queue and is planned in a later run.")
            else:
                r = mine.iloc[0]
                W["date"] = r.date
                st.markdown(f"**{W['case']} is listed on {r.date:%A %d %b}, {r.window}, Court "
                            f"{JUDGES[r.judge_id]['court']}**, expected at {r.start}.")
                reasons = dict(zip(W["plan"]["plan"].id, explain(W["plan"]["inst"], W["plan"]["plan"])))
                st.caption("Why this day: " + reasons.get(W["case"], ""))
                dayv = seq[seq.date == r.date].copy()
                dayv["Type"] = ["urgent" if u else "quota" if o else "ready" for u, o in zip(dayv.urgent, dayv.old)]
                dayv["Type"] = dayv.Type.map(VIA_LABELS)
                dayv.loc[dayv.id == W["case"], "Type"] = "This case"
                base = pd.Timestamp(r.date)
                dayv["Start"] = base + pd.to_timedelta(dayv.start_min, unit="m")
                dayv["Finish"] = base + pd.to_timedelta(dayv.end_min, unit="m")
                dayv["Court"] = dayv.judge_id.map(lambda j: f"Court {JUDGES[j]['court']}")
                cmap = {VIA_LABELS[k]: v for k, v in VIA_COLORS.items()} | {"This case": "#e34948"}
                fig = px.timeline(dayv, x_start="Start", x_end="Finish", y="Court", color="Type",
                                  color_discrete_map=cmap, hover_data={"title": True, "window": True,
                                                                        "Start": False, "Finish": False})
                fig.update_layout(height=240, margin=dict(l=0, r=0, t=10, b=0), legend_title_text="",
                                  legend=dict(orientation="h", y=-0.3), xaxis_title=None, yaxis_title=None)
                fig.update_xaxes(tickformat="%H:%M")
                st.plotly_chart(fig, width="stretch")

# ---------------------------------------------------------------- 4. judge approves
if done >= 3 and W.get("date") is not None:
    with st.container(border=True):
        judge = W["judge"]
        st.markdown(f"#### 4. {JUDGES[judge]['name']} reviews and approves the list for {W['date']:%d %b}")
        seq = W["plan"]["seq"]
        items = seq[(seq.date == W["date"]) & (seq.judge_id == judge)].copy().reset_index(drop=True)
        items["seq"] = range(1, len(items) + 1)
        items["slot_start"] = items.window.str[:5]
        items["slot_end"] = items.window.str[-5:]
        items["exp_start"] = items.start
        items["p_effective"] = items.p_eff_plan
        items["via"] = ["urgent" if u else "quota" if o else "ready" for u, o in zip(items.urgent, items.old)]
        items["reason"] = [f"Planned by the optimiser ({W['plan']['info']['method']})."] * len(items)
        st.caption(f"{len(items)} matters, {items.expected_minutes.sum():.0f} expected minutes.")
        if done == 3 and st.button("Approve list", type="primary"):
            approve(conn, judge, W["date"], items, judge_name=JUDGES[judge]["name"])
            W["step"] = 4
            st.rerun()
        if done >= 4:
            note = df(conn, "SELECT message FROM notifications WHERE case_id=? AND actor='litigant' "
                            "ORDER BY rowid DESC LIMIT 1", (W["case"],))
            if not note.empty:
                st.markdown(f"Litigant's WhatsApp: *\"{note.message.iloc[0]}\"*")

# ---------------------------------------------------------------- 5. the hearing
if done >= 4:
    with st.container(border=True):
        st.markdown("#### 5. On the day, the court master marks the outcome with one tap")
        c1, c2, c3 = st.columns(3)
        pick = None
        if done == 4:
            if c1.button("Effective", icon=":material/check_circle:", type="primary"):
                pick = ("effective", None)
            if c2.button("Heard, not effective", icon=":material/pending:"):
                pick = ("heard_not_effective", "not prepared")
            if c3.button("Adjourned: counsel absent", icon=":material/event_busy:"):
                pick = ("adjourned", "counsel absent")
        if pick:
            record_outcome(conn, W["judge"], W["date"], W["case"], *pick)
            purpose = conn.execute("SELECT next_purpose FROM cases WHERE id=?", (W["case"],)).fetchone()[0]
            nxt = next_stage(purpose) if pick[0] == "effective" else purpose
            W["outcome"], W["rec"] = pick, next_date(conn, W["case"], pick[0], nxt, W["date"], pick[1])
            W["step"] = 5
            st.rerun()
        if W.get("outcome"):
            st.markdown(f"Marked **{W['outcome'][0].replace('_', ' ')}**.")

# ---------------------------------------------------------------- 6. next date
if done >= 5:
    with st.container(border=True):
        rec = W["rec"]
        st.markdown(f"#### 6. The next date: {rec['date']:%A %d %b}, {rec['slot']}, for {rec['purpose']}")
        st.caption(f"{rec['gap_days']} days out instead of a flat 60. Why: {rec['reason']}.")
        if rec["tasks"]:
            st.markdown("Tasks sent to both sides: " + "; ".join(rec["tasks"]))
        if done == 5 and st.button("Confirm next date", type="primary"):
            confirm_next_date(conn, W["case"], rec, W["date"])
            W["step"] = 6
            st.rerun()
        if done >= 6:
            st.success("Done. The outcome is in the audit log, the advocate's show rate is updated, and the case "
                       "goes back into tomorrow night's plan.")
            st.dataframe(df(conn, "SELECT ts, service, decision, reason, overridden_by FROM audit_log "
                                  "WHERE case_id=? ORDER BY ts", (W["case"],)), width="stretch", hide_index=True)

if st.button("Start the walkthrough again", help="Keeps the data; files a new case"):
    st.session_state.walk = {}
    st.rerun()
