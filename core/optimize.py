"""Multi-judge, multi-day cause-list planner, solved four ways so they can be compared.

Two stages, because the two halves of the problem suit different solvers:

  Stage 1, which day (assignment):  x[i,d] = 1 if case i is listed on horizon day d.
      A knapsack/assignment structure with a linear objective. MILP (HiGHS/SCIP) and
      CP-SAT both handle it; LP bounds make MILP strong here.
  Stage 2, what time (sequencing):  start time of each listed case in its courtroom.
      Disjunctive no-overlap per courtroom AND per advocate across courtrooms (with
      travel time). That is constraint programming's home ground (interval variables,
      NoOverlap), where a MILP needs big-M pairs and a weak relaxation.

Methods for stage 1:
  greedy     priority-ratio fill, day by day (what the single-day scheduler does)
  milp       the model below as a mixed-integer linear program (HiGHS, SCIP or CBC)
  cpsat      the same model in CP-SAT (integer-scaled)
  cpsat_saa  stochastic version: sample show-up and duration scenarios, allow planned
             overbooking, and pay for expected overtime instead of a hard 95% cap
             (sample average approximation). Best when show-ups are uncertain.

Every weight and parameter comes from config/optimizer.yaml and config/model.yaml.
"""
import time
import zlib
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from core import agents
from core.config import FILL_TARGET, HEARING_TYPES, JUDGES, LOCKED, MODEL, OPTIMIZER, PREREQ_DAYS, to_hhmm, to_min
from core.data import df, working_days
from core.readiness import case_frame
from core.scheduler import block_for

try:
    from ortools.linear_solver import pywraplp
    from ortools.sat.python import cp_model
except ImportError:  # greedy still works
    pywraplp = cp_model = None

DEF = MODEL["defects"]
FX = MODEL["show_effects"]
ADJ = MODEL["durations"]["adjourn_minutes"]


def _u(case_id, salt):
    """Deterministic uniform draw per case, so every method faces the same hidden truth."""
    return (zlib.crc32(f"{case_id}:{salt}".encode()) % 10_000) / 10_000


@dataclass
class Instance:
    cases: pd.DataFrame          # one row per candidate case
    days: list                   # horizon working days (dates)
    blocks: dict                 # (judge, day_idx, block) -> capacity minutes
    prefiling: bool
    judges: list
    feasible: dict = field(default_factory=dict)   # case id -> list of day indices


