"""One judge for a year: the organisers' 100 real cases inside a 3,000-case docket (their
generator), new complaints arriving daily, four approaches, averaged over seeds.

    .venv/bin/python -m scripts.run_one_judge [--days 250] [--seeds 3] [--out outputs]
"""
import argparse
from datetime import date
from pathlib import Path

import pandas as pd

from core import pucar_engine as E

ARMS = {"Today's rules": dict(rtl=False),
        "Scheduling only": dict(rtl=True, levers=["optimiser", "smart_next_date", "fixed_slot_cluster"]),
        "Scheduling + pre-filing": dict(rtl=True, levers=["prefiling", "optimiser", "smart_next_date", "fixed_slot_cluster"]),
        "Samay (all levers)": dict(rtl=True)}
EARLY = ["ADMISSION", "DELAY_CONDONATION_HEARING", "COGNIZANCE"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=250)
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--out", default="outputs")
    a = ap.parse_args()
    data = E.judge_docket(E.load(), 3000)
    real = set(data["roster"][data["roster"]["sample"]].case_number)
    rows, journeys = [], []
    for name, kw in ARMS.items():
        runs = []
        for s in range(a.seeds):
            m, _ = E.simulate(data, date(2026, 10, 1), days=a.days, seed=7 + 100 * s, **kw)
            cs, j = m["cases"], m["journey"]
            nw = cs[cs.new_filing]
            runs.append({"disposed": cs.disposed.sum(), "disposed_of_real_100": cs[cs.case_number.isin(real)].disposed.sum(),
                         "substantive_per_day": m["substantive_per_day"], "substantiveness_pct": m["substantiveness_pct"],
                         "reach_rate_pct": m["reach_rate_pct"], "backlog_4y_heard_pct": m["backlog_4y_heard_pct"],
                         "new_complaints": len(nw), "new_past_cognizance_pct": 100 * (~nw.purpose_now.isin(EARLY)).mean(),
                         "delay_condonation_listings": (j.hearing_type == "DELAY_CONDONATION_HEARING").sum(),
                         "wasted_listings": m["wasted_listings"], "pending_at_end": (~cs.disposed).sum()})
            if s == 0:
                journeys.append(j[j.case_number.isin(real)].assign(approach=name))
        rows.append({"approach": name, **pd.DataFrame(runs).mean().round(1).to_dict()})
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    res = pd.DataFrame(rows)
    res.to_csv(out / "one_judge_year.csv", index=False)
    pd.concat(journeys).to_csv(out / "one_judge_real100_journeys.csv", index=False)
    md = res.to_markdown(index=False)
    (out / "one_judge_year.md").write_text(md + "\n")
    print(md)


if __name__ == "__main__":
    main()
