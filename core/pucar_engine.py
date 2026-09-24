"""Ready-to-List on the organisers' data (PUCAR / FOSS United hackathon repo, data/).

Truth model, calibrated so that today's rules reproduce the real rates in their CSVs:
  * each hearing type has a real probability of being substantive (substantiveness_by_hearing_type)
  * a failed hearing fails for a reason, in the real proportions (hearing_failure_reasons), grouped:
      process  - awaiting process / summons / warrant return (a prerequisite, persists until returned)
      absence  - a party or counsel absent
      unready  - time sought, evidence or filing not ready
      court    - administrative, holiday, external
      unclear
  * minutes per hearing: their estimated minutes x lognormal noise; an adjournment still costs 2 min
  * the last hearing's note ("Await warrant", "Absent: Accused", "not ready") raises the matching risk

Ready-to-List levers, each switchable for the ablation:
  process_tracking   list a case only when its process is back (status known 90% of the time)
  intent_check       T-2 confirmation: half the "not ready" failures are caught before listing
  fixed_slot_cluster a real time window, an advocate's matters together: a third fewer absences
  text_signals       the planner reads the last hearing's note when ranking
  optimiser          CP-SAT day packing (priority x P(substantive) per minute), ageing quota, waitlist
  smart_next_date    next date from the purpose and the failure reason, not a flat 60 days
"""
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
from core import priority as PRIO  # noqa: E402  (the Samay case priority score)