def build_instance(conn, predictor, start: date, horizon=None, judges=None, prefiling=True, max_per_judge=None):
    horizon = horizon or OPTIMIZER["horizon_working_days"]
    judges = judges or list(JUDGES)
    days = working_days(start, horizon)
    cal = df(conn, "SELECT date, judge_id, holiday, judge_leave FROM calendar")
    off = {(r.judge_id, r.date) for r in cal.itertuples() if r.holiday or r.judge_leave}
    pre = df(conn, """SELECT p.case_id, MAX(p.due_date) due FROM prerequisites p JOIN cases c
                      ON c.id=p.case_id AND c.next_purpose=p.purpose WHERE p.done=0 GROUP BY p.case_id""")
    pre_due = dict(zip(pre.case_id, pre.due))

    frames = []
    for jid in judges:
        cf = case_frame(conn, jid, on=start)
        cfg = JUDGES[jid]
        cf["judge_id"] = jid
        cf["block"] = cf.next_purpose.map(lambda p: block_for(p, cfg["blocks"]))
        cf["clashes"] = 0
        cf["bundled"] = 0
        cf["fixed_slot"] = int(cfg.get("clustering", True))
        cf["show_rate"] = cf.pet_show
        cf["res_show_rate"] = np.where(cf.pip == 1, agents.PARTY_IN_PERSON_SHOW,
                                       cf.res_type.map(agents.BASE_SHOW).fillna(agents.PARTY_IN_PERSON_SHOW))
        cf["old_unsum"] = ((cf.age_years >= 4) & ~cf.summary_ok).astype(int)
        # Keep the pool to what the horizon can plausibly hold: the court is the bottleneck
        cap_cases = max_per_judge or int(len(days) * 60 * 1.5)
        cf = cf.sort_values("priority", ascending=False).head(cap_cases)
        frames.append(cf)
    c = pd.concat(frames, ignore_index=True)
    c = c.join(predictor.predict(c).drop(columns=[], errors="ignore"))

    # Hidden truth about critical defects, and what the pre-filing check does about it
    u = np.array([_u(i, "defect") for i in c.id])
    caught = np.array([_u(i, "caught") for i in c.id]) < DEF["caught_by_prefiling"]
    c["defect_true"] = u < DEF["share_with_critical"]
    c["defect_known"] = c.defect_true & caught if prefiling else False
    c["defect_hidden"] = c.defect_true & ~c.defect_known
    fail = DEF["critical_fail_rate"]
    if prefiling:
        # Known defects get fixed before listing (a few days later); unknown ones stay hidden
        c["p_eff_plan"] = c.p_effective
    else:
        # Nobody checked: the planner can only discount every case by the average risk
        c["p_eff_plan"] = c.p_effective * (1 - DEF["share_with_critical"] * fail)
    c["p_eff_true"] = np.where(c.defect_hidden, c.p_effective * (1 - fail), c.p_effective)

    # Earliest day: not before its prerequisites are due to be done, or before a known defect is fixed
    day_iso = [d.isoformat() for d in days]
    fix_days = DEF.get("fix_working_days", 3)
    earliest, due_idx, feasible = [], [], {}
    for r in c.itertuples():
        e = 0
        if r.id in pre_due and pre_due[r.id] is not None:
            e = next((k for k, d in enumerate(day_iso) if d > pre_due[r.id]), len(days))
        if r.defect_known:
            e = max(e, fix_days)
        if r.urgent:
            e = 0  # urgent bypass (locked): liberty matters never wait for paperwork
        earliest.append(e)
        due_idx.append(next((k for k, d in enumerate(day_iso) if d >= r.next_date), len(days)))
        feasible[r.id] = [k for k in range(e, len(days)) if (r.judge_id, day_iso[k]) not in off]
    c["earliest"] = earliest
    c["due_idx"] = due_idx
    c["due_in_horizon"] = c.next_date <= day_iso[-1]
    c["prio_n"] = c.priority / 100.0
    c["deadline"] = np.where(c.urgent, 1, len(days) - 1)

    blocks = {}
    for jid in judges:
        for k, d in enumerate(day_iso):
            if (jid, d) in off:
                continue
            for b in JUDGES[jid]["blocks"]:
                blocks[(jid, k, b["name"])] = to_min(b["end"]) - to_min(b["start"])
    return Instance(c, days, blocks, prefiling, judges, feasible)


# ---------------------------------------------------------------------- objective pieces

def _coef(inst, w):
    """Per (case, day) objective coefficient for everything that is linear in x."""
    c = inst.cases
    out = {}
    for r in c.itertuples():
        base = (w["throughput"] * r.prio_n * r.p_eff_plan
                - w["wasted"] * (1 - r.p_show)
                + w["unlisted"] * r.prio_n * (1.0 + 0.5 * r.due_in_horizon)
                + w["idle"] * r.expected_minutes)
        for k in inst.feasible[r.id]:
            out[(r.id, k)] = base - w["wait"] * r.prio_n * k
    return out


def _groups(inst):
    """Advocates with 2+ candidate cases: trips and clashes are only decisions for them."""
    c = inst.cases
    g = c.groupby("pet_adv")
    return {a: grp for a, grp in g if len(grp) >= 2 and isinstance(a, str)}


# ---------------------------------------------------------------------- stage 1 solvers

def solve(inst, method=None, weights=None, time_limit=None, seed=0):
    method = method or OPTIMIZER["method"]
    w = {**OPTIMIZER["weights"], **(weights or {})}
    time_limit = time_limit or OPTIMIZER["time_limit_seconds"]
    t0 = time.perf_counter()
    if method == "greedy" or (method != "greedy" and pywraplp is None):
        assign, info = _greedy(inst, w), {"status": "heuristic", "bound": None}
    elif method == "milp":
        assign, info = _milp(inst, w, time_limit)
    elif method == "cpsat":
        assign, info = _cpsat(inst, w, time_limit, saa=False, seed=seed)
    elif method == "cpsat_saa":
        assign, info = _cpsat(inst, w, time_limit, saa=True, seed=seed)
    else:
        raise ValueError(method)
    info["seconds"] = round(time.perf_counter() - t0, 2)
    info["method"] = method
    plan = _plan_frame(inst, assign)
    info["objective"] = round(objective_value(inst, plan, w), 1)
    return plan, info


