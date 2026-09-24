"""Stage 2 evidence: sequence one court day with CP-SAT (intervals, NoOverlap) and with a
big-M MILP (pairwise order binaries), same objective, same time limit.

Run: .venv/bin/python -m scripts.sequencing_bench [--cases 40] [--time 30]
"""
import argparse
import itertools
import time

from ortools.linear_solver import pywraplp
from ortools.sat.python import cp_model

from core.config import JUDGES, OPTIMIZER, to_min
from core.data import DEMO_DAY, connect
from core.optimize import build_instance, solve
from core.predict import Predictor


def objective(rows, starts):
    """Priority-weighted start times (urgent and important matters early)."""
    return sum(w * s for w, s in zip(rows.weight, starts))


def cp(rows, travel, limit):
    m = cp_model.CpModel()
    st, ivs, adv = [], {}, {}
    for p, r in rows.iterrows():
        s = m.NewIntVar(r.b0, r.b1 + 120, f"s{p}")
        iv = m.NewIntervalVar(s, r.dur, s + r.dur, f"i{p}")
        ivs.setdefault(r.judge_id, []).append(iv)
        adv.setdefault(r.pet_adv, []).append((s, r.dur, r.judge_id))
        st.append(s)
    for v in ivs.values():
        m.AddNoOverlap(v)
    for items in adv.values():
        if len({j for _, _, j in items}) > 1:
            m.AddNoOverlap([m.NewFixedSizeIntervalVar(s, d + travel, "") for s, d, _ in items])
    m.Minimize(sum(int(w) * s for w, s in zip(rows.weight, st)))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = limit
    solver.parameters.num_workers = 8
    t = time.perf_counter()
    status = solver.Solve(m)
    return solver.StatusName(status), solver.ObjectiveValue(), solver.BestObjectiveBound(), time.perf_counter() - t


def milp(rows, travel, limit):
    s = pywraplp.Solver.CreateSolver("SCIP")
    s.SetTimeLimit(int(limit * 1000))
    big = 24 * 60
    st = [s.NumVar(r.b0, r.b1 + 120, f"s{p}") for p, r in rows.iterrows()]
    pairs = set()
    for j, g in rows.groupby("judge_id"):
        pairs |= {(a, b, 0) for a, b in itertools.combinations(g.index, 2)}
    for a_, g in rows.groupby("pet_adv"):
        if g.judge_id.nunique() > 1:
            pairs |= {(a, b, travel) for a, b in itertools.combinations(g.index, 2)
                      if rows.at[a, "judge_id"] != rows.at[b, "judge_id"]}
    for a, b, tr in pairs:  # either a before b or b before a
        y = s.BoolVar("")
        s.Add(st[a] + rows.at[a, "dur"] + tr <= st[b] + big * (1 - y))
        s.Add(st[b] + rows.at[b, "dur"] + tr <= st[a] + big * y)
    s.Minimize(sum(float(w) * v for w, v in zip(rows.weight, st)))
    t = time.perf_counter()
    status = s.Solve()
    ok = status in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE)
    name = {pywraplp.Solver.OPTIMAL: "OPTIMAL", pywraplp.Solver.FEASIBLE: "FEASIBLE"}.get(status, "NO SOLUTION")
    return name, s.Objective().Value() if ok else None, s.Objective().BestBound(), time.perf_counter() - t, len(pairs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--time", type=float, default=30)
    a = ap.parse_args()
    conn = connect()
    inst = build_instance(conn, Predictor(conn), DEMO_DAY, horizon=3)
    plan, _ = solve(inst, "greedy")
    day0 = plan[plan.day_idx == 0].reset_index(drop=True)
    travel = OPTIMIZER["sequencing"]["travel_minutes"]
    print("cases_per_day | method | status | objective | bound | seconds | order_pairs")
    for n in (15, 30, 60, len(day0)):
        rows = day0.sort_values("priority", ascending=False).head(n).reset_index(drop=True)
        rows["dur"] = rows.expected_minutes.round().clip(lower=1).astype(int)
        rows["weight"] = (rows.priority.clip(upper=1100) / 10).round().astype(int)
        blk = {(j, b["name"]): (to_min(b["start"]), to_min(b["end"])) for j in JUDGES for b in JUDGES[j]["blocks"]}
        rows["b0"] = [blk[(j, b)][0] for j, b in zip(rows.judge_id, rows.block)]
        rows["b1"] = [blk[(j, b)][1] for j, b in zip(rows.judge_id, rows.block)]
        c = cp(rows, travel, a.time)
        m = milp(rows, travel, a.time)
        print(f"{n} | CP-SAT | {c[0]} | {c[1]:.0f} | {c[2]:.0f} | {c[3]:.1f} | -")
        print(f"{n} | MILP big-M (SCIP) | {m[0]} | {m[1] if m[1] is None else round(m[1])} | {m[2]:.0f} | "
              f"{m[3]:.1f} | {m[4]}", flush=True)


if __name__ == "__main__":
    main()
