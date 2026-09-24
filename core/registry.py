"""The registry's scrutiny of an NI Act s.138 complaint, computed at e-filing.

Nothing is refused: the result says what is missing, whether the complaint is in time, how
many days late it is if not (so the condonation petition is filed and heard with admission
instead of in a separate track), and whether a s.225 enquiry is needed before summons.
Rules and periods: config/registry_ni138.yaml.
"""
from datetime import date, timedelta

import yaml

from core.pucar_engine import ROOT

RULES = yaml.safe_load((ROOT / "config" / "registry_ni138.yaml").read_text())
P = RULES["periods"]


def timeline(cheque_date: date, presented: date, return_memo: date, notice_sent: date,
             notice_received: date, complaint: date) -> dict:
    """Every statutory date, and whether each step was in time."""
    cause = notice_received + timedelta(days=P["pay_after_notice"])
    last_day = cause + timedelta(days=P["complaint_after_cause"])
    steps = [
        ("Cheque presented", presented, cheque_date + timedelta(days=P["cheque_validity"]),
         "within 3 months of the cheque's date"),
        ("Demand notice sent", notice_sent, return_memo + timedelta(days=P["notice_after_intimation"]),
         "within 30 days of the return memo"),
        ("Complaint filed", complaint, last_day, "within one month of the cause of action"),
    ]
    rows = [{"step": s, "date": d, "deadline": dl, "in_time": d <= dl, "rule": r} for s, d, dl, r in steps]
    return {"rows": rows, "cause_of_action": cause, "limitation_ends": last_day,
            "delay_days": max(0, (complaint - last_day).days),
            "maintainable": rows[0]["in_time"] and rows[1]["in_time"]}


def scrutinise(tl: dict, documents: dict, summons: dict, company_drawer: bool, accused_outside: bool) -> dict:
    missing = [d["label"] for d in RULES["documents"]
               if (d["required"] or (d["id"] == "s141_averments" and company_drawer)) and not documents.get(d["id"])]
    summons_missing = [s["label"] for s in RULES["summons_details"] if s["required"] and not summons.get(s["id"])]
    e_summons = bool(summons.get("phone") or summons.get("email"))
    notes = []
    if not tl["maintainable"]:
        notes.append("A statutory step was out of time: the complaint may not be maintainable. The registry flags it for "
                     "the judge; it is still accepted.")
    if tl["delay_days"]:
        notes.append(f"Filed {tl['delay_days']} days after limitation ended: attach a condonation petition now, so it is "
                     "heard with admission instead of in a separate delay condonation track.")
    if accused_outside:
        notes.append("The accused lives outside the court's area: a BNSS s.225 enquiry is needed before summons. The "
                     "enquiry affidavit should come with the complaint.")
    if e_summons:
        notes.append("Phone or email given: summons can go electronically, which cuts the wait for service.")
    ready = not missing and not summons_missing
    status = ("Ready for admission" if ready and not tl["delay_days"] else
              "Ready, with condonation heard at admission" if ready else "Accepted, items to cure")
    return {"status": status, "ready": ready, "missing": missing, "summons_missing": summons_missing,
            "e_summons": e_summons, "notes": notes,
            "first_purpose": "ADMISSION", "s225_enquiry": accused_outside}