def _plan_frame(inst, assign):
    c = inst.cases.set_index("id")
    rows = [{"id": i, "day_idx": k, "date": inst.days[k], **c.loc[i].to_dict()} for i, k in assign.items()]
    return pd.DataFrame(rows)


def objective_value(inst, plan, w):
    """Evaluate any plan on the deterministic objective, so methods are scored alike."""
    if plan.empty:
        return 0.0
    coef = _coef(inst, w)
    v = sum(coef[(r.id, r.day_idx)] for r in plan.itertuples())
    att = plan.groupby(["pet_adv", "day_idx"]).judge_id.nunique()
    v -= w["trips"] * len(att) + w["clash"] * (att - 1).clip(lower=0).sum()
    load = plan.groupby(["judge_id", "day_idx"]).expected_minutes.sum()
    caps = pd.Series({(j, k): sum(m for (jj, kk, _), m in inst.blocks.items() if jj == j and kk == k)
                      for (j, k, _) in inst.blocks})
    util = (load.reindex(caps.index).fillna(0) / caps)
    v -= w["balance"] * (util.max() - util.min())
    return float(v)


def _cap(inst, key, saa=False):
    over = OPTIMIZER["saa"]["allow_overbooking"] if saa else 0
    jid = key[0]
    return inst.blocks[key] * (FILL_TARGET + JUDGES[jid].get("overbooking", 0) + over)


def _quota_need(inst, jid, k):
    total = sum(m for (j, kk, _), m in inst.blocks.items() if j == jid and kk == k)
    return LOCKED["ageing_quota"] * total * FILL_TARGET


def _greedy(inst, w):
    c = inst.cases.copy()
    c["ratio"] = c.prio_n * c.p_eff_plan / c.expected_minutes.clip(lower=1) ** OPTIMIZER.get("balance", 0.25)
    left = {key: _cap(inst, key) for key in inst.blocks}
    feas = {i: set(v) for i, v in inst.feasible.items()}
    assign = {}
    for k in range(len(inst.days)):
        for jid in inst.judges:
            if not any(kk == k and j == jid for (j, kk, _) in inst.blocks):
                continue
            pool = c[(c.judge_id == jid) & ~c.id.isin(assign.keys())]
            pool = pool[[k in feas[i] for i in pool.id]]
            order = pd.concat([pool[pool.urgent].sort_values("priority", ascending=False),
                               pool[pool.old & ~pool.urgent].sort_values("priority", ascending=False),
                               pool[~pool.old & ~pool.urgent].sort_values("ratio", ascending=False)])
            quota, used_q = _quota_need(inst, jid, k), 0.0
            for r in order.itertuples():
                key = (jid, k, r.block)
                if key not in left:
                    continue
                is_quota = r.old and not r.urgent and used_q < quota
                if not r.urgent and not is_quota and r.readiness < LOCKED["readiness_gate"]:
                    continue
                if r.urgent or left[key] >= r.expected_minutes:
                    assign[r.id] = k
                    left[key] -= r.expected_minutes
                    used_q += r.expected_minutes if r.old else 0
    return assign


def _scenarios(inst, n, seed):
    """Sampled realised minutes per case: heard -> lognormal duration, else an adjournment."""
    rng = np.random.default_rng(seed)
    c = inst.cases
    shows = rng.random((n, len(c))) < c.p_show.values
    dur = c.duration.values * rng.lognormal(0, MODEL["durations"]["lognormal_sigma"], (n, len(c)))
    return np.where(shows, dur, ADJ)


