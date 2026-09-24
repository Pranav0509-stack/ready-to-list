"""Score Ready-to-List on the organisers' data and write the submission outputs.

    .venv/bin/python -m scripts.run_pucar [--cases 3000] [--days 60] [--capacity 330] [--out outputs]

Writes:
    outputs/results.csv            baseline, Ready-to-List, and each lever switched off (ablation)
    outputs/results.md             the same as a Markdown table, for SUBMISSION.md
    outputs/proposed_schedule.csv  the first 10 working days of the Ready-to-List cause list
    outputs/daily.csv              day-by-day series for both arms
"""
import argparse
from datetime import date
from pathlib import Path

import pandas as pd

from core import pucar_engine as E

LABELS = {"utilisation_pct": "Utilisation %", "reach_rate_pct": "Reach rate %", "substantiveness_pct":
          "Substantiveness %", "backlog_4y_heard_pct": "4+ year cases heard %", "predictability_days":
          "Predictability, days listed to heard", "next_date_gap_days": "Next-date gap, days",
          "substantive_per_day": "Substantive hearings a day", "listed_per_day": "Listed a day",
          "wasted_listings": "Wasted listings", "disposed": "Disposed", "type_switches_per_day": "Type switches a day"}


def run(cases=3000, days=60, capacity=None, start=date(2026, 10, 1), seed=7, data_dir=None, seeds=5):
    data = E.load(data_dir)
    if cases and cases != len(data["roster"]):
        data = E.scale_roster(data, cases)
    arms = {"Today's rules": dict(rtl=False), "Samay": dict(rtl=True)}
    for lever in E.LEVERS:
        arms[f"Without {lever.replace('_', ' ')}"] = dict(rtl=True, levers=[x for x in E.LEVERS if x != lever])
    arms["Readiness levers only (no optimiser, no smart date)"] = dict(
        rtl=True, levers=["process_tracking", "intent_check", "fixed_slot_cluster", "text_signals"])
    arms["Scheduling only (no pre-filing, no party input)"] = dict(rtl=True, levers=["optimiser", "smart_next_date"])
    arms["Scheduling only + fixed slots and clustering"] = dict(
        rtl=True, levers=["optimiser", "smart_next_date", "fixed_slot_cluster"])
    rows, daily, schedule = [], [], None
    for name, kw in arms.items():
        runs = []
        for s in range(seeds):  # average over seeds: lever effects are small next to day-to-day noise
            m, sched = E.simulate(data, start, days=days, capacity=capacity, seed=seed + 100 * s, **kw)
            runs.append({k: v for k, v in m.items() if isinstance(v, (int, float))})  # numbers only
            if s == 0:
                daily.append(m["daily"].assign(arm=name))
                if name == "Samay":
                    schedule = sched
        rows.append({"arm": name, **pd.DataFrame(runs).mean().to_dict()})
    return pd.DataFrame(rows), pd.concat(daily), schedule


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=3000)
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--capacity", type=int, default=None, help="court minutes a day (default 330; README uses 420)")
    ap.add_argument("--data", default=None)
    ap.add_argument("--out", default="outputs")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    res, daily, sched = run(a.cases, a.days, a.capacity, data_dir=a.data)
    res.to_csv(out / "results.csv", index=False)
    daily.to_csv(out / "daily.csv", index=False)
    sched.to_csv(out / "proposed_schedule.csv", index=False)
    cols = ["arm"] + list(LABELS)
    md = res[cols].rename(columns=LABELS).round(1).to_markdown(index=False)
    (out / "results.md").write_text(md + "\n")
    print(md)


if __name__ == "__main__":
    main()
