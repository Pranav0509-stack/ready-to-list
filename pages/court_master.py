from datetime import date

import pandas as pd
import streamlit as st

from core import pucar_engine as E
from core.registry import RULES, scrutinise, timeline
from pages.shared import LISTING, OUTCOME, day_list, docket, page_setup, plans, require, sidebar

page_setup()
u = require("Court master")
sidebar()

st.markdown("# Court 12")
tabs = st.tabs(["Docket file", "Run today's list", "New complaint"])

# ---------------------------------------------------------------- docket file
with tabs[0]:
    c1, c2 = st.columns([1.1, 1])
    with c1:
        up = st.file_uploader("Upload the judge's docket (Excel or CSV)", type=["xlsx", "xls", "csv"])
        if up is not None:
            try:
                df = pd.read_excel(up) if up.name.lower().endswith(("xlsx", "xls")) else pd.read_csv(up)
                problems = E.validate_roster(df)
                if problems:
                    st.error("The file cannot be planned yet.\n\n" + "\n".join(f"- {p}" for p in problems))
                else:
                    st.session_state.docket, st.session_state.docket_name = df, up.name
            except Exception as e:
                st.error(f"Could not read {up.name}: {e}")
        d = docket()
        if d is None:
            st.info("No docket yet. Upload the file to plan the quarter; the judge's screens open once it is in.")
        else:
            st.success(f"{st.session_state.docket_name}: {len(d)} cases, {d.advocate_id.nunique()} advocates. "
                       f"Planned for the quarter from 1 October 2026.")
            if st.button("Remove this docket"):
                for k in ("docket", "docket_name"):
                    st.session_state.pop(k, None)
                st.rerun()
    with c2:
        st.markdown("**Columns the file needs**")
        st.dataframe(pd.DataFrame([{"Column": k, "Meaning": v} for k, v in E.REQUIRED_COLUMNS.items()]
                                  + [{"Column": "last_hearing_summary", "Meaning": "the last order, read for readiness and urgency"},
                                     {"Column": "total_hearings_held", "Meaning": "hearings so far, for the churn factor"}]),
                     hide_index=True, width="stretch", height=270)
    if docket() is not None:
        st.dataframe(docket(), width="stretch", hide_index=True, height=330)

# ---------------------------------------------------------------- run today's list
with tabs[1]:
    if docket() is None:
        st.info("Upload the docket first.")
    else:
        R, real, data, scores = plans(docket())
        rtl = R["Samay"]
        j = rtl["journey"]
        order = {b["name"]: i for i, b in enumerate(E.DAY["blocks"])}
        leave = {date.fromisoformat(str(x)) for x in E.CFG["judge_leave"]["dates"]}
        sitting_days = [d for d in rtl["workdays"] if d not in leave]
        advocate_of = dict(zip(data["roster"].case_number, data["roster"].advocate_id))
        gaps = E.CFG["next_date"]["after_failure_days"]
        top, side = st.columns([1.6, 1])
        with side:
            day = st.selectbox("Court date", sitting_days, format_func=lambda x: x.strftime("%A %d %B %Y"))
            todays = day_list(j, day, order)
            marks = st.session_state.setdefault("marks", {})
            done = sum(1 for c in todays.case_number if (day, c) in marks)
            m1, m2 = st.columns(2)
            m1.metric("On the list", len(todays))
            m2.metric("Recorded", done)
            st.progress(done / max(1, len(todays)))
            pending = [c for c in todays.case_number if (day, c) not in marks]
            if pending:
                cur = st.selectbox("Now calling", pending, format_func=lambda c: f"{c}  ({todays.set_index('case_number').loc[c].start})")
                r = todays.set_index("case_number").loc[cur]
                st.markdown(f"**{r.hearing_type.replace('_', ' ').title()}**, {LISTING[r.listing]}, advocate {advocate_of.get(cur, '')}, score {r.score:.0f}")
                choice = st.radio("Outcome", list(OUTCOME.values()), label_visibility="collapsed")
                if st.button("Record outcome", type="primary", width="stretch"):
                    marks[(day, cur)] = next(k for k, v in OUTCOME.items() if v == choice)
                    st.rerun()
            else:
                st.success("Every matter on the list is recorded.")
            if done and st.button("Undo last record"):
                last = [c for c in todays.case_number if (day, c) in marks][-1]
                marks.pop((day, last))
                st.rerun()
        with top:
            rows = []
            for r in todays.itertuples():
                mark = marks.get((day, r.case_number))
                if mark == "substantive":
                    nxt = f"in {int(data['ref'].loc[r.hearing_type].gap_days)} days, next purpose"
                elif mark:
                    nxt = f"in {gaps[mark]} days" + (", or when process returns" if mark == "process" else "")
                else:
                    nxt = ""
                rows.append({"Time": r.start, "Case": r.case_number,
                             "Hearing": r.hearing_type.replace("_", " ").title(), "Listing": LISTING[r.listing],
                             "Advocate": advocate_of.get(r.case_number, ""),
                             "Outcome": OUTCOME.get(mark, "") if mark else "", "Next date": nxt})
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True, height=560)

# ---------------------------------------------------------------- new complaint
with tabs[2]:
    c1, c2, c3 = st.columns(3)
    cheque = c1.date_input("Cheque date", date(2026, 5, 2))
    presented = c1.date_input("Presented to the bank", date(2026, 7, 20))
    memo = c2.date_input("Return memo received", date(2026, 7, 24))
    sent = c2.date_input("Demand notice sent", date(2026, 8, 10))
    received = c3.date_input("Notice received by the drawer", date(2026, 8, 14))
    filed = c3.date_input("Complaint filed", date(2026, 9, 30))
    tl = timeline(cheque, presented, memo, sent, received, filed)
    left, right = st.columns([1.3, 1])
    with left:
        st.dataframe(pd.DataFrame([{"Step": r["step"], "Date": r["date"], "Deadline": r["deadline"],
                                    "In time": "Yes" if r["in_time"] else "No", "Rule": r["rule"]} for r in tl["rows"]]),
                     width="stretch", hide_index=True)
        d1, d2 = st.columns(2)
        docs = {d["id"]: d1.checkbox(f"{d['label']} ({d['law']})", value=d["id"] not in ("s141_averments", "vakalat"),
                                     key=f"doc_{d['id']}") for d in RULES["documents"]}
        summons = {s["id"]: d2.checkbox(s["label"], value=s["id"] == "address", key=f"sum_{s['id']}")
                   for s in RULES["summons_details"]}
        company = d2.toggle("The drawer is a company")
        outside = d2.toggle("The accused lives outside this court's area", value=True)
    res = scrutinise(tl, docs, summons, company, outside)
    with right:
        st.markdown(f"**Status:** {res['status']}")
        if tl["delay_days"]:
            st.markdown(f"Filed {tl['delay_days']} days after limitation ended. The condonation petition is filed with the "
                        "complaint and decided at admission.")
        if res["s225_enquiry"]:
            st.markdown("Accused outside the court's area: s.225 enquiry affidavit with the complaint.")
        if res["e_summons"]:
            st.markdown("Phone or email given: summons can go electronically.")
        if res["missing"] or res["summons_missing"]:
            st.markdown("**To cure:** " + "; ".join(res["missing"] + res["summons_missing"]))
