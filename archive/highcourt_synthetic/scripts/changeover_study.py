"""How much judge time does grouping similar cases save? Two weeks of cause lists, three courts:
changeover minutes when the list is called in priority order vs ordered by CP-SAT with
sequence-dependent changeovers (config/case_taxonomy.yaml court_day).

Run: .venv/bin/python -m scripts.changeover_study
"""
import pandas as pd

from core import taxonomy as T
from core.config import JUDGES
from core.data import DEMO_DAY, connect, working_days
from core.predict import Predictor
from core.scheduler import build_causelist


def main(days=10):
    conn = connect()
    pred = Predictor(conn)
    rows = []
    for d in working_days(DEMO_DAY, days):
        for jid in JUDGES:
            items = build_causelist(conn, pred, jid, d)["items"]
            if items.empty:
                continue
            it = items.assign(type=items.category.fillna(items.next_purpose), minutes=items.expected_minutes,
                              weight=(items.priority.clip(upper=1100) / 10).round())
            for block, g in it.groupby("block"):
                nv = T.naive_changeover(g)
                _, cp = T.sequence_with_changeovers(g, time_limit=5)
                rows.append({"date": d, "judge": jid, "block": block, "cases": len(g), "types": g.type.nunique(),
                             "naive_switches": nv["switches"], "grouped_switches": cp["switches"],
                             "naive_min": nv["changeover"], "grouped_min": cp["changeover"]})
    r = pd.DataFrame(rows)
    per_court_day = r.groupby(["date", "judge"])[["cases", "naive_min", "grouped_min"]].sum()
    per_court_day["saved"] = per_court_day.naive_min - per_court_day.grouped_min
    print(r.groupby("judge")[["cases", "types", "naive_switches", "grouped_switches"]].mean().round(1))
    print("per court-day:", per_court_day.mean().round(1).to_dict())
    return r


if __name__ == "__main__":
    main()
