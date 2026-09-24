from datetime import timedelta

import streamlit as st

from core.config import JUDGES, STAGE_FLOW, to_hhmm, to_min
from core.data import df
from core.nextdate import REASON_CODES, confirm_next_date, next_date, plan_after, record_outcome
from core.scheduler import approve, build_causelist
from pages.common import resources, sidebar

conn, predictor = resources()
jid, day = sidebar()
court = JUDGES[jid]

st.title(f"Court {court['court']}, hearing day")
st.caption(f"{court['name']}, {day:%A %d %B %Y}. Mark each outcome with one tap; the next date follows.")

items = df(conn, """SELECT cl.*, c.title, c.next_purpose, a.name counsel FROM causelists cl
                    JOIN cases c ON c.id=cl.case_id
                    LEFT JOIN case_parties p ON p.case_id=c.id AND p.side='petitioner'
                    LEFT JOIN advocates a ON a.id=p.advocate_id
                    WHERE cl.judge_id=? AND cl.date=? ORDER BY cl.seq""", (jid, day.isoformat()))
if items.empty:
    st.info("The judge has not approved a list for this date yet.")
    if st.button("Approve the recommended list (demo shortcut)"):
        r = build_causelist(conn, predictor, jid, day)
        approve(conn, jid, day, r["items"], judge_name=court["name"])
        st.rerun()
    st.stop()

# Live ETA: each block starts on time, done items take their recorded minutes, the rest their expected minutes
used = df(conn, "SELECT case_id, minutes_used FROM hearings WHERE judge_id=? AND date=?",
          (jid, day.isoformat())).set_index("case_id").minutes_used
eta = {}
for block, g in items.groupby("block", sort=False):
    t = to_min(next(b for b in court["blocks"] if b["name"] == block)["start"])
    for r in g.itertuples():
        eta[r.case_id] = to_hhmm(t)
        t += used.get(r.case_id, r.expected_minutes)
items["eta"] = items.case_id.map(eta)
pending = items[items.outcome.isna()]
done = len(items) - len(pending)

k1, k2, k3, k4 = st.columns(4)
k1.metric("Listed", len(items))
k2.metric("Done", done)
k3.metric("Effective", int((items.outcome == "effective").sum()))
k4.metric("Adjourned", int((items.outcome == "adjourned").sum()))
st.progress(done / len(items))

left, right = st.columns([1.3, 1])
ICON = {"effective": ":green[:material/check_circle:]", "heard_not_effective": ":orange[:material/pending:]",
        "adjourned": ":red[:material/event_busy:]"}

with right:
    if pending.empty:
        st.success("All items marked for today.")
    else:
        cur_id = st.session_state.get("cm_current")
        if cur_id not in pending.case_id.values:
            cur_id = pending.case_id.iloc[0]
        cur = items.set_index("case_id").loc[cur_id]
        with st.container(border=True):
            st.markdown(f"#### Now hearing: item {cur.seq}")
            st.markdown(f"**{cur.title}**  \nFor {cur.next_purpose}. Counsel {cur.counsel}. Slot {cur.slot_start}-{cur.slot_end}, "
                        f"expected {cur.eta}.")
            with st.expander("Why listed"):
                st.write(cur.reason)
            reason = st.radio("If adjourned, reason", REASON_CODES, horizontal=True, key=f"rc_{cur_id}")
            b1, b2, b3 = st.columns(3)
            clicked = None
            if b1.button("Effective", icon=":material/check_circle:", width="stretch", type="primary"):
                clicked = ("effective", None)
            if b2.button("Heard, not effective", icon=":material/pending:", width="stretch"):
                clicked = ("heard_not_effective", "not prepared")
            if b3.button("Adjourned", icon=":material/event_busy:", width="stretch"):
                clicked = ("adjourned", reason)
            if clicked:
                outcome, code = clicked
                record_outcome(conn, jid, day, cur_id, outcome, code)
                purpose = plan_after(outcome, cur.next_purpose, code)
                st.session_state.cm_pending = {"case_id": cur_id, "outcome": outcome, "code": code,
                                               "purpose": purpose}
                st.rerun()

    # Next-date suggestion for the last marked case
    pend = st.session_state.get("cm_pending")
    if pend:
        with st.container(border=True):
            st.markdown(f"#### Next date for {pend['case_id']}")
            purpose = st.selectbox("Next purpose", STAGE_FLOW + ["bail", "stay", "habeas"],
                                   index=(STAGE_FLOW + ["bail", "stay", "habeas"]).index(pend["purpose"]),
                                   key=f"np_{pend['case_id']}")
            rec = next_date(conn, pend["case_id"], pend["outcome"], purpose, day, pend["code"])
            st.markdown(f"### {rec['date']:%d %b}, {rec['slot']}")
            st.caption(f"{rec['gap_days']} days out instead of a flat 60. Why: {rec['reason']}.")
            if rec["tasks"]:
                st.markdown("**Tasks sent to both sides:** " + "; ".join(rec["tasks"]))
            new_d = st.date_input("Change date", rec["date"], key=f"nd_{pend['case_id']}")
            c1, c2 = st.columns(2)
            if c1.button("Confirm", type="primary", width="stretch"):
                changed = new_d != rec["date"]
                if changed:
                    rec = {**rec, "date": new_d, "reason": rec["reason"] + "; changed by court master"}
                confirm_next_date(conn, pend["case_id"], rec, day, changed_by="Court master" if changed else None)
                st.session_state.cm_pending = None
                st.toast(f"Next date {rec['date']:%d %b} sent to both sides and the litigant.")
                st.rerun()
            if c2.button("Skip", width="stretch"):
                st.session_state.cm_pending = None
                st.rerun()

with left:
    st.markdown("**Today's list**")
    for block, g in items.groupby("block", sort=False):
        st.caption(block)
        for r in g.itertuples():
            icon = ICON.get(r.outcome, ":gray[:material/radio_button_unchecked:]")
            c1, c2 = st.columns([5, 1])
            c1.markdown(f"{icon} **{r.seq}.** {r.eta}, {r.title[:52]}  \n"
                        f":gray[{r.next_purpose}, {r.counsel}, slot {r.slot_start}-{r.slot_end}]")
            if r.outcome is None or r.outcome != r.outcome:  # NaN when not yet marked
                if c2.button("Call", key=f"call_{r.case_id}"):
                    st.session_state.cm_current = r.case_id
                    st.rerun()
