"""Readiness score (0-100), state, pending tasks and queue priority."""
from datetime import date

import numpy as np
import pandas as pd

from core import agents
from core import taxonomy as T
from core.config import HEARING_TYPES, LOCKED
from core.data import df, working_days

URGENT_PRIORITY = 1000  # fixed top priority, nothing overrides it


def case_frame(conn, judge_id=None, on: date | None = None) -> pd.DataFrame:
    """One row per pending case with readiness, state, priority and advocate info."""
    where = "WHERE c.status='pending'" + (" AND c.judge_id=?" if judge_id else "")
    params = (judge_id,) if judge_id else ()
    cases = df(conn, f"""
        SELECT c.*, f.filing_score,
               p.advocate_id AS pet_adv, pa.name AS pet_name, pa.agent_type AS pet_type,
               pa.show_rate AS pet_show, pa.warned AS pet_warned,
               r.advocate_id AS res_adv, ra.name AS res_name, ra.agent_type AS res_type,
               r.party_in_person AS pip
        FROM cases c
        LEFT JOIN (SELECT case_id, MAX(version) v FROM filings GROUP BY case_id) lv ON lv.case_id=c.id
        LEFT JOIN filings f ON f.case_id=c.id AND f.version=lv.v
        LEFT JOIN case_parties p ON p.case_id=c.id AND p.side='petitioner'
        LEFT JOIN advocates pa ON pa.id=p.advocate_id
        LEFT JOIN case_parties r ON r.case_id=c.id AND r.side='respondent'
        LEFT JOIN advocates ra ON ra.id=r.advocate_id
        {where}""", params)
    pre = df(conn, "SELECT p.case_id, p.item, p.done FROM prerequisites p JOIN cases c "
                   "ON c.id=p.case_id AND c.next_purpose=p.purpose")
    agg = pre.groupby("case_id").agg(n=("done", "size"), done=("done", "sum"))
    pending = pre[pre.done == 0].groupby("case_id")["item"].apply(lambda s: ", ".join(s))
    cases = cases.join(agg, on="id").join(pending.rename("pending_items"), on="id")
    cases["prereq_frac"] = np.where(cases.n.fillna(0) > 0, cases.done.fillna(0) / cases.n.clip(lower=1), 1.0)
    cases["pending_items"] = cases.pending_items.fillna("")

    on = on or date.today()
    cases["age_years"] = (pd.Timestamp(on) - pd.to_datetime(cases.filing_date)).dt.days / 365.25
    cases["filing_score"] = cases.filing_score.fillna(80)
    cases["summary_ok"] = (cases.age_years < 4) | (cases.summary_verified == 1)
    cases["readiness"] = (30 * cases.filing_score / 100 + 40 * cases.prereq_frac
                          + 20 * cases.confirmed + 10 * cases.summary_ok).round(0)
    gate = LOCKED["readiness_gate"]
    cases["state"] = np.select([cases.readiness >= 75, cases.readiness >= gate], ["ready", "at risk"], "blocked")
    cases["urgent"] = cases.urgency_flag.astype(bool)
    cases["old"] = cases.age_years >= 5
    cases["priority"] = priority(cases)
    return cases


def priority(c: pd.DataFrame) -> pd.Series:
    """hearing_type_priority + age_boost + readiness * 0.5; urgent matters get a fixed top priority."""
    htp = c.next_purpose.map(lambda p: HEARING_TYPES[p]["priority"])
    age_boost = (c.age_years * 12 * 0.5)  # half a point for every month waited
    base = htp + age_boost + c.readiness * 0.5
    if "category" in c:  # cost of waiting for this kind of case (liberty, statutory clocks)
        vw = T.TAX["priority_from_waiting_cost"]
        cost = [T.waiting_cost(cat, a * 365)[()] if isinstance(cat, str) and cat in T.TYPES else 0
                for cat, a in zip(c.category, c.age_years)]
        base = base + np.minimum(np.array(cost, dtype=float) / vw["divisor"], vw["cap"])
    return np.where(c.urgent, URGENT_PRIORITY + base, base).round(1)


def readiness(conn, case_id, on=None) -> dict:
    row = case_frame(conn, on=on).set_index("id").loc[case_id]
    tasks = [f"Complete {i}" for i in row.pending_items.split(", ") if i]
    if not row.confirmed:
        tasks.append("Counsel to confirm readiness")
    if not row.summary_ok:
        tasks.append("Both counsel to verify summary cover sheet")
    return {"score": row.readiness, "state": row.state, "priority": row.priority, "tasks": tasks}


def run_nudges(conn, on: date, rng=None):
    """T-2 intent check: advocates with matters due in the next two working days reply.
    Response probability follows the advocate model."""
    rng = rng or np.random.default_rng(11)
    horizon = working_days(on, 3)[-1].isoformat()
    due = df(conn, """SELECT c.id, a.agent_type, a.id adv FROM cases c
                      JOIN case_parties p ON p.case_id=c.id AND p.side='petitioner'
                      JOIN advocates a ON a.id=p.advocate_id
                      WHERE c.status='pending' AND c.next_date<=? AND c.confirmed=0""", (horizon,))
    for r in due.itertuples():
        yes = agents.will_confirm(rng, r.agent_type)
        conn.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?)",
                     (r.id, r.adv, "whatsapp", "T-2 intent check: are you ready for the listed hearing?",
                      on.isoformat(), "confirmed" if yes else "no reply"))
        if yes:
            conn.execute("UPDATE cases SET confirmed=1 WHERE id=?", (r.id,))
    conn.commit()
