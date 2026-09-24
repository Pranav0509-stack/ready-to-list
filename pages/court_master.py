from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from core import pucar_engine as E
from core.registry import RULES, scrutinise, timeline
from pages.shared import (COURT_OF, JUDGES, LISTING, OUTCOME, REFERENCE_FILES, classify, day_list, docket, dockets,
                          hearing_label, hearing_table, page_setup, plans, read_any, reference_dir, require, sidebar)

page_setup()
u = require("Court master")
sidebar()

st.markdown("<div class='eyebrow'>Registry</div>", unsafe_allow_html=True)
st.markdown("# Court master")
tabs = st.tabs(["Judges", "Files", "Run a day", "New complaint"])

# ---------------------------------------------------------------- judges dashboard
with tabs[0]:
    cols = st.columns(len(JUDGES))
    leave = {date.fromisoformat(str(x)) for x in E.CFG["judge_leave"]["dates"]}
    for col, jname in zip(cols, JUDGES):
        d = docket(jname)
        if d is None:
            body = "<div class='empty'>No docket yet. Add one on the Files tab.</div>"
        else:
            R, real, data, scores = plans(d, jname, reference_dir())
            rtl, base = R["Samay"], R["Today's rules"]
            body = ("<div class='kv'>"
                    f"<div><div class='k'>Cases on file</div><div class='v'>{len(d)}</div></div>"
                    f"<div><div class='k'>Listed a day</div><div class='v'>{rtl['listed_per_day']:.0f}</div></div>"
                    f"<div><div class='k'>Listed cases reached</div><div class='v'>{rtl['reach_rate_pct']:.0f}%</div></div>"
                    f"<div><div class='k'>Heard cases that move</div><div class='v'>{rtl['substantiveness_pct']:.0f}%</div></div>"
                    f"<div><div class='k'>Disposed this quarter</div><div class='v'>{rtl['disposed']:.0f}</div></div>"
                    f"<div><div class='k'>Against today's rules</div><div class='v'>+{rtl['disposed'] - base['disposed']:.0f}</div></div>"
                    "</div>"
                    f"<div class='muted' style='margin-top:10px'>Leave: {', '.join(f'{x:%d %b}' for x in sorted(leave))}</div>")
        col.markdown(f"<div class='card'><h3>{jname}</h3><div class='muted'>{COURT_OF[jname]}, sitting 10:30 to 12:30 "
                     f"and 13:30 to 17:00</div>{body}</div>", unsafe_allow_html=True)
    st.markdown("")
    loaded = [jn for jn in JUDGES if docket(jn) is not None]
    if loaded:
        st.markdown("<div class='eyebrow'>Next sitting day</div>", unsafe_allow_html=True)
        cols = st.columns(len(loaded))
        for col, jname in zip(cols, loaded):
            R, real, data, scores = plans(docket(jname), jname, reference_dir())
            rtl = R["Samay"]
            order = {b["name"]: i for i, b in enumerate(E.DAY["blocks"])}
            first = next(d for d in rtl["workdays"] if d not in leave)
            dl = day_list(rtl["journey"], first, order)
            col.markdown(f"**{jname}**, {first:%A %d %B}: {len(dl)} matters, "
                         f"{float(dl.hearing_type.map(data['ref'].minutes).sum()):.0f} planned minutes, "
                         f"{dl.case_number.map(dict(zip(data['roster'].case_number, data['roster'].advocate_id))).nunique()} advocates")