def _cpsat(inst, w, time_limit, saa=False, seed=0):
    S = 100  # integer scale
    m = cp_model.CpModel()
    c = inst.cases.set_index("id")
    coef = _coef(inst, w)
    x = {(i, k): m.NewBoolVar(f"x_{i}_{k}") for i, ks in inst.feasible.items() for k in ks}
    obj = [int(round(S * coef[key])) * v for key, v in x.items()]
    gate = LOCKED["readiness_gate"]

    for i, ks in inst.feasible.items():
        if not ks:
            continue
        r = c.loc[i]
        vs = [x[(i, k)] for k in ks]
        m.Add(sum(vs) <= 1)  # listed at most once in the horizon
        if r.urgent and any(k <= r.deadline for k in ks):
            m.Add(sum(x[(i, k)] for k in ks if k <= r.deadline) == 1)  # locked: urgent within deadline
        if not r.urgent and not r.old and r.readiness < gate:
            for v in vs:
                m.Add(v == 0)  # readiness gate (locked); the ageing quota may still take old cases

    by_block = {}
    for (i, k), v in x.items():
        by_block.setdefault((c.at[i, "judge_id"], k, c.at[i, "block"]), []).append((i, v))

    if saa:
        n = OPTIMIZER["saa"]["scenarios"]
        mins = _scenarios(inst, n, seed)
        idx = {i: p for p, i in enumerate(inst.cases.id)}
    for key, items in by_block.items():
        if key not in inst.blocks:
            for _, v in items:
                m.Add(v == 0)
            continue
        m.Add(sum(int(round(S * c.at[i, "expected_minutes"])) * v for i, v in items) <= int(S * _cap(inst, key, saa)))
        if saa:
            capm = inst.blocks[key]
            per_min = max(1, int(round(S * w["overtime"] / n)))  # expected overtime, on the S scale
            for s in range(n):
                ot = m.NewIntVar(0, capm * 3, f"ot_{key}_{s}")  # overtime minutes in scenario s
                m.Add(S * ot >= sum(int(round(S * mins[s, idx[i]])) * v for i, v in items) - S * capm)
                obj.append(-per_min * ot)

    # Ageing quota per court-day (locked): soft only if there are not enough old cases
    for jid in inst.judges:
        for k in range(len(inst.days)):
            olds = [x[(i, k)] for i in inst.cases[(inst.cases.judge_id == jid) & inst.cases.old].id if (i, k) in x]
            need = _quota_need(inst, jid, k)
            if not olds or not any(kk == k and j == jid for (j, kk, _) in inst.blocks):
                continue
            slack = m.NewIntVar(0, int(S * need), f"qs_{jid}_{k}")
            m.Add(sum(int(round(S * c.at[i, "expected_minutes"])) * x[(i, k)]
                      for i in inst.cases[(inst.cases.judge_id == jid) & inst.cases.old].id if (i, k) in x) + slack
                  >= int(S * need))
            obj.append(-1000 * slack)

    # Trips and clashes for advocates with several matters
    for a, grp in _groups(inst).items():
        for k in range(len(inst.days)):
            per_j = {}
            for i in grp.id:
                if (i, k) in x:
                    per_j.setdefault(c.at[i, "judge_id"], []).append(x[(i, k)])
            if not per_j:
                continue
            zs = []
            for jid, vs in per_j.items():
                z = m.NewBoolVar(f"z_{a}_{jid}_{k}")
                for v in vs:
                    m.Add(z >= v)
                zs.append(z)
            obj.append(-int(round(S * w["trips"])) * sum(zs))
            if len(zs) > 1:
                cl = m.NewIntVar(0, len(zs), f"cl_{a}_{k}")
                m.Add(cl >= sum(zs) - 1)
                obj.append(-int(round(S * w["clash"])) * cl)

    # Load balance: spread of planned utilisation across court-days (per-mille)
    umax, umin = m.NewIntVar(0, 2000, "umax"), m.NewIntVar(0, 2000, "umin")
    for jid in inst.judges:
        for k in range(len(inst.days)):
            keys = [kk for kk in inst.blocks if kk[0] == jid and kk[1] == k]
            if not keys:
                continue
            capm = sum(inst.blocks[kk] for kk in keys)
            load = sum(int(round(1000 * c.at[i, "expected_minutes"] / capm)) * v
                       for kk in keys for i, v in by_block.get(kk, []))
            m.Add(umax >= load)
            m.Add(umin <= load)
    obj.append(-int(round(S * w["balance"] / 1000)) * (umax - umin))

    m.Maximize(sum(obj))
    s = cp_model.CpSolver()
    s.parameters.max_time_in_seconds = time_limit
    s.parameters.num_workers = 8
    s.parameters.random_seed = seed
    st = s.Solve(m)
    ok = st in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    assign = {i: k for (i, k), v in x.items() if ok and s.Value(v)}
    return assign, {"status": s.StatusName(st), "bound": round(s.BestObjectiveBound() / S, 1) if ok else None,
                    "gap_pct": round(100 * abs(s.BestObjectiveBound() - s.ObjectiveValue()) /
                                     max(1, abs(s.ObjectiveValue())), 2) if ok else None}