def to_min(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def to_hhmm(minutes: float) -> str:
    minutes = int(round(minutes))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


COURT_CAL = yaml.safe_load((ROOT / "config" / "calendar.yaml").read_text())
_HOLIDAYS = set(COURT_CAL["holidays"])
for _v in COURT_CAL["vacations"]:
    _HOLIDAYS |= {_v["start"] + timedelta(days=k) for k in range((_v["end"] - _v["start"]).days + 1)}


def is_working_day(d: date) -> bool:
    """Kerala High Court calendar (config/calendar.yaml): weekends, gazetted holidays, vacations,
    second Saturdays closed, listed working Saturdays open."""
    if d in set(COURT_CAL.get("working_saturdays", [])):
        return True
    if d.weekday() in COURT_CAL["weekend_days"] or d in _HOLIDAYS:
        return False
    if COURT_CAL.get("second_saturdays_closed") and d.weekday() == 5 and 8 <= d.day <= 14:
        return False
    return True

CFG = yaml.safe_load((ROOT / "config" / "pucar.yaml").read_text())
DAY = CFG["court_day"]
LEVERS = ["prefiling", "process_tracking", "intent_check", "fixed_slot_cluster", "text_signals", "optimiser",
          "smart_next_date"]


def default_data_dir() -> Path:
    """Inside the organisers' repo (submissions/<team>/) their data/ is two levels up."""
    for p in (ROOT.parent.parent / "data", ROOT / "data" / "pucar"):
        if (p / "roster_sample_100.csv").exists():
            return p
    raise FileNotFoundError("Organisers' data/ not found")


def norm(s: str) -> str:
    return str(s).strip().upper().replace(" ", "_").replace("S351_BNSS", "S351_BNSS")


# ---------------------------------------------------------------- data

def load(data_dir=None, roster="roster_sample_100.csv"):
    d = Path(data_dir or default_data_dir())
    ref = pd.read_csv(d / "hearing_type_reference.csv").rename(columns={
        "Hearing Purpose": "type", "Time it takes for hearing (mins) - estimated": "minutes",
        "Time to next hearing given this is the purpose (days)": "gap_days",
        "Mean Hearings per Case": "mean_hearings"}).set_index("type")
    sub = pd.read_csv(d / "substantiveness_by_hearing_type.csv").set_index("hearingType")
    ref["p_sub"] = sub["Substantive Hearings (percentage probability)"] / 100
    fail = pd.read_csv(d / "hearing_failure_reasons.csv").set_index("hearingType")
    for g, cols in CFG["reason_groups"].items():
        ref[f"n_{g}"] = fail[cols].sum(axis=1)
    counted = ref[[f"n_{g}" for g in CFG["reason_groups"]]].sum(axis=1).clip(lower=1)
    for g in CFG["reason_groups"]:
        ref[f"share_{g}"] = ref[f"n_{g}"] / counted
    cal = pd.read_csv(d / "court_calendar.csv")
    workdays = [date.fromisoformat(x) for x in cal[cal.is_working_day == "Yes"].date]
    r = pd.read_csv(d / roster) if (d / roster).exists() else pd.read_csv(roster)
    return {"ref": ref, "calendar": cal, "workdays": workdays, "roster": r, "dir": d}


def signals(note: str) -> dict:
    t = str(note).lower()
    absent_line = next((ln for ln in t.splitlines() if ln.startswith("absent:")), "")
    return {"sig_process": any(k in t for k in CFG["signals"]["process"]),
            "sig_unready": any(k in t for k in CFG["signals"]["unready"]),
            "sig_absence": ("accused" in absent_line) or ("complainant" in absent_line)}


def cases_frame(data, start: date):
    r = data["roster"].copy()
    c = pd.DataFrame({
        "id": r.case_number, "filing_date": pd.to_datetime(r.filing_date), "advocate": r.advocate_id,
        "purpose": r.purpose_of_next_hearing.map(norm), "stage": r.current_stage.map(norm),
        "hearings_held": r.total_hearings_held})
    c["age_years"] = (pd.Timestamp(start) - c.filing_date).dt.days / 365.25
    c["old"] = c.age_years >= CFG["old_years"]
    sig = pd.DataFrame([signals(n) for n in r.last_hearing_summary])
    return pd.concat([c, sig], axis=1)


# ---------------------------------------------------------------- truth model

def failure_rates(ref, c):
    """Per case, per group: P(fail for that reason), conditional on the process being back.
    Calibrated so the type's overall substantive rate equals the real one under today's rules."""
    boost = CFG["levers"]["text_signals"]["boost"]
    out = {}
    f_proc = (1 - ref.p_sub) * ref.share_process
    q = (1 - ref.p_sub / (1 - f_proc).clip(lower=0.05)).clip(0, 0.99)   # other failures, given process ok
    others = [g for g in CFG["reason_groups"] if g != "process"]
    tot = ref[[f"share_{g}" for g in others]].sum(axis=1).clip(lower=1e-9)
    for g in others:
        base = (q * ref[f"share_{g}"] / tot).reindex(c.purpose).values
        sig = c.get(f"sig_{g}")
        if sig is not None:
            base = base * _signal_multiplier(sig, c.purpose, boost)
        out[g] = np.clip(base, 0, 0.95)
    pend = f_proc.reindex(c.purpose).values
    out["process"] = np.clip(pend * _signal_multiplier(c.sig_process, c.purpose, boost), 0, 0.95)
    return out


def _type_rates(ref):
    """Failure probabilities for a case with no history yet: its type's averages."""
    out = {}
    for t, f in ref.iterrows():
        q = max(0.0, 1 - f.p_sub / max(0.05, 1 - (1 - f.p_sub) * f.share_process))
        tot = max(1e-9, 1 - f.share_process)
        out[t] = {"p_absence": q * f.share_absence / tot, "p_unready": q * f.share_unready / tot,
                  "p_court": q * f.share_court / tot, "p_unclear": q * f.share_unclear / tot}
    return out


def _signal_multiplier(sig, purpose, boost):
    """Cases whose last note signals a risk get `boost` times the risk of those that do not,
    with each hearing type's average held at the real rate: s*m_sig + (1-s)*m_none = 1."""
    s = sig.groupby(purpose).transform("mean").values
    m_none = 1 / (s * boost + 1 - s)
    return np.where(sig, boost * m_none, m_none)


@dataclass
class Case:
    id: str
    purpose: str
    stage: str
    advocate: str
    age_days: float
    old: bool
    due: int                  # day index next listed / eligible
    pending_until: int        # process back on this day index (-1 = not pending)
    p_absence: float
    p_unready: float
    p_court: float
    p_unclear: float
    first_listed: int = -1
    first_heard: int = -1
    reached: int = 0
    heard: int = 0
    disposed: bool = False
    disposed_day: int = -1
    late: bool = False        # complaint filed after the limitation period
    new: bool = False
    attempt: int = 0          # listings so far for the current purpose (1st, 2nd, deferred)
    hearings0: int = 0        # hearings held before the simulation started
    urgency_value: float = 0.0  # court-set urgency read from the last order (static)
    attendance0: float = 1.0  # share of required people present at the last real hearing
    score: float = 0.0        # Samay score at the last planning
    last_failure: str = ""    # why the last listing for this purpose failed


def samay_score(k, ref, fp_age: int) -> float:
    """The Samay priority score for a case as it stands today (core/priority.py, dynamic version):
    age grows, stage and hearings held move, attendance is the last listing's, urgency is the
    court's own words in the last real order."""
    w = PRIO.WEIGHTS
    age_value = min(1.0, k.age_days / 365.25 / fp_age)
    rate = float(ref.loc[k.purpose].p_sub) if k.purpose in ref.index else 0.5
    share = 0.0 if k.last_failure == "absence" else (k.attendance0 if k.attempt == 0 and k.reached == 0 else 1.0)
    readiness_value = min(1.0, rate * (PRIO.ATTENDANCE_FLOOR + (1 - PRIO.ATTENDANCE_FLOOR) * share))
    stage = k.stage if k.stage in PRIO.STAGES else k.purpose
    stage_no = PRIO.STAGES.index(stage) if stage in PRIO.STAGES else 0
    disposal_value = stage_no / (len(PRIO.STAGES) - 1)
    exp = _EXPECTED.get(stage, 1) or 1
    churn_value = float(np.clip((k.hearings0 + k.reached) / exp - 1, 0, 1))
    return (w["age"] * age_value + w["readiness"] * readiness_value + w["disposal"] * disposal_value
            + w["churn"] * churn_value + w["urgency"] * k.urgency_value)


_EXPECTED = {}


def escalation(attempt: int) -> dict:
    """The rule for a case listed `attempt` times for the same purpose (config: escalation)."""
    e = CFG["escalation"]
    if attempt + 1 >= e["deferred"]["from_attempt"]:
        return {"key": "deferred", **e["deferred"]}
    if attempt + 1 == 2:
        return {"key": "second", **e["second"]}
    return {"key": "first", **e["first"]}


def _prefiling_process_scale(purpose):
    pf = CFG["levers"]["prefiling"]
    if purpose in pf["stages"]:
        return 1 - pf["process_removed"]
    if purpose in pf["summons_stages"]:
        return 1 - pf["summons_process_removed"]
    return 1.0


def _advance(purpose, stage, flow):
    """Next purpose after a substantive hearing. Side hearings return where config says
    (delay condonation -> cognizance, warrant executed -> plea, reports -> the current stage)."""
    if purpose not in flow:
        back = CFG["side_return"].get(purpose, "stage")
        if back == "stage":
            return stage if stage in flow else "APPEARANCE"
        return back
    i = flow.index(purpose)
    return flow[i + 1] if i + 1 < len(flow) else None  # None = judgment pronounced, disposed


# ---------------------------------------------------------------- simulation

def simulate(data, start: date, days=60, rtl=True, levers=None, capacity=None, seed=7):
    levers = set(LEVERS if levers is None else levers) if rtl else set()
    rng = np.random.default_rng(seed)
    ref, flow = data["ref"], CFG["stage_flow"]
    wd = working_days_from(data, start, days)
    days = len(wd)
    c = cases_frame(data, start)
    fr = failure_rates(ref, c)
    capacity = capacity or DAY["scoring_capacity_minutes"]
    blocks = DAY["blocks"]
    block_of = {p: b["name"] for b in blocks for p in b["purposes"]}
    gross = sum(to_min(b["end"]) - to_min(b["start"]) for b in blocks)
    scale = capacity / gross   # lets --capacity 420 stretch the same two blocks
    cap_block = {b["name"]: (to_min(b["end"]) - to_min(b["start"])) * scale -
                 (DAY["opening_minutes"] if b is blocks[0] else 0) for b in blocks}

    # Same initial state for both arms: due dates spread over the horizon, process state drawn once
    order = rng.permutation(len(c))
    cases = []
    for n, i in enumerate(order):
        r = c.iloc[i]
        p_pend = fr["process"][i]
        if "prefiling" in levers:
            p_pend *= _prefiling_process_scale(r.purpose)
        pending = rng.random() < p_pend
        note = str(data["roster"].last_hearing_summary.iloc[i]) if "last_hearing_summary" in data["roster"] else ""
        need = PRIO.REQUIRED_PEOPLE.get(r.purpose, PRIO.DEFAULT_PEOPLE)
        att0 = sum(1 for q in need if q in PRIO.present_people(note)) / len(need)
        urg0 = next((v for k, v in PRIO.URGENCY if k in note.lower()), 0.0)
        cases.append(Case(r.id, r.purpose, r.stage, r.advocate, r.age_years * 365.25, bool(r.old),
                          hearings0=int(r.hearings_held), urgency_value=urg0, attendance0=att0,
                          # every case already has a next date inside today's 60-day cycle
                          due=n * min(days, CFG["next_date"]["initial_spread_working_days"]) // len(c),
                          pending_until=int(rng.integers(3, 25)) if pending else -1,
                          p_absence=fr["absence"][i], p_unready=fr["unready"][i], p_court=fr["court"][i],
                          p_unclear=fr["unclear"][i]))
    rng = np.random.default_rng(seed + 1)   # outcome draws shared across arms
    rows, schedule, next_gaps, journey = [], [], [], []
    minutes_ref = ref.minutes.to_dict()
    _EXPECTED.clear()
    _EXPECTED.update(PRIO.expected_hearings({t: float(r["Median Hearings per Case"]) for t, r in ref.iterrows()}))
    fp_age = PRIO.full_points_age(data["roster"].filing_date, start)   # computed once per roster, then fixed

    def p_sub_plan(k: Case, d):
        """What the planner believes: with text signals it sees each case's own risks, without
        it only the type average."""
        pa, pu = k.p_absence, k.p_unready
        if "text_signals" not in levers:
            pa = pu = None
        f = ref.loc[k.purpose]
        base_other = 1 - f.p_sub / max(0.05, 1 - (1 - f.p_sub) * f.share_process)
        if pa is None:  # type averages only
            tot = max(1e-9, 1 - f.share_process)
            pa, pu = base_other * f.share_absence / tot, base_other * f.share_unready / tot
            rest = base_other - pa - pu
        else:
            rest = k.p_court + k.p_unclear
        fx = CFG["levers"]
        if "fixed_slot_cluster" in levers:
            pa *= 1 - fx["fixed_slot_cluster"]["removed"]
        if "intent_check" in levers:
            pu *= 1 - fx["intent_check"]["removed"]
        if "prefiling" in levers and k.purpose in fx["prefiling"]["stages"]:
            pu *= 1 - fx["prefiling"]["unready_removed"]
        pend = 0.0 if "process_tracking" in levers else (1 - f.p_sub) * f.share_process
        return max(0.02, (1 - pend) * (1 - min(0.98, pa + pu + rest)))

    nf = CFG["new_filings"]
    type_avg = _type_rates(ref)
    advs = c.advocate.unique()
    leave = {date.fromisoformat(str(x)) for x in CFG["judge_leave"]["dates"]}
    for d in range(days):
        if wd[d] in leave:  # judge on leave: no sitting
            for k in cases:
                if not k.disposed and k.due <= d:
                    if rtl:
                        k.due = d + 1
                    else:
                        _reschedule(k, d, "court", set(), rng, next_gaps, not_reached=True)
            rows.append({"day": d + 1, "date": wd[d], "listed": 0, "called": 0, "reached": 0, "heard": 0,
                         "minutes_used": 0.0, "type_switches": 0, "leave": True})
            continue
        for i in range(rng.poisson(nf["per_day"])):  # new complaints arrive every working day
            pr = type_avg["ADMISSION"]
            cases.append(Case(f"NEW-{d:03d}-{i}", "ADMISSION", "ADMISSION", str(rng.choice(advs)), 0.0, False,
                              due=d + int(rng.integers(3, 10)), pending_until=-1, late=rng.random() < nf["late_share"],
                              new=True, **pr))
        live = [k for k in cases if not k.disposed and k.due <= d]
        if rtl and CFG["escalation"]["deferred"]["hold_until_cured"]:
            # Deferred cases (3rd+ listing) whose last failure was process are held until it is back
            held = [k for k in live if escalation(k.attempt)["key"] == "deferred" and k.last_failure == "process"
                    and k.pending_until > d]
            for k in held:
                k.due = k.pending_until
            live = [k for k in live if k not in held]
        # Process tracking: a case whose summons/warrant has not come back is not listed
        if "process_tracking" in levers:
            acc = CFG["levers"]["process_tracking"]["status_accuracy"]
            ready = []
            for k in live:
                if k.pending_until > d and rng.random() < acc:
                    k.due = k.pending_until  # listed the day it is back
                else:
                    ready.append(k)
            live = ready
        if "optimiser" in levers:
            listed, waitlist = _pack(live, d, p_sub_plan, cap_block, block_of, minutes_ref, ref, fp_age)
        else:
            live.sort(key=lambda k: (k.purpose not in CFG["urgent_purposes"], k.due, -k.age_days))
            listed, waitlist = live[:CFG["baseline_listed_per_day"]], []
            if rtl:  # levers without the optimiser: same list, but called in blocks
                listed.sort(key=lambda k: block_of.get(k.purpose, blocks[-1]["name"]))

        # Call the list block by block, grouped by purpose and advocate when optimised
        used = {b["name"]: 0.0 for b in blocks}
        reached = heard = 0
        mins_heard = 0.0
        switches = 0
        prev = {}
        by_block = {b["name"]: [] for b in blocks}
        for k in listed:
            by_block[block_of.get(k.purpose, blocks[-1]["name"])].append(k)
        called = standby_called = 0
        for bname, items in by_block.items():
            if "optimiser" in levers:
                items.sort(key=lambda k: (k.purpose not in CFG["urgent_purposes"], k.purpose, k.advocate))
            queue = items + ([w for w in waitlist if block_of.get(w.purpose) == bname] if waitlist else [])
            t0 = to_min(next(b for b in blocks if b["name"] == bname)["start"]) + (
                DAY["opening_minutes"] if bname == blocks[0]["name"] else 0)
            for k in queue:
                standby = k not in items
                if used[bname] >= cap_block[bname]:
                    if standby:
                        break
                    _reschedule(k, d, "court", levers, rng, next_gaps, not_reached=True)
                    continue
                called += 1
                change = DAY["changeover_same"] if prev.get(bname) in (None, k.purpose) else DAY["changeover_switch"]
                switches += prev.get(bname) not in (None, k.purpose)
                prev[bname] = k.purpose
                used[bname] += change
                k.reached += 1
                k.attempt += 1
                esc = escalation(k.attempt - 1)["key"]
                reached += 1
                if k.first_listed < 0:
                    k.first_listed = d
                ptype = k.purpose
                standby_called += standby
                outcome = _outcome(k, d, levers, rng, standby)
                start_min = t0 + used[bname] - change
                if outcome == "substantive":
                    m = minutes_ref[k.purpose] * rng.lognormal(0, DAY["duration_sigma"])
                    if k.purpose == "ADMISSION" and k.late and "prefiling" in levers:
                        m += nf["admission_extra_minutes_with_prefiling"]
                    used[bname] += m
                    mins_heard += m
                    heard += 1
                    k.heard += 1
                    if k.first_heard < 0:
                        k.first_heard = d
                    nxt = _advance(k.purpose, k.stage, CFG["stage_flow"])
                    if k.purpose == "ADMISSION" and k.late and "prefiling" not in levers:
                        nxt = "DELAY_CONDONATION_HEARING"  # limitation found late: a separate hearing track
                    if nxt is None:
                        k.disposed = True
                        k.disposed_day = d
                    else:
                        if k.purpose in CFG["stage_flow"]:
                            k.stage = k.purpose
                        k.purpose = nxt
                        k.attempt, k.last_failure = 0, ""
                        p_pend = (1 - ref.loc[nxt].p_sub) * ref.loc[nxt].share_process
                        if "prefiling" in levers:
                            p_pend *= _prefiling_process_scale(nxt)
                        if rng.random() < p_pend:  # the next step needs process again
                            lo, hi = (CFG["levers"]["prefiling"]["summons_return_days"]
                                      if "prefiling" in levers and nxt in CFG["levers"]["prefiling"]["summons_stages"]
                                      else (3, 25))
                            k.pending_until = d + int(rng.integers(lo, hi))
                        _reschedule(k, d, "substantive", levers, rng, next_gaps, ref=ref)
                else:
                    used[bname] += DAY["adjourned_minutes"]
                    mins_heard += DAY["adjourned_minutes"]
                    k.last_failure = outcome
                    _reschedule(k, d, outcome, levers, rng, next_gaps)
                journey.append({"day": d, "date": wd[d], "case_number": k.id, "hearing_type": ptype,
                                "block": bname, "start": to_hhmm(start_min), "outcome": outcome,
                                "from_waitlist": standby, "attempt": k.attempt, "listing": esc,
                                "score": round(k.score, 1)})
                if rtl and d < 10:
                    schedule.append({"date": wd[d].isoformat(), "block": bname, "expected_start": to_hhmm(start_min),
                                     "window": f"{to_hhmm((start_min // 30) * 30)}-{to_hhmm((start_min // 30) * 30 + 60)}",
                                     "case_number": k.id, "hearing_type": ptype,
                                     "advocate_id": k.advocate, "from_waitlist": standby,
                                     "p_substantive_planned": round(p_sub_plan(k, d), 2), "simulated_outcome": outcome})
        for k in cases:
            k.age_days += 1.4  # a working day is about 1.4 calendar days
        rows.append({"day": d + 1, "date": wd[d], "listed": len(listed) + standby_called,
                     "called": called, "reached": reached, "heard": heard, "minutes_used": min(sum(used.values()), capacity),
                     "type_switches": switches, "leave": False})
    m = _metrics(pd.DataFrame(rows), cases, c, next_gaps, capacity, days)
    m["journey"] = pd.DataFrame(journey)
    m["cases"] = pd.DataFrame([{"case_number": k.id, "purpose_now": k.purpose, "stage_now": k.stage,
                                "first_listed": k.first_listed, "first_heard": k.first_heard,
                                "disposed_day": k.disposed_day, "new_filing": k.new, "late": k.late,
                                "attempt": k.attempt, "last_failure": k.last_failure, "score": round(k.score, 1),
                                "disposed": k.disposed, "hearings_reached": k.reached, "substantive": k.heard,
                                "old": k.old, "advocate": k.advocate} for k in cases])
    m["workdays"] = wd
    return m, pd.DataFrame(schedule)


def working_days_from(data, start, n):
    """Their calendar first; past its end, our court calendar (config/calendar.yaml) takes over."""
    days = [d for d in data["workdays"] if d >= start]
    d = (days[-1] if days else start - timedelta(days=1)) + timedelta(days=1)
    while len(days) < n:
        if is_working_day(d):
            days.append(d)
        d += timedelta(days=1)
    return days[:n]


def _outcome(k: Case, d, levers, rng, standby):
    if k.pending_until > d:
        return "process"
    fx = CFG["levers"]
    pf = fx["prefiling"]
    pu_scale = 1 - pf["unready_removed"] if ("prefiling" in levers and k.purpose in pf["stages"]) else 1
    pa = k.p_absence * (1 - fx["fixed_slot_cluster"]["removed"] if "fixed_slot_cluster" in levers else 1)
    if standby:
        pa = min(0.95, pa * 1.3)  # called at short notice from the waitlist
    pu = k.p_unready * (1 - fx["intent_check"]["removed"] if "intent_check" in levers else 1) * pu_scale
    u = rng.random()
    for g, p in (("absence", pa), ("unready", pu), ("court", k.p_court), ("unclear", k.p_unclear)):
        if u < p:
            return g
        u -= p
    return "substantive"


def _reschedule(k: Case, d, outcome, levers, rng, gaps, ref=None, not_reached=False):
    wd_per_cal = 5 / 7
    if "smart_next_date" not in levers:
        gap_cal = CFG["next_date"]["baseline_gap_days"]
    elif outcome == "substantive":
        gap_cal = float(ref.loc[k.purpose].gap_days)
    elif not_reached:
        gap_cal = 1
    else:
        gap_cal = CFG["next_date"]["after_failure_days"][outcome]
        if outcome == "process" and k.pending_until > d:
            gap_cal = max(gap_cal, (k.pending_until - d) / wd_per_cal)
    gaps.append({"outcome": outcome, "purpose": k.purpose, "gap_days": gap_cal})
    k.due = d + max(1, int(round(gap_cal * wd_per_cal)))


def _pack(live, d, p_sub_plan, cap_block, block_of, minutes_ref, ref, fp_age):
    """CP-SAT knapsack per block: bail first (liberty lane), the locked ageing quota, then the rest
    by Samay score x P(substantive) per minute^0.25. The next ready cases form a same-day waitlist.
    Scheduling-layer boosts sit on top of the score: listing number (escalation) and the s.143 clock."""
    from ortools.sat.python import cp_model
    rows = []
    for k in live:
        p = p_sub_plan(k, d)
        exp = p * minutes_ref[k.purpose] * np.exp(DAY["duration_sigma"] ** 2 / 2) + (1 - p) * DAY["adjourned_minutes"] \
            + DAY["changeover_same"]
        clock = CFG["statutory_clock"]
        k.score = samay_score(k, ref, fp_age)
        value = (k.score + escalation(k.attempt)["priority_boost"]
                 + (clock["priority_boost"] if k.age_days < clock["days"] else 0)) * p  # still inside the s.143 window
        rows.append((k, block_of.get(k.purpose, list(cap_block)[-1]), exp, value))
    listed, waitlist = [], []
    for bname, cap in cap_block.items():
        cap *= DAY["fill_target"]
        items = [r for r in rows if r[1] == bname]
        urgent = [r for r in items if r[0].purpose in CFG["urgent_purposes"]]
        used = sum(r[2] for r in urgent)
        chosen = [r[0] for r in urgent]
        q_need, q_used = CFG["ageing_quota"] * cap, 0.0
        for r in sorted([r for r in items if r[0].old and r not in urgent], key=lambda r: -r[3]):
            if q_used + r[2] <= q_need and used + r[2] <= cap:
                chosen.append(r[0]); used += r[2]; q_used += r[2]
        rest = [r for r in items if r[0] not in chosen]
        rest.sort(key=lambda r: -r[3] / r[2] ** 0.25)
        rest = rest[:300]
        if rest and cap - used > 0:
            m = cp_model.CpModel()
            x = [m.NewBoolVar("") for _ in rest]
            m.Add(sum(int(r[2] * 10) * v for r, v in zip(rest, x)) <= int((cap - used) * 10))
            m.Maximize(sum(int(1000 * r[3] * r[2] ** 0.75) * v for r, v in zip(rest, x)))
            s = cp_model.CpSolver()
            s.parameters.max_time_in_seconds = 1.0
            s.parameters.num_workers = 8
            s.Solve(m)
            picked = [r[0] for r, v in zip(rest, x) if s.Value(v)]
            chosen += picked
            waitlist += [r[0] for r in rest if r[0] not in picked][:5]
        listed += chosen
    return listed, waitlist


def _metrics(daily, cases, c, gaps, capacity, days):
    old = [k for k in cases if k.old]
    heard_cases = [k for k in cases if k.first_heard >= 0 and k.first_listed >= 0]
    g = pd.DataFrame(gaps)
    total_listed = daily.listed.sum()
    return {
        "days": days,
        "listed_per_day": daily.listed.mean(),
        "reached_per_day": daily.reached.mean(),
        "substantive_per_day": daily.heard.mean(),
        "utilisation_pct": 100 * daily.minutes_used.sum() / (capacity * days),
        "reach_rate_pct": 100 * daily.reached.sum() / max(1, total_listed),
        "substantiveness_pct": 100 * daily.heard.sum() / max(1, daily.reached.sum()),
        "backlog_4y_heard_pct": 100 * np.mean([k.heard > 0 for k in old]) if old else float("nan"),
        # Days from a case's first listing to the hearing that actually moved it; cases never heard
        # are counted to the end of the horizon (censored), so failing fast is not rewarded
        "predictability_days": 1.4 * np.mean([(k.first_heard if k.first_heard >= 0 else days) - k.first_listed
                                              for k in cases if k.first_listed >= 0]),
        "next_date_gap_days": g.gap_days.mean() if not g.empty else float("nan"),
        "wasted_listings": int(daily.reached.sum() - daily.heard.sum() + (total_listed - daily.reached.sum())),
        "disposed": sum(k.disposed for k in cases),
        "type_switches_per_day": daily.type_switches.mean(),
        "daily": daily,
    }


REQUIRED_COLUMNS = {"case_number": "the case's number", "filing_date": "date the case was filed (YYYY-MM-DD)",
                    "advocate_id": "the advocate's ID, used to group their matters",
                    "current_stage": "the stage the case is at", "purpose_of_next_hearing": "what the next hearing is for"}


def validate_roster(df: pd.DataFrame) -> list:
    """Plain-language problems with an uploaded docket; empty list means it can be planned."""
    problems = [f"Missing column `{c}`: {why}." for c, why in REQUIRED_COLUMNS.items() if c not in df.columns]
    if problems:
        return problems
    bad_dates = pd.to_datetime(df.filing_date, errors="coerce").isna().sum()
    if bad_dates:
        problems.append(f"{bad_dates} rows have a filing_date that is not a date.")
    known = set(CFG["priority"])
    unknown = sorted(set(df.purpose_of_next_hearing.map(norm)) - known)
    if unknown:
        problems.append("Unknown hearing purposes: " + ", ".join(unknown) + ". Known: " + ", ".join(sorted(known)) + ".")
    return problems


def with_roster(data, roster: pd.DataFrame):
    """The organisers' reference tables with a different docket (an uploaded file)."""
    r = roster.copy()
    if "last_hearing_summary" not in r:
        r["last_hearing_summary"] = ""
    if "total_hearings_held" not in r:
        cols = [c for c in r.columns if c.startswith("hearings_")]
        r["total_hearings_held"] = r[cols].sum(axis=1) if cols else 0
    if "party_id" not in r:
        r["party_id"] = [f"PARTY-{i:05d}" for i in range(len(r))]
    r["filing_date"] = pd.to_datetime(r.filing_date).dt.strftime("%Y-%m-%d")
    return {**data, "roster": r}


def judge_docket(data, total=3000, seed=42):
    """One judge's docket: the 100 real sample cases plus generated cases (their generator)
    up to `total`. Generated case numbers carry a G- prefix so the real 100 stay traceable."""
    if len(data["roster"]) >= total:
        return {**data, "roster": data["roster"].assign(sample=True)}
    extra = scale_roster(data, total - len(data["roster"]), seed)["roster"]
    extra["case_number"] = "G-" + extra.case_number
    real = data["roster"].assign(sample=True)
    return {**data, "roster": pd.concat([real, extra.assign(sample=False)], ignore_index=True)}


def scale_roster(data, n=3000, seed=42):
    """Their own generator (scripts/generate_roster.py): bootstrap rows, fresh IDs."""
    import importlib.util
    here = [data["dir"].parent / "scripts" / "generate_roster.py", ROOT / "scripts" / "pucar_generate_roster.py"]
    path = next(p for p in here if p.exists())
    spec = importlib.util.spec_from_file_location("gen", path)
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:  # generate from whatever docket is loaded
        data["roster"].drop(columns=["sample"], errors="ignore").to_csv(f.name, index=False)
    out = gen.generate(n, seed, f.name)
    out["filing_date"] = out.filing_date.dt.strftime("%Y-%m-%d")
    return {**data, "roster": out}