# ---------------------------------------------------------------- files
with tabs[1]:
    left, right = st.columns([1.15, 1])
    with left:
        jname = st.selectbox("Judge", JUDGES, key="upload_judge")
        ups = st.file_uploader("Add files or a whole folder (CSV, Excel, JSON, or a ZIP of files)",
                               accept_multiple_files=True, key=f"up_{jname}")
        if ups:
            report = []
            for up in ups:
                for fname, df in read_any(up.name, up.getvalue()):
                    if isinstance(df, Exception):
                        report.append((fname, "Could not read", str(df)))
                        continue
                    kind = classify(fname, df)
                    if kind == "docket":
                        problems = E.validate_roster(df)
                        if problems:
                            report.append((fname, "Docket, not usable", "; ".join(problems)))
                        else:
                            dockets()[jname] = df
                            st.session_state[f"docket_name_{jname}"] = fname
                            report.append((fname, f"Docket for {jname}", f"{len(df)} cases"))
                    elif kind in REFERENCE_FILES:
                        df.to_csv(Path(reference_dir()) / kind, index=False)
                        st.cache_data.clear()
                        report.append((fname, "Reference table replaced", kind))
                    else:
                        report.append((fname, "Not recognised", ", ".join(map(str, df.columns[:6]))))
            st.dataframe(pd.DataFrame(report, columns=["File", "Read as", "Detail"]), hide_index=True,
                         width="stretch", height=min(200, 38 + 35 * len(report)))
        d = docket(jname)
        if d is None:
            st.markdown("No docket loaded for this judge.")
        else:
            st.markdown(f"**{st.session_state.get(f'docket_name_{jname}', 'docket')}**: {len(d)} cases, "
                        f"{d.advocate_id.nunique()} advocates, planned for the quarter from 1 October 2026.")
            if st.button("Remove this docket"):
                dockets().pop(jname, None)
                st.rerun()
            st.dataframe(d, width="stretch", hide_index=True, height=280)
    with right:
        st.markdown("<div class='eyebrow'>What Samay reads</div>", unsafe_allow_html=True)
        st.markdown("A docket needs these columns. Reference tables (hearing types, substantiveness, failure reasons, "
                    "calendar) are recognised by their columns and replace the defaults.")
        st.dataframe(pd.DataFrame(
            [{"Column": k, "Meaning": v} for k, v in E.REQUIRED_COLUMNS.items()]
            + [{"Column": "last_hearing_summary", "Meaning": "the last order: who was present, what the court said"},
               {"Column": "total_hearings_held", "Meaning": "hearings so far"}]),
            hide_index=True, width="stretch", height=270)
        st.markdown("<div class='eyebrow'>How the rows look</div>", unsafe_allow_html=True)
        example = pd.DataFrame([
            {"case_number": "ST/819/2023", "filing_date": "2023-01-09", "advocate_id": "ADV-005",
             "current_stage": "Evidence Accused", "purpose_of_next_hearing": "Evidence Accused",
             "last_hearing_summary": "Present: Complainant, Complainant's Advocate, Accused Advocate. Absent: Accused. "
                                     "For defence evidence, last chance.", "total_hearings_held": 35},
            {"case_number": "ST/7/2025", "filing_date": "2025-08-14", "advocate_id": "ADV-011",
             "current_stage": "Appearance", "purpose_of_next_hearing": "Warrant",
             "last_hearing_summary": "Present: Complainant's Advocate. Absent: Accused. Issue NBW to accused. "
                                     "Take steps. For return of warrant.", "total_hearings_held": 6}])
        st.dataframe(example, hide_index=True, width="stretch", height=110)

# ---------------------------------------------------------------- run a day
with tabs[2]:
    loaded = [jn for jn in JUDGES if docket(jn) is not None]
    if not loaded:
        st.markdown("Add a docket first.")
    else:
        top = st.columns([1.2, 1.6, 1, 1])
        jname = top[0].selectbox("Judge", loaded, key="run_judge")
        R, real, data, scores = plans(docket(jname), jname, reference_dir())
        rtl = R["Samay"]
        j = rtl["journey"]
        order = {b["name"]: i for i, b in enumerate(E.DAY["blocks"])}
        leave = {date.fromisoformat(str(x)) for x in E.CFG["judge_leave"]["dates"]}
        sitting_days = [d for d in rtl["workdays"] if d not in leave]
        advocate_of = dict(zip(data["roster"].case_number, data["roster"].advocate_id))
        gaps = E.CFG["next_date"]["after_failure_days"]
        day = top[1].selectbox("Court date", sitting_days, format_func=lambda x: x.strftime("%A %d %B %Y"))
        todays = day_list(j, day, order)
        marks = st.session_state.setdefault("marks", {})
        key = (jname, day)
        done = sum(1 for c in todays.case_number if (key, c) in marks)
        top[2].metric("On the list", len(todays))
        top[3].metric("Recorded", done)
        left, right = st.columns([1.9, 1])
        with right:
            st.progress(done / max(1, len(todays)))
            pending = [c for c in todays.case_number if (key, c) not in marks]
            if pending:
                td = todays.set_index("case_number")
                cur = st.selectbox("Now calling", pending, format_func=lambda c: f"{td.loc[c].start}  {c}")
                r = td.loc[cur]
                st.markdown(f"**{hearing_label(r.hearing_type)}**  \n{LISTING[r.listing]}, advocate "
                            f"{advocate_of.get(cur, '')}, score {r.score:.0f}")
                choice = st.radio("Outcome", list(OUTCOME.values()), label_visibility="collapsed")
                if st.button("Record outcome", type="primary", width="stretch"):
                    marks[(key, cur)] = next(k for k, v in OUTCOME.items() if v == choice)
                    st.rerun()
            else:
                st.success("Every matter on the list is recorded.")
            if done and st.button("Undo last record", width="stretch"):
                last = [c for c in todays.case_number if (key, c) in marks][-1]
                marks.pop((key, last))
                st.rerun()
        with left:
            outcome_text = {}
            for r in todays.itertuples():
                mark = marks.get((key, r.case_number))
                if mark == "substantive":
                    outcome_text[r.case_number] = (OUTCOME[mark], f"{int(data['ref'].loc[r.hearing_type].gap_days)} days, next purpose")
                elif mark:
                    outcome_text[r.case_number] = (OUTCOME[mark], f"{gaps[mark]} days" + (", or when process returns" if mark == "process" else ""))
            st.markdown(hearing_table(todays, advocate_of, height=560, show_why=False, show_outcome=outcome_text),
                        unsafe_allow_html=True)

# ---------------------------------------------------------------- new complaint
with tabs[3]:
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