def _milp(inst, w, time_limit):
    solver = None
    for name in ("SCIP", "CBC"):
        solver = pywraplp.Solver.CreateSolver(name)
        if solver:
            break
    solver.SetTimeLimit(int(time_limit * 1000))
    c = inst.cases.set_index("id")
    coef = _coef(inst, w)
    x = {(i, k): solver.BoolVar(f"x_{i}_{k}") for i, ks in inst.feasible.items() for k in ks}
    obj = solver.Objective()
    for key, v in x.items():
        obj.SetCoefficient(v, coef[key])
    gate = LOCKED["readiness_gate"]
    for i, ks in inst.feasible.items():
        if not ks:
            continue
        r = c.loc[i]
        ct = solver.Constraint(0, 1)  # listed at most once in the horizon
        for k in ks:
            ct.SetCoefficient(x[(i, k)], 1)
        if r.urgent and any(k <= r.deadline for k in ks):
            ct = solver.Constraint(1, 1)  # locked: urgent within deadline
            for k in ks:
                if k <= r.deadline:
                    ct.SetCoefficient(x[(i, k)], 1)
        if not r.urgent and not r.old and r.readiness < gate:
            for k in ks:
                x[(i, k)].SetUb(0)
    by_block = {}
    for (i, k), v in x.items():
        by_block.setdefault((c.at[i, "judge_id"], k, c.at[i, "block"]), []).append((i, v))
    for key, items in by_block.items():
        ct = solver.Constraint(-solver.infinity(), _cap(inst, key) if key in inst.blocks else 0)
        for i, v in items:
            ct.SetCoefficient(v, float(c.at[i, "expected_minutes"]))
    for jid in inst.judges:
        for k in range(len(inst.days)):
            ids = [i for i in inst.cases[(inst.cases.judge_id == jid) & inst.cases.old].id if (i, k) in x]
            if not ids or not any(kk == k and j == jid for (j, kk, _) in inst.blocks):
                continue
            slack = solver.NumVar(0, solver.infinity(), f"qs_{jid}_{k}")
            ct = solver.Constraint(_quota_need(inst, jid, k), solver.infinity())
            for i in ids:
                ct.SetCoefficient(x[(i, k)], float(c.at[i, "expected_minutes"]))
            ct.SetCoefficient(slack, 1)
            obj.SetCoefficient(slack, -1000)
    for a, grp in _groups(inst).items():
        for k in range(len(inst.days)):
            per_j = {}
            for i in grp.id:
                if (i, k) in x:
                    per_j.setdefault(c.at[i, "judge_id"], []).append(x[(i, k)])
            zs = []
            for jid, vs in per_j.items():
                z = solver.NumVar(0, 1, f"z_{a}_{jid}_{k}")
                for v in vs:
                    ct = solver.Constraint(0, solver.infinity())
                    ct.SetCoefficient(z, 1)
                    ct.SetCoefficient(v, -1)
                obj.SetCoefficient(z, -w["trips"])
                zs.append(z)
            if len(zs) > 1:
                cl = solver.NumVar(0, solver.infinity(), f"cl_{a}_{k}")
                ct = solver.Constraint(-1, solver.infinity())
                ct.SetCoefficient(cl, 1)
                for z in zs:
                    ct.SetCoefficient(z, -1)
                obj.SetCoefficient(cl, -w["clash"])
    umax, umin = solver.NumVar(0, 3, "umax"), solver.NumVar(0, 3, "umin")
    for jid in inst.judges:
        for k in range(len(inst.days)):
            keys = [kk for kk in inst.blocks if kk[0] == jid and kk[1] == k]
            if not keys:
                continue
            capm = sum(inst.blocks[kk] for kk in keys)
            ct1 = solver.Constraint(0, solver.infinity())   # umax - load >= 0
            ct2 = solver.Constraint(0, solver.infinity())   # load - umin >= 0
            ct1.SetCoefficient(umax, 1)
            ct2.SetCoefficient(umin, -1)
            for kk in keys:
                for i, v in by_block.get(kk, []):
                    ct1.SetCoefficient(v, -float(c.at[i, "expected_minutes"]) / capm)
                    ct2.SetCoefficient(v, float(c.at[i, "expected_minutes"]) / capm)
    obj.SetCoefficient(umax, -w["balance"])
    obj.SetCoefficient(umin, w["balance"])
    obj.SetMaximization()
    st = solver.Solve()
    ok = st in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE)
    assign = {i: k for (i, k), v in x.items() if ok and v.solution_value() > 0.5}
    names = {pywraplp.Solver.OPTIMAL: "OPTIMAL", pywraplp.Solver.FEASIBLE: "FEASIBLE"}
    bound = solver.Objective().BestBound() if ok else None
    val = solver.Objective().Value() if ok else None
    return assign, {"status": names.get(st, str(st)), "solver": solver.SolverVersion().split()[0],
                    "bound": round(bound, 1) if bound is not None else None,
                    "gap_pct": round(100 * abs(bound - val) / max(1, abs(val)), 2) if ok else None}


