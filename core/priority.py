"""Samay case priority score (0 to 100), as specified in docs/PRIORITY_SCORE.md.

Five factors, each scaled to 0..1 then weighted: Case Age 35, Hearing Readiness 25, Disposal
Proximity 15, Hearing Churn 15, Court-Set Urgency 10. Every case is scored on its own; the score
never compares cases. Rules R1 to R7 produce a status and flags next to the score and never change it.
The scheduling layer (core/pucar_engine.py) orders and fits cases; this module only scores them.
"""
import re
from datetime import date

import numpy as np
import pandas as pd

WEIGHTS = {"age": 35, "readiness": 25, "disposal": 15, "churn": 15, "urgency": 10}
STAGES = ["ADMISSION", "DELAY_CONDONATION_HEARING", "COGNIZANCE", "APPEARANCE", "WARRANT", "PLEA",
          "EXAMINATION_UNDER_S351_BNSS", "EVIDENCE_COMPLAINANT", "EVIDENCE_ACCUSED", "ARGUMENTS", "JUDGEMENT"]
REQUIRED_PEOPLE = {  # who a hearing needs, by its purpose (fixed rule from the spec)
    "EVIDENCE_COMPLAINANT": ["Complainant", "Complainant's Advocate", "Accused Advocate"],
    "EVIDENCE_ACCUSED": ["Accused", "Accused Advocate", "Complainant's Advocate"],
    "PLEA": ["Accused", "Accused Advocate"],
    "EXAMINATION_UNDER_S351_BNSS": ["Accused", "Accused Advocate"],
    "ARGUMENTS": ["Complainant's Advocate", "Accused Advocate"],
    "JUDGEMENT": ["Complainant's Advocate", "Accused Advocate"],
    "BAIL": ["Accused Advocate"],
}
DEFAULT_PEOPLE = ["Complainant's Advocate"]
ATTENDANCE_FLOOR = 0.7          # nobody present reduces readiness to 70 percent, never lower (R5)
URGENCY = [("last chance", 1.0), ("for judgment", 0.8), ("for judgement", 0.8)]
PROCESS_PENDING = ["return of summons", "return of warrant", "return of notice", "await notice", "issue nbw",
                   "issue summons", "issue warrant", "take steps"]
MIN_AGE_WEIGHT = 20             # R2: age can never fall below 20 percent


def norm(s) -> str:
    return str(s).strip().upper().replace(" ", "_")


def full_points_age(filing_dates, as_of: date) -> int:
    """Age at which a case earns all 35 age points: max(5, round(mean + 2 sd)). Computed once per roster."""
    ages = (pd.Timestamp(as_of) - pd.to_datetime(filing_dates)).dt.days / 365.25
    return int(max(5, round(ages.mean() + 2 * ages.std())))


def expected_hearings(ref_median: dict) -> dict:
    """Median hearings per case summed over every stage up to and including each stage."""
    out, run = {}, 0
    for st in STAGES:
        run += ref_median.get(st, 0)
        out[st] = run
    return out


def present_people(summary: str) -> list:
    for line in str(summary).splitlines():
        if line.strip().lower().startswith("present:"):
            return [p.strip() for p in line.split(":", 1)[1].split(",") if p.strip()]
    return []


def score_case(*, filing_date, current_stage, next_purpose, summary, total_hearings, as_of: date,
               progress_rate: dict, ref_median: dict, fp_age: int, weights=None) -> dict:
    """One case's score and every factor's points, plus status and flags."""
    w = weights or WEIGHTS
    purpose, stage = norm(next_purpose), norm(current_stage)
    # 1. Case age
    age_years = (pd.Timestamp(as_of) - pd.Timestamp(filing_date)).days / 365.25
    age_value = min(1.0, age_years / fp_age)
    # 2. Hearing readiness
    rate = progress_rate.get(purpose, 0.5)
    need = REQUIRED_PEOPLE.get(purpose, DEFAULT_PEOPLE)
    present = present_people(summary)
    share = sum(1 for p in need if p in present) / len(need)
    attendance = ATTENDANCE_FLOOR + (1 - ATTENDANCE_FLOOR) * share
    readiness_value = min(1.0, rate * attendance)
    # 3. Disposal proximity
    stage_no = STAGES.index(stage) if stage in STAGES else 0
    disposal_value = stage_no / (len(STAGES) - 1)
    # 4. Hearing churn
    exp = expected_hearings(ref_median).get(stage, 1) or 1
    churn_value = float(np.clip(total_hearings / exp - 1, 0, 1))
    # 5. Court-set urgency
    text = str(summary).lower()
    urgency_value = next((v for k, v in URGENCY if k in text), 0.0)
    pts = {"age": w["age"] * age_value, "readiness": w["readiness"] * readiness_value,
           "disposal": w["disposal"] * disposal_value, "churn": w["churn"] * churn_value,
           "urgency": w["urgency"] * urgency_value}
    flags = []
    if urgency_value == 1.0:
        flags.append("Last chance")
    if churn_value > 0:
        flags.append("Churning")
    if age_years >= 5:
        flags.append("Ageing 5y+")
    elif age_years >= 4:
        flags.append("Backlog 4y+")
    if purpose == "BAIL":
        flags.append("Liberty lane")
    if "mediation" in text and "report" in text:
        flags.append("Mediation report pending")
    process_pending = any(k in text for k in PROCESS_PENDING)
    status = "Conditional" if process_pending else "Eligible"
    if process_pending:
        flags.append("Confirm service")
    return {"score": round(sum(pts.values()), 1), **{f"{k}_points": round(v, 1) for k, v in pts.items()},
            "age_years": round(age_years, 1), "status": status, "flag_list": ", ".join(flags),
            "process_pending": process_pending}


