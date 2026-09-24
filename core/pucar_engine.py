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

from core.config import ROOT, to_hhmm, to_min

CFG = yaml.safe_load((ROOT / "config" / "pucar.yaml").read_text())
DAY = CFG["court_day"]
LEVERS = ["process_tracking", "intent_check", "fixed_slot_cluster", "text_signals", "optimiser", "smart_next_date"]


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
            s = sig.groupby(c.purpose).transform("mean").values
            k = np.clip((1 - s * boost) / np.clip(1 - s, 1e-9, None), 0.2, 1.0)
            base = base * np.where(sig, boost, k)
        out[g] = np.clip(base, 0, 0.95)
    pend = f_proc.reindex(c.purpose).values
    s = c.sig_process.groupby(c.purpose).transform("mean").values
    k = np.clip((1 - s * boost) / np.clip(1 - s, 1e-9, None), 0.2, 1.0)
    out["process"] = np.clip(pend * np.where(c.sig_process, boost, k), 0, 0.95)
    return out


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


def _advance(purpose, stage, flow):
    """Next purpose after a substantive hearing: side purposes return to the stage's flow."""
    cur = purpose if purpose in flow else (stage if stage in flow else "APPEARANCE")
    if purpose not in flow:
        return cur
    i = flow.index(cur)
    return flow[i + 1] if i + 1 < len(flow) else None  # None = judgment pronounced, disposed


# ---------------------------------------------------------------- simulation

def simulate(data, start: date, days=60, rtl=True, levers=None, capacity=None, seed=7):
    levers = set(LEVERS if levers is None else levers) if rtl else set()
    rng = np.random.default_rng(seed)
    ref, flow = data["ref"], CFG["stage_flow"]
    wd = [d for d in data["workdays"] if d >= start][:days]
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
        pending = rng.random() < fr["process"][i]
        cases.append(Case(r.id, r.purpose, r.stage, r.advocate, r.age_years * 365.25, bool(r.old),
                          due=n * days // len(c), pending_until=int(rng.integers(3, 25)) if pending else -1,
                          p_absence=fr["absence"][i], p_unready=fr["unready"][i], p_court=fr["court"][i],
                          p_unclear=fr["unclear"][i]))
    rng = np.random.default_rng(seed + 1)   # outcome draws shared across arms
    rows, schedule, next_gaps = [], [], []
    minutes_ref = ref.minutes.to_dict()

    def p_sub_plan(k: Case, d):
        """What the planner believes: with text signals it sees each case's own risks, without
        it only the type average."""
        pa, pu = k.p_absence, k.p_unready
        if "text_signals" not in levers:
            pa = pu = None
        f = ref.loc[k.purpose]
        base_other = 1 - f.p_sub / max(0.05, 1 - (1 - f.p_sub) * f.share_process)
        if pa is None:
            other = base_other
        else:
            other = min(0.95, pa + pu + k.p_court + k.p_unclear)
        if "fixed_slot_cluster" in levers:
            other -= (pa if pa is not None else base_other * f.share_absence) * CFG["levers"]["fixed_slot_cluster"]["removed"]
        if "intent_check" in levers:
            other -= (pu if pu is not None else base_other * f.share_unready) * CFG["levers"]["intent_check"]["removed"]
        pend = 0.0 if "process_tracking" in levers else (1 - f.p_sub) * f.share_process
        return max(0.02, (1 - pend) * (1 - max(0.0, other)))

    for d in range(days):
        live = [k for k in cases if not k.disposed and k.due <= d]
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
            listed, waitlist = _pack(live, d, p_sub_plan, cap_block, block_of, minutes_ref)
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
                reached += 1
                if k.first_listed < 0:
                    k.first_listed = d
                ptype = k.purpose
                standby_called += standby
                outcome = _outcome(k, d, levers, rng, standby)
                start_min = t0 + used[bname] - change
                if outcome == "substantive":
                    m = minutes_ref[k.purpose] * rng.lognormal(0, DAY["duration_sigma"])
                    used[bname] += m
                    mins_heard += m
                    heard += 1
                    k.heard += 1
                    if k.first_heard < 0:
                        k.first_heard = d
                    nxt = _advance(k.purpose, k.stage, CFG["stage_flow"])
                    if nxt is None:
                        k.disposed = True
                    else:
                        if k.purpose in CFG["stage_flow"]:
                            k.stage = k.purpose
                        k.purpose = nxt
                        if rng.random() < (1 - ref.loc[nxt].p_sub) * ref.loc[nxt].share_process:
                            k.pending_until = d + int(rng.integers(3, 25))  # the next step needs process again
                        _reschedule(k, d, "substantive", levers, rng, next_gaps, ref=ref)
                else:
                    used[bname] += DAY["adjourned_minutes"]
                    mins_heard += DAY["adjourned_minutes"]
                    _reschedule(k, d, outcome, levers, rng, next_gaps)
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
                     "type_switches": switches})
    return _metrics(pd.DataFrame(rows), cases, c, next_gaps, capacity, days), pd.DataFrame(schedule)


def _outcome(k: Case, d, levers, rng, standby):
    if k.pending_until > d:
        return "process"
    fx = CFG["levers"]
    pa = k.p_absence * (1 - fx["fixed_slot_cluster"]["removed"] if "fixed_slot_cluster" in levers else 1)
    if standby:
        pa = min(0.95, pa * 1.3)  # called at short notice from the waitlist
    pu = k.p_unready * (1 - fx["intent_check"]["removed"] if "intent_check" in levers else 1)
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


def _pack(live, d, p_sub_plan, cap_block, block_of, minutes_ref):
    """CP-SAT knapsack per block: urgent first, the locked ageing quota, then the rest by
    priority x P(substantive) per minute^0.25. The next ready cases form a same-day waitlist."""
    from ortools.sat.python import cp_model
    pr = CFG["priority"]
    rows = []
    for k in live:
        p = p_sub_plan(k, d)
        exp = p * minutes_ref[k.purpose] * np.exp(DAY["duration_sigma"] ** 2 / 2) + (1 - p) * DAY["adjourned_minutes"] \
            + DAY["changeover_same"]
        value = (pr.get(k.purpose, 30) + k.age_days / 30 * 0.5) * p
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


def scale_roster(data, n=3000, seed=42):
    """Their own generator (scripts/generate_roster.py): bootstrap rows, fresh IDs."""
    import importlib.util
    here = [data["dir"].parent / "scripts" / "generate_roster.py", ROOT / "scripts" / "pucar_generate_roster.py"]
    path = next(p for p in here if p.exists())
    spec = importlib.util.spec_from_file_location("gen", path)
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    out = gen.generate(n, seed, str(data["dir"] / "roster_sample_100.csv"))
    out["filing_date"] = out.filing_date.dt.strftime("%Y-%m-%d")
    return {**data, "roster": out}
