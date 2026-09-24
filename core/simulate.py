"""Agent simulation of one judge's roster: today's rules vs Ready-to-List.

Calibrated to the manual's case study (Justice Sehgal): 3,000 pending cases, 3 months,
~60 listed a day of which ~20 are heard and ~10 are effective, flat 60-day next date.
A quarter of cases are under a year old, 1 in 6 over four years.

Both arms share the roster, the random seed and the advocate agents (core.agents).
The judge's own style applies in both arms, because it is how that court runs today:
  Sehgal  - unheard cases carry over to the same weekday next week
  Dimakar - arbitration bench, cover sheet mandatory before arguments in old cases
  Joshi   - fresh matters first (the locked ageing quota still binds under Ready-to-List)
Ready-to-List adds: T-2 intent check, readiness gate, cover-sheet request for 4+ year
cases, urgent bypass, ageing quota, fill to 95% of expected minutes, advocate clustering
in fixed slots, and a purpose-based next date.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from core import agents
from core.config import (BALANCE, FILL_TARGET, HEARING_TYPES, JUDGES, LOCKED, MODEL, PREREQ_DAYS, STAGE_FLOW,
                         URGENT_PURPOSES, capacity, next_stage)

MIX, BASE, FX, DEF = MODEL["case_mix"], MODEL["baseline"], MODEL["show_effects"], MODEL["defects"]
# Judge styles come from config/judge_rules.yaml, keyed by surname
STYLES = {j["name"].split()[-1]: {
    "minutes": capacity(jid), "baseline_list": BASE["listed_per_day"],
    "carry_over": bool(j.get("weekly_carry_over")), "cover_sheet": bool(j.get("cover_sheet_required")),
    "fresh_first": bool(j.get("fresh_first"))} for jid, j in JUDGES.items()}
TYPES = agents.AGENT_TYPES
COVER_SHEET_RATE = MODEL["advocates"]["cover_sheet_rate"]
FOUR_YEARS, FIVE_YEARS = 4 * 365, 5 * 365
READY_SHARE = MIX["prereq_ready_share"]
AGE_MIX = [(lo * 365, hi * 365, w) for lo, hi, w in MIX["age_buckets"]]


@dataclass
class Case:
    id: int
    age: float           # days since filing
    purpose: str
    due: int             # sim day it is next due
    prereq_ready: int    # sim day its prerequisites complete
    pet: int
    res: int             # -1 = party in person
    urgent: bool
    summary: bool = False
    disposed: bool = False
    defect: bool = False     # an uncaught critical defect (court fee, vakalatnama, limitation)


def _roster(rng, n_cases, n_adv, diligent_share, chronic_share, cover_sheet):
    busy = max(0.0, 1 - diligent_share - chronic_share)
    types = rng.choice(TYPES, size=n_adv, p=[diligent_share, busy, chronic_share])
    cases = []
    for i in range(n_cases):
        lo, hi, _ = AGE_MIX[rng.choice(len(AGE_MIX), p=[m[2] for m in AGE_MIX])]
        age = rng.uniform(lo, hi)
        urgent = rng.random() < MIX["urgent_share"]
        if urgent:
            purpose, age = rng.choice(sorted(URGENT_PURPOSES)), rng.uniform(0, 60)
        else:
            purpose = STAGE_FLOW[int(np.clip(rng.normal(age / 600, 1.2), 0, len(STAGE_FLOW) - 1))]
        items = HEARING_TYPES[purpose]["prereqs"]
        # Some pending prerequisites are done; the rest finish within a few weeks
        ready = 0 if (not items or rng.random() < READY_SHARE) else int(rng.integers(1, 40))
        pet = int(rng.integers(0, n_adv))
        summary = cover_sheet and age >= FOUR_YEARS and rng.random() < COVER_SHEET_RATE[types[pet]]
        defect = rng.random() < DEF["share_with_critical"]
        cases.append(Case(i, age, purpose, int(rng.integers(0, 60)), ready, pet,
                          -1 if rng.random() < 0.05 else int(rng.integers(0, n_adv)), urgent, summary,
                          defect=defect))
    return types, cases


def run(days=60, style="Sehgal", rtl_on=True, diligent_share=None, chronic_share=None,
        overbooking=0.0, seed=42, n_cases=3000, n_adv=250, balance=BALANCE, prefiling=None):
    """prefiling: run the pre-filing check (defaults to rtl_on). Without it, critical defects
    surface only in court and sink the hearing."""
    diligent_share = MODEL["advocates"]["mix"]["diligent"] if diligent_share is None else diligent_share
    chronic_share = MODEL["advocates"]["mix"]["chronic"] if chronic_share is None else chronic_share
    prefiling = rtl_on if prefiling is None else prefiling
    st = STYLES[style]
    rng_roster = np.random.default_rng(seed)
    types, cases = _roster(rng_roster, n_cases, n_adv, diligent_share, chronic_share, st["cover_sheet"])
    rng = np.random.default_rng(seed + 1)
    minutes = st["minutes"]
    warned = np.zeros(n_adv, bool)
    trips = np.zeros(n_adv, int)
    wasted = np.zeros(n_adv, int)
    next_id, rows = n_cases, []

    for day in range(days):
        for _ in range(rng.poisson(MIX["new_filings_per_day"])):  # fresh filings keep arriving
            urgent = rng.random() < MIX["new_urgent_share"]
            cases.append(Case(next_id, 0, rng.choice(sorted(URGENT_PURPOSES)) if urgent else "admission",
                              day + int(rng.integers(1, 10)), day, int(rng.integers(0, n_adv)),
                              int(rng.integers(0, n_adv)), urgent))
            next_id += 1

        if prefiling:  # the check catches most critical defects; the advocate fixes them before listing
            for c in cases:
                if c.defect and rng.random() < DEF["caught_by_prefiling"]:
                    c.defect = False
        live = [c for c in cases if not c.disposed]
        due = [c for c in live if c.due <= day]
        load = np.bincount([c.pet for c in due], minlength=n_adv)

        if rtl_on:
            confirmed = {c.id: agents.will_confirm(rng, types[c.pet]) for c in due}
            for c in due:  # T-7 cover-sheet request for old cases
                if c.age >= FOUR_YEARS and not c.summary:
                    c.summary = rng.random() < COVER_SHEET_RATE[types[c.pet]]
            listed, waitlist = _rtl_select(due, confirmed, day, minutes, overbooking, types, st["fresh_first"], balance)
        else:
            confirmed = {c.id: False for c in due}
            key = (lambda c: (not c.urgent, c.age >= 365, c.due)) if st["fresh_first"] else \
                  (lambda c: (not c.urgent, c.due, -c.age))
            listed = sorted(due, key=key)[:st["baseline_list"]]
            waitlist = []

        here = np.bincount([c.pet for c in listed], minlength=n_adv)
        used, heard, effective = 0.0, 0, 0
        standby = set()
        for c in _with_waitlist(listed, waitlist, lambda: used, minutes, standby):
            t = types[c.pet]
            clashes = min(3, max(0, int(load[c.pet]) - int(here[c.pet])))
            bundled = rtl_on and here[c.pet] >= 2
            ps = agents.p_show(t, rtl_on, bundled, confirmed[c.id], clashes, warned[c.pet])
            if c.id in standby:
                ps *= FX["waitlist_call_factor"]  # called from the waitlist at short notice
            show_pet = rng.random() < ps
            show_res = rng.random() < (agents.p_show(types[c.res], rtl_on, False, False, 0, warned[c.res])
                                       if c.res >= 0 else agents.PARTY_IN_PERSON_SHOW)
            trips[c.pet] += 1
            if used >= minutes:  # time ran out: everyone still waiting goes home
                wasted[c.pet] += 1
                _reschedule(c, day, rtl_on, False, st, rng)
                continue
            if not (show_pet and show_res):
                used += MODEL["durations"]["adjourn_minutes"]
                wasted[c.pet] += 1
                if rtl_on and confirmed[c.id] and not show_pet and t == "busy":
                    warned[c.pet] = True  # costs warning after a confirmed no-show
                _reschedule(c, day, rtl_on, False, st, rng)
                continue
            heard += 1
            old_unsum = c.age >= FOUR_YEARS and not c.summary
            dur = HEARING_TYPES[c.purpose]["duration"] * rng.lognormal(0, MODEL["durations"]["lognormal_sigma"]) * \
                (MODEL["effective"]["old_unsummarised_overrun"] if old_unsum else 1.0)
            used += dur
            p_eff = agents.p_effective_given_heard(float(c.prereq_ready <= day), old_unsum, t, confirmed[c.id])
            if c.defect:
                p_eff *= 1 - DEF["critical_fail_rate"]
                c.defect = rng.random() < 0.5  # the court points it out; half get fixed before next time
            if rng.random() < p_eff:
                effective += 1
                if c.purpose == "final":
                    c.disposed = True
                    continue
                c.purpose = next_stage(c.purpose)
                items = HEARING_TYPES[c.purpose]["prereqs"]
                c.prereq_ready = day + (max(PREREQ_DAYS[i] for i in items) + int(rng.integers(0, 10)) if items else 0)
                _reschedule(c, day, rtl_on, True, st, rng)
            else:
                wasted[c.pet] += 1
                _reschedule(c, day, rtl_on, False, st, rng)

        for c in live:
            c.age += 1
        pending = [c for c in cases if not c.disposed]
        gaps = [c.due - day for c in listed if not c.disposed]
        rows.append({
            "day": day + 1, "listed": len(listed) + len(standby), "heard": heard, "effective": effective,
            "utilisation": min(used, minutes) / minutes * 100,
            "predictability": heard / len(listed) * 100 if listed else 0,
            "substantiveness": effective / heard * 100 if heard else 0,
            "backlog_3y": sum(c.age >= 3 * 365 for c in pending),
            "backlog_4y": sum(c.age >= FOUR_YEARS for c in pending),
            "backlog_5y": sum(c.age >= FIVE_YEARS for c in pending),
            "disposed": sum(c.disposed for c in cases),
            "next_date_gap": float(np.mean(gaps)) * 7 / 5 if gaps else 0,  # working days -> calendar days
        })

    return pd.DataFrame(rows), pd.DataFrame({"type": types, "trips": trips, "wasted": wasted})


def _rtl_select(due, confirmed, day, minutes, overbook, types, fresh_first, balance):
    cap = minutes * (FILL_TARGET + overbook)
    gate = LOCKED["readiness_gate"]

    def readiness(c):
        summary_ok = c.age < FOUR_YEARS or c.summary
        return 30 * 0.85 + 40 * (c.prereq_ready <= day) + 20 * confirmed[c.id] + 10 * summary_ok

    def show(c):
        return agents.p_show(types[c.pet], True, False, confirmed[c.id], 0)

    def exp_min(c):
        return HEARING_TYPES[c.purpose]["duration"] * show(c) + 2 * (1 - show(c))

    def prio(c):
        fresh = 40 if (fresh_first and c.age < 365) else 0
        return HEARING_TYPES[c.purpose]["priority"] + c.age / 30 * 0.5 + readiness(c) * 0.5 + fresh

    listed = [c for c in due if c.urgent]
    used = sum(exp_min(c) for c in listed)
    q_used = 0.0
    for c in sorted([c for c in due if not c.urgent and c.age >= FIVE_YEARS], key=lambda c: -prio(c)):
        m = exp_min(c)
        if q_used + m > LOCKED["ageing_quota"] * cap:
            break
        listed.append(c); used += m; q_used += m
    chosen = {c.id for c in listed}
    # See `balance` in config/judge_rules.yaml: throughput vs disposal
    rest = sorted([c for c in due if c.id not in chosen and readiness(c) >= gate],
                  key=lambda c: -prio(c) * show(c) / exp_min(c) ** balance)
    for c in rest:
        if used + exp_min(c) <= cap:
            listed.append(c); chosen.add(c.id); used += exp_min(c)
    waitlist = [c for c in rest if c.id not in chosen][:10]
    chosen |= {c.id for c in waitlist}
    # Unready cases are not listed: they move to when their prerequisites are done, no wasted trip
    for c in due:
        if c.id not in chosen and readiness(c) < gate:
            c.due = max(day + 1, c.prereq_ready)
    return listed, waitlist


def _with_waitlist(listed, waitlist, used_now, minutes, standby):
    """Walk the list; when it runs out with time to spare, call waitlisted cases."""
    yield from listed
    for c in waitlist:
        if used_now() >= minutes * 0.9:
            break
        standby.add(c.id)
        yield c


def _reschedule(c, day, rtl_on, was_effective, st, rng):
    if not was_effective and st["carry_over"]:
        c.due = day + BASE["carry_over_working_days"]  # Sehgal: same weekday next week until heard
    elif not rtl_on:
        c.due = day + BASE["flat_gap_working_days"]  # flat 60 calendar days
    elif was_effective:
        c.due = day + max(1, HEARING_TYPES[c.purpose]["ideal_gap"] * 5 // 7, c.prereq_ready - day)
    else:
        c.due = day + int(rng.integers(3, 8))


def summary(base: pd.DataFrame, rtl: pd.DataFrame, base_adv, rtl_adv) -> dict:
    def pct(a, b):
        return (b - a) / a * 100 if a else 0.0
    return {
        "listed_per_day": (base.listed.mean(), rtl.listed.mean()),
        "heard_per_day": (base.heard.mean(), rtl.heard.mean()),
        "effective_per_day": (base.effective.mean(), rtl.effective.mean(), pct(base.effective.mean(), rtl.effective.mean())),
        "utilisation": (base.utilisation.mean(), rtl.utilisation.mean()),
        "predictability": (base.predictability.mean(), rtl.predictability.mean()),
        "substantiveness": (base.substantiveness.mean(), rtl.substantiveness.mean()),
        "backlog_5y_change": (base.backlog_5y.iloc[-1] - base.backlog_5y.iloc[0],
                              rtl.backlog_5y.iloc[-1] - rtl.backlog_5y.iloc[0]),
        "backlog_4y_change": (base.backlog_4y.iloc[-1] - base.backlog_4y.iloc[0],
                              rtl.backlog_4y.iloc[-1] - rtl.backlog_4y.iloc[0]),
        "disposed": (base.disposed.iloc[-1], rtl.disposed.iloc[-1]),
        "next_date_gap": (base.next_date_gap.mean(), rtl.next_date_gap.mean()),
        "wasted_trips": (base_adv.wasted.sum(), rtl_adv.wasted.sum(), pct(base_adv.wasted.sum(), rtl_adv.wasted.sum())),
    }