# ---------------------------------------------------------------------- stage 2: sequencing

def sequence_day(plan_day: pd.DataFrame, time_limit=5.0, seed=0):
    """CP-SAT with interval variables: exact start times for one date across all courtrooms.

    No two hearings overlap in a courtroom; an advocate with matters in two courtrooms is
    never due in both at once and gets travel time in between. Urgent and high-priority
    matters go early, and each advocate's matters sit close together (short wait)."""
    if plan_day.empty:
        return plan_day.assign(start_min=[], end_min=[], start=[], window=[])
    travel = OPTIMIZER["sequencing"]["travel_minutes"]
    window = OPTIMIZER["sequencing"]["window_minutes"]
    m = cp_model.CpModel()
    rows = plan_day.reset_index(drop=True)
    horizon_end = max(to_min(b["end"]) for j in rows.judge_id.unique() for b in JUDGES[j]["blocks"]) + 120
    starts, ends, intervals, adv_iv = [], [], {}, {}
    late = []
    for p, r in rows.iterrows():
        blk = next(b for b in JUDGES[r.judge_id]["blocks"] if b["name"] == r.block)
        b0, b1 = to_min(blk["start"]), to_min(blk["end"])
        dur = max(1, int(round(r.expected_minutes)))
        st = m.NewIntVar(b0, horizon_end, f"s{p}")
        en = m.NewIntVar(b0, horizon_end + dur, f"e{p}")
        iv = m.NewIntervalVar(st, dur, en, f"iv{p}")
        intervals.setdefault(r.judge_id, []).append(iv)
        over = m.NewIntVar(0, horizon_end, f"late{p}")
        m.Add(over >= en - b1)  # running past the block end
        late.append(over)
        if isinstance(r.pet_adv, str):
            adv_iv.setdefault(r.pet_adv, []).append((r.judge_id, st, dur))
        starts.append(st)
        ends.append(en)
    for ivs in intervals.values():
        m.AddNoOverlap(ivs)
    spans = []
    for a, items in adv_iv.items():
        courts = {j for j, _, _ in items}
        if len(courts) > 1:  # same advocate in two courtrooms: no overlap, plus walking time
            m.AddNoOverlap([m.NewIntervalVar(st, dur + travel, m.NewIntVar(0, horizon_end + 200, ""), "")
                            for _, st, dur in items])
        if len(items) > 1:
            lo, hi = m.NewIntVar(0, horizon_end, ""), m.NewIntVar(0, horizon_end + 200, "")
            m.AddMinEquality(lo, [st for _, st, _ in items])
            m.AddMaxEquality(hi, [st + dur for _, st, dur in items])
            spans.append(hi - lo)
    weight = (rows.priority.clip(upper=1100) / 10).round().astype(int).tolist()
    m.Minimize(sum(wt * st for wt, st in zip(weight, starts)) + 50 * sum(spans) + 500 * sum(late))
    s = cp_model.CpSolver()
    s.parameters.max_time_in_seconds = time_limit
    s.parameters.num_workers = 8
    s.parameters.random_seed = seed
    st = s.Solve(m)
    out = rows.copy()
    if st in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        out["start_min"] = [s.Value(v) for v in starts]
    else:  # fall back to back-to-back order by priority
        out["start_min"] = 0
    out["end_min"] = out.start_min + out.expected_minutes
    out["start"] = out.start_min.map(to_hhmm)
    w0 = (out.start_min // 30) * 30
    out["window"] = [f"{to_hhmm(a)}-{to_hhmm(a + window)}" for a in w0]
    out["seq_status"] = s.StatusName(st)
    return out.sort_values(["judge_id", "start_min"])


# ---------------------------------------------------------------------- evaluation

def evaluate(inst, plan, n=200, seed=1):
    """Monte Carlo: play each planned day forward many times against the hidden truth.

    People respond to the plan: an advocate with 2+ matters in one court that day shows up
    more often (bundled), one listed in two courts shows up less (clash). Hearings run in
    the planned order; when a block's minutes run out, the rest are sent home."""
    if plan.empty:
        return {}
    rng = np.random.default_rng(seed)
    p = plan.copy()
    per_court = p.groupby(["pet_adv", "day_idx", "judge_id"]).id.transform("count")
    courts = p.groupby(["pet_adv", "day_idx"]).judge_id.transform("nunique")
    p["p_show_true"] = (p.p_show + FX["bundled"] * (per_court >= 2) + FX["per_clash"] * (courts - 1)).clip(
        FX["floor"], FX["ceiling"])
    p = p.sort_values(["day_idx", "judge_id", "block", "priority"], ascending=[True, True, True, False])
    keys = list(zip(p.judge_id, p.day_idx, p.block))
    cap = np.array([inst.blocks.get(k, 0) for k in keys])
    group = pd.factorize(pd.Series(keys))[0]
    show = rng.random((n, len(p))) < p.p_show_true.values
    eff = show & (rng.random((n, len(p))) < (p.p_eff_true / p.p_show.clip(lower=0.05)).clip(0, 1).values)
    dur = np.where(show, p.duration.values * rng.lognormal(0, MODEL["durations"]["lognormal_sigma"], (n, len(p))), ADJ)
    heard_tot = eff_tot = wasted = overtime = idle = 0.0
    for g in np.unique(group):
        cols = np.where(group == g)[0]
        c_min = cap[cols[0]]
        cum = np.cumsum(dur[:, cols], axis=1)
        in_time = (cum - dur[:, cols]) < c_min  # started before the block ended
        h = show[:, cols] & in_time
        e = eff[:, cols] & in_time
        heard_tot += h.sum()
        eff_tot += e.sum()
        wasted += (~e).sum()
        used = np.minimum(cum[:, -1], c_min + 30)
        overtime += np.clip(cum[:, -1] - c_min, 0, 30).sum()
        idle += np.clip(c_min - cum[:, -1], 0, None).sum()
    court_days = len(np.unique(group[:0])) or len({(j, k) for j, k, _ in keys})
    total_cap = sum(inst.blocks.values())
    listed = len(p)
    days = len(inst.days)
    load = p.groupby(["judge_id", "day_idx"]).expected_minutes.sum()
    caps = pd.Series({(j, k): sum(m for (jj, kk, _), m in inst.blocks.items() if jj == j and kk == k)
                      for (j, k, _) in inst.blocks})
    util = (load.reindex(caps.index).fillna(0) / caps)
    old = inst.cases[inst.cases.old]
    old_listed = p[p.old]
    due = inst.cases[inst.cases.due_in_horizon]
    return {
        "listed": listed,
        "effective": eff_tot / n,
        "effective_per_court_day": eff_tot / n / max(1, len(caps)),
        "heard": heard_tot / n,
        "predictability_pct": 100 * heard_tot / n / listed,
        "substantiveness_pct": 100 * eff_tot / max(1, heard_tot),
        "wasted_listings": wasted / n,
        "utilisation_pct": 100 * (total_cap * n - idle) / (total_cap * n),
        "overtime_min_per_court_day": overtime / n / max(1, len(caps)),
        "advocate_trips": int(p.groupby(["pet_adv", "day_idx"]).ngroups),
        "clash_days": int((p.groupby(["pet_adv", "day_idx"]).judge_id.nunique() > 1).sum()),
        "load_spread_pct": 100 * float(util.max() - util.min()),
        "old_cases_listed": int(len(old_listed)),
        "old_mean_wait_days": float(old_listed.day_idx.mean()) if len(old_listed) else float("nan"),
        "due_left_unlisted": int((~due.id.isin(p.id)).sum()),
        "urgent_on_time_pct": 100 * float((p[p.urgent].day_idx <= 1).mean()) if p.urgent.any() else 100.0,
    }


def compare(conn, predictor, start, methods=("greedy", "milp", "cpsat", "cpsat_saa"), prefiling=(True, False),
            time_limit=None, horizon=None, judges=None, weights=None):
    rows, plans = [], {}
    for pf in prefiling:
        inst = build_instance(conn, predictor, start, horizon=horizon, judges=judges, prefiling=pf)
        for meth in methods:
            plan, info = solve(inst, meth, weights=weights, time_limit=time_limit)
            ev = evaluate(inst, plan)
            rows.append({"prefiling": pf, "method": meth, **info, **ev})
            plans[(pf, meth)] = (inst, plan)
    return pd.DataFrame(rows), plans


# ---------------------------------------------------------------------- explain, store, load

def explain(inst, plan):
    """One plain sentence per listed case: why this day."""
    out = []
    for r in plan.itertuples():
        why = []
        if r.urgent:
            why.append("urgent matter, must be heard within 2 working days (locked)")
        if r.old:
            why.append(f"{r.age_years:.1f} years old, counts toward the ageing quota (locked)")
        if r.earliest > 0 and not r.urgent:
            why.append(f"not before {inst.days[r.earliest]:%d %b}: " +
                       ("defect being fixed" if r.defect_known else "prerequisites still due"))
        if r.day_idx == r.earliest and not r.urgent:
            why.append("listed on the first day it is ready")
        elif not r.urgent:
            why.append(f"day {r.day_idx + 1} of the plan: earlier days were full of higher-priority matters")
        why.append(f"P(heard) {r.p_show:.0%}, P(moves forward) {r.p_eff_plan:.0%}")
        out.append("; ".join(why) + ".")
    return out


PLAN_SCHEMA = """CREATE TABLE IF NOT EXISTS plans (
  plan_id TEXT, created TEXT, method TEXT, prefiling INT, case_id TEXT, judge_id TEXT, date TEXT,
  block TEXT, start TEXT, window TEXT, expected_minutes REAL, p_show REAL, p_effective REAL, reason TEXT)"""


def save_plan(conn, inst, plan, method, sequenced=None):
    from datetime import datetime
    conn.execute(PLAN_SCHEMA)
    pid = datetime.now().strftime("%Y%m%d%H%M%S")
    seq = sequenced.set_index("id") if sequenced is not None and not sequenced.empty else None
    reasons = dict(zip(plan.id, explain(inst, plan)))
    for r in plan.itertuples():
        st = seq.at[r.id, "start"] if seq is not None and r.id in seq.index else None
        wn = seq.at[r.id, "window"] if seq is not None and r.id in seq.index else None
        conn.execute("INSERT INTO plans VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (pid, datetime.now().isoformat(timespec="seconds"), method, int(inst.prefiling), r.id,
                      r.judge_id, r.date.isoformat(), r.block, st, wn, float(r.expected_minutes),
                      float(r.p_show), float(r.p_eff_plan), reasons[r.id]))
    conn.commit()
    return pid


