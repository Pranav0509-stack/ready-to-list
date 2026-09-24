"""Hearing-day outcomes and the next-date recommender.

next_date = today + max(ideal gap for next purpose, time prerequisites need)
            -> first day the judge has capacity, avoiding holidays, the judge's leave
               and the advocate's listings in other courts.
"""
from datetime import date, timedelta

from core import audit
from core.agents import BASE_SHOW
from core.config import HEARING_TYPES, JUDGES, PREREQ_DAYS, capacity, next_stage, to_hhmm, to_min
from core.data import df
from core.predict import learn_from_outcome
from core.scheduler import block_for

AVG_P_SHOW = 0.7
REASON_CODES = ["counsel absent", "not prepared", "service pending", "time ran out"]


def plan_after(outcome, purpose, reason_code=None):
    """Which purpose comes next, given what happened today."""
    if outcome == "effective":
        return next_stage(purpose)
    return purpose


def next_date(conn, case_id, outcome, next_purpose, today: date, reason_code=None):
    case = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
    jid = case["judge_id"]
    cfg = JUDGES[jid]
    adv = conn.execute("SELECT advocate_id FROM case_parties WHERE case_id=? AND side='petitioner'",
                       (case_id,)).fetchone()["advocate_id"]

    items = list(HEARING_TYPES[next_purpose]["prereqs"])
    if reason_code == "service pending" and "service" not in items:
        items.append("service")
    prereq_days = max([PREREQ_DAYS[i] for i in items], default=0)
    ideal = HEARING_TYPES[next_purpose]["ideal_gap"]
    if outcome != "effective":
        # Same purpose again: time ran out comes back soonest, absence gets a short gap
        ideal = {"time ran out": 1, "counsel absent": 7, "not prepared": 10}.get(reason_code, 7)
    gap = max(ideal, prereq_days)
    if HEARING_TYPES[next_purpose]["urgent"]:
        gap = min(gap, HEARING_TYPES[next_purpose]["ideal_gap"])  # liberty matters are never pushed out
    why = []
    if prereq_days and prereq_days >= ideal:
        why.append(f"{', '.join(items)} needs {prereq_days} days")
    else:
        why.append(f"ideal gap for {next_purpose} is {ideal} days")

    cap = capacity(jid) * 0.95
    cal = df(conn, "SELECT date, holiday, judge_leave FROM calendar WHERE judge_id=?", (jid,)).set_index("date")
    skipped = []
    d = today + timedelta(days=gap)
    for _ in range(120):
        ds = d.isoformat()
        row = cal.loc[ds] if ds in cal.index else None
        if d.weekday() >= 5:
            pass
        elif row is not None and row.holiday:
            skipped.append(f"{d:%d %b} holiday")
        elif row is not None and row.judge_leave:
            skipped.append(f"{d:%d %b} judge on leave")
        else:
            booked = _booked_minutes(conn, jid, ds)
            clash = conn.execute("""SELECT c.judge_id FROM cases c JOIN case_parties p
                                    ON p.case_id=c.id AND p.side='petitioner'
                                    WHERE p.advocate_id=? AND c.next_date=? AND c.judge_id<>? AND c.status='pending'
                                    LIMIT 1""", (adv, ds, jid)).fetchone()
            if booked >= cap and not HEARING_TYPES[next_purpose]["urgent"]:  # urgent never waits for room
                skipped.append(f"{d:%d %b} court full")
            elif clash:
                skipped.append(f"{d:%d %b} counsel listed in Court {JUDGES[clash['judge_id']]['court']}")
            else:
                break
        d += timedelta(days=1)

    bname = block_for(next_purpose, cfg["blocks"])
    blk = next(b for b in cfg["blocks"] if b["name"] == bname)
    in_block = _booked_minutes(conn, jid, d.isoformat(), bname)
    s0 = min(to_min(blk["start"]) + (in_block // 60) * 60, to_min(blk["end"]) - 60)
    tasks = [f"{i} (due {(d - timedelta(days=PREREQ_DAYS[i] // 2)):%d %b})" for i in items]
    if skipped:
        why.append("skipped " + "; ".join(skipped[:3]))
    return {"date": d, "slot": f"{to_hhmm(s0)}-{to_hhmm(s0 + 60)}", "purpose": next_purpose,
            "reason": "; ".join(why), "tasks": tasks, "items": items, "gap_days": (d - today).days}


def _booked_minutes(conn, jid, ds, block_name=None):
    rows = conn.execute("SELECT next_purpose FROM cases WHERE judge_id=? AND next_date=? AND status='pending'",
                        (jid, ds)).fetchall()
    blocks = JUDGES[jid]["blocks"]
    return sum(HEARING_TYPES[r[0]]["duration"] * AVG_P_SHOW for r in rows
               if block_name is None or block_for(r[0], blocks) == block_name)


def record_outcome(conn, judge_id, day: date, case_id, outcome, reason_code=None, minutes=None):
    case = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
    purpose = case["next_purpose"]
    showed = outcome != "adjourned" or reason_code not in ("counsel absent",)
    minutes = minutes if minutes is not None else (HEARING_TYPES[purpose]["duration"] if showed else 2)
    adv = conn.execute("SELECT p.advocate_id, a.agent_type, a.show_rate FROM case_parties p JOIN advocates a ON a.id=p.advocate_id "
                       "WHERE p.case_id=? AND p.side='petitioner'", (case_id,)).fetchone()
    pre = conn.execute("SELECT AVG(done) FROM prerequisites WHERE case_id=? AND purpose=?",
                       (case_id, purpose)).fetchone()[0]
    conn.execute("UPDATE causelists SET outcome=? WHERE judge_id=? AND date=? AND case_id=?",
                 (outcome, judge_id, day.isoformat(), case_id))
    res = conn.execute("SELECT r.party_in_person, a.agent_type FROM case_parties r LEFT JOIN advocates a "
                       "ON a.id=r.advocate_id WHERE r.case_id=? AND r.side='respondent'", (case_id,)).fetchone()
    res_rate = 0.8 if (res is None or res[0] or res[1] is None) else BASE_SHOW[res[1]]
    age = (day - date.fromisoformat(case["filing_date"])).days / 365.25
    old_unsum = int(age >= 4 and not case["summary_verified"])
    conn.execute(f"INSERT INTO hearings VALUES ({','.join('?' * 18)})",
                 (case_id, judge_id, day.isoformat(), purpose, None, outcome, reason_code, minutes,
                  case["confirmed"], 0, 0, 1, pre if pre is not None else 1.0, adv["show_rate"] if adv else 0.7, res_rate, old_unsum, int(showed),
                  int(outcome == "effective")))
    if adv:
        learn_from_outcome(conn, adv["advocate_id"], showed)
        if case["confirmed"] and reason_code == "counsel absent":
            conn.execute("UPDATE advocates SET warned=1 WHERE id=?", (adv["advocate_id"],))
            conn.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?)",
                         (case_id, adv["advocate_id"], "sms",
                          "Costs warning: you confirmed readiness but did not appear.", day.isoformat(), None))
    if outcome == "effective" and purpose == "final":
        conn.execute("UPDATE cases SET status='disposed' WHERE id=?", (case_id,))
    audit.log(conn, "court_master", case_id, f"outcome {outcome}", reason_code or "")
    conn.commit()


def confirm_next_date(conn, case_id, rec, today: date, changed_by=None):
    d = rec["date"].isoformat()
    conn.execute("UPDATE cases SET next_date=?, next_purpose=?, stage=?, confirmed=0 WHERE id=?",
                 (d, rec["purpose"], rec["purpose"], case_id))
    for item in rec["items"]:
        due = (rec["date"] - timedelta(days=PREREQ_DAYS[item] // 2)).isoformat()
        conn.execute("INSERT INTO prerequisites VALUES (?,?,?,?,?)", (case_id, rec["purpose"], item, 0, due))
    court = JUDGES[conn.execute("SELECT judge_id FROM cases WHERE id=?", (case_id,)).fetchone()[0]]["court"]
    msg = f"Next hearing: {rec['date']:%d %b}, {rec['slot']}, Court {court}, for {rec['purpose']}."
    for side in ("petitioner", "respondent", "litigant"):
        conn.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?)",
                     (case_id, side, "whatsapp", msg, today.isoformat(), None))
    audit.log(conn, "nextdate", case_id, f"next date {d} {rec['slot']}", rec["reason"], changed_by)
    conn.commit()