def score_roster(roster: pd.DataFrame, ref: pd.DataFrame, as_of: date, weights=None) -> pd.DataFrame:
    """Score every case in a roster. `ref` is the engine's hearing-type reference (index = type,
    columns p_sub and median hearings)."""
    fp = full_points_age(roster.filing_date, as_of)
    progress = {t: float(r.p_sub) for t, r in ref.iterrows()}
    median = {t: float(r["Median Hearings per Case"]) for t, r in ref.iterrows()}
    rows = []
    for r in roster.itertuples():
        rows.append({"case_number": r.case_number, **score_case(
            filing_date=r.filing_date, current_stage=r.current_stage, next_purpose=r.purpose_of_next_hearing,
            summary=r.last_hearing_summary, total_hearings=r.total_hearings_held, as_of=as_of,
            progress_rate=progress, ref_median=median, fp_age=fp, weights=weights)})
    out = pd.DataFrame(rows)
    out.attrs["full_points_age"] = fp
    return out


def rescale_weights(w: dict) -> dict:
    """R2: the judge may change weights, but age never below 20; then rescale to 100."""
    w = dict(w)
    w["age"] = max(MIN_AGE_WEIGHT, w["age"])
    total = sum(w.values())
    return {k: v * 100 / total for k, v in w.items()}


def explain(*, filing_date, current_stage, next_purpose, summary, total_hearings, as_of: date,
            progress_rate: dict, ref_median: dict, fp_age: int, weights=None) -> list:
    """Each factor as (name, points, max points, the evidence behind it), for the judge's view.
    Uses the same arithmetic as score_case."""
    w = weights or WEIGHTS
    purpose, stage = norm(next_purpose), norm(current_stage)
    label = lambda t: t.replace("_", " ").title()
    age_years = (pd.Timestamp(as_of) - pd.Timestamp(filing_date)).days / 365.25
    rate = progress_rate.get(purpose, 0.5)
    need = REQUIRED_PEOPLE.get(purpose, DEFAULT_PEOPLE)
    present = present_people(summary)
    came = [p for p in need if p in present]
    attendance = ATTENDANCE_FLOOR + (1 - ATTENDANCE_FLOOR) * len(came) / len(need)
    stage_no = STAGES.index(stage) if stage in STAGES else 0
    exp = expected_hearings(ref_median).get(stage, 1) or 1
    churn = float(np.clip(total_hearings / exp - 1, 0, 1))
    text = str(summary).lower()
    hit = next(((k, v) for k, v in URGENCY if k in text), (None, 0.0))
    missing = [p for p in need if p not in present]
    return [
        ("Case age", w["age"] * min(1.0, age_years / fp_age), w["age"],
         f"Filed {pd.Timestamp(filing_date):%d %b %Y}, {age_years:.1f} years ago. Full points at {fp_age} years."),
        ("Hearing readiness", w["readiness"] * min(1.0, rate * attendance), w["readiness"],
         f"{label(purpose)} hearings move the case {rate:.0%} of the time. Needs: {', '.join(need)}. "
         + (f"All present last time." if not missing else f"Absent last time: {', '.join(missing)}.")),
        ("Disposal proximity", w["disposal"] * stage_no / (len(STAGES) - 1), w["disposal"],
         f"Stage {stage_no + 1} of {len(STAGES)}: {label(stage)}."),
        ("Hearing churn", w["churn"] * churn, w["churn"],
         f"{int(total_hearings)} hearings held; about {exp:.0f} expected by this stage."),
        ("Court-set urgency", w["urgency"] * hit[1], w["urgency"],
         f'Last order says "{hit[0]}".' if hit[0] else "No urgency set in the last order."),
    ]
