"""Every combination: pre-filing on/off x method x objective profile, on the same court.

Writes data/benchmark.csv, which the Optimisation lab page reads.
Run (app stopped is not required; it only reads cases and writes the plans table):
    .venv/bin/python -m scripts.benchmark [--horizon 5] [--time 15]
"""
import argparse
import itertools

import pandas as pd

from core.config import OPTIMIZER, ROOT
from core.data import DEMO_DAY, connect
from core.optimize import build_instance, evaluate, solve
from core.predict import Predictor

METHODS = ["greedy", "milp", "cpsat", "cpsat_saa"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--time", type=float, default=15)
    a = ap.parse_args()
    conn = connect()
    pred = Predictor(conn)
    rows = []
    for pf in (True, False):
        inst = build_instance(conn, pred, DEMO_DAY, horizon=a.horizon, prefiling=pf)
        for prof, meth in itertools.product(OPTIMIZER["profiles"], METHODS):
            plan, info = solve(inst, meth, weights=OPTIMIZER["profiles"][prof], time_limit=a.time)
            ev = evaluate(inst, plan)
            rows.append({"prefiling": pf, "profile": prof, "method": meth, **info,
                         **{k: (float(v) if hasattr(v, "__float__") else v) for k, v in ev.items()}})
            print(pf, prof, meth, info["status"], round(ev.get("effective", 0), 1), info["seconds"], flush=True)
    out = pd.DataFrame(rows)
    out["horizon"] = a.horizon
    out["time_limit"] = a.time
    out.to_csv(ROOT / "data" / "benchmark.csv", index=False)
    print("wrote data/benchmark.csv")


if __name__ == "__main__":
    main()