def plan_horizon(conn, predictor, start, method=None, prefiling=True, weights=None, time_limit=None, horizon=None):
    """Full stage 1 + stage 2: pick the day for every case, then sequence every day across courtrooms."""
    inst = build_instance(conn, predictor, start, horizon=horizon, prefiling=prefiling)
    plan, info = solve(inst, method, weights=weights, time_limit=time_limit)
    seqs = [sequence_day(g, time_limit=3) for _, g in plan.groupby("day_idx")] if not plan.empty else []
    seq = pd.concat(seqs) if seqs else plan
    pid = save_plan(conn, inst, plan, info["method"], seq)
    return {"plan_id": pid, "inst": inst, "plan": plan, "seq": seq, "info": info, "eval": evaluate(inst, plan)}


def load_plan(conn, plan_id=None):
    conn.execute(PLAN_SCHEMA)
    if plan_id is None:
        row = conn.execute("SELECT plan_id FROM plans ORDER BY created DESC LIMIT 1").fetchone()
        if not row:
            return pd.DataFrame()
        plan_id = row[0]
    return df(conn, """SELECT p.*, c.title, c.next_purpose, c.filing_date, c.urgency_flag, a.name counsel
                       FROM plans p JOIN cases c ON c.id=p.case_id
                       LEFT JOIN case_parties cp ON cp.case_id=c.id AND cp.side='petitioner'
                       LEFT JOIN advocates a ON a.id=cp.advocate_id WHERE p.plan_id=?""", (plan_id,))
