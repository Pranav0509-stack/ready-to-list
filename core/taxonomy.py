"""Case taxonomy: hearing minutes as a function of type, sub-type, stage and case features;
the cost of waiting as a function of time; the judge's day budget; sequencing with
changeover times. All numbers come from config/case_taxonomy.yaml."""
import numpy as np
import pandas as pd
import yaml

from core.config import JUDGES, ROOT, to_min

TAX = yaml.safe_load((ROOT / "config" / "case_taxonomy.yaml").read_text())
TYPES = TAX["types"]
DAY = TAX["court_day"]
STAGES = ["admission", "notice", "interim", "arguments", "final"]
# Purposes in the scheduler map onto taxonomy stages
PURPOSE_STAGE = {"bail": "admission", "habeas": "admission", "stay": "interim", "admission": "admission",
                 "notice": "notice", "reply": "interim", "evidence": "arguments", "arguments": "arguments",
                 "final": "final"}


def subtypes(type_key):
    return TYPES[type_key]["subtypes"]


def median_minutes(type_key, subtype, stage, pages=None, parties=2, cover_sheet=False, prechecked=False):
    t = TYPES[type_key]
    m = t["stage_minutes"][stage] * t["subtypes"][subtype]["multiplier"]
    if pages:
        m *= (pages / TAX["reference_pages"]) ** TAX["pages_elasticity"]
    m *= 1 + TAX["per_extra_party"] * max(0, parties - 2)
    if cover_sheet:
        m *= TAX["cover_sheet_factor"]
    if prechecked:
        m *= TAX["precheck_factor"]
    return m


def mean_minutes(type_key, subtype, stage, **kw):
    """Lognormal mean = median x exp(sigma^2 / 2)."""
    return median_minutes(type_key, subtype, stage, **kw) * np.exp(TYPES[type_key]["sigma"] ** 2 / 2)


def sample_minutes(rng, type_key, subtype, stage, **kw):
    return median_minutes(type_key, subtype, stage, **kw) * rng.lognormal(0, TYPES[type_key]["sigma"])


def waiting_cost(type_key, days_waiting):
    """Cost of one more day of waiting, after `days_waiting` days. Rises with time and jumps past the clock."""
    v = TYPES[type_key]["value"]
    base = v["weight"] * (1 + v["growth"] * np.asarray(days_waiting) / 30)
    return base * np.where(np.asarray(days_waiting) >= v["clock_days"], v["clock_multiplier"], 1.0)


def all_checks(type_key, subtype):
    t = TYPES[type_key]
    return t["judge_checks"] + t["subtypes"][subtype].get("extra_checks", [])


def catalogue() -> pd.DataFrame:
    """One row per (type, sub-type, stage) with median and 90th percentile minutes."""
    rows = []
    for tk, t in TYPES.items():
        for sk, s in t["subtypes"].items():
            for st in STAGES:
                med = median_minutes(tk, sk, st)
                rows.append({"type": tk, "type_name": t["name"], "code": t["code"], "subtype": sk,
                             "subtype_name": s["name"], "stage": st, "median_min": round(med, 1),
                             "mean_min": round(med * np.exp(t["sigma"] ** 2 / 2), 1),
                             "p90_min": round(med * np.exp(1.2816 * t["sigma"]), 1),
                             "share_in_type": s["share"], "liberty": t["liberty"]})
    return pd.DataFrame(rows)


def draw_type(rng, judge_id):
    mix = TAX["bench_mix"][judge_id]
    tk = rng.choice(list(mix), p=np.array(list(mix.values())) / sum(mix.values()))
    subs = TYPES[tk]["subtypes"]
    shares = np.array([s["share"] for s in subs.values()])
    sk = rng.choice(list(subs), p=shares / shares.sum())
    return tk, sk


# ---------------------------------------------------------------- the judge's day

def net_block_minutes(judge_id, block_name):
    """Block minutes left for hearings: the opening (pronouncements, mentions) comes out of the
    first block, short breaks are spread across blocks. Per-case changeover is charged to each case."""
    blocks = JUDGES[judge_id]["blocks"]
    b = next(x for x in blocks if x["name"] == block_name)
    m = to_min(b["end"]) - to_min(b["start"])
    if b is blocks[0]:
        m -= DAY["opening_minutes"]
    return m - DAY["misc_break_minutes"] / len(blocks)


def day_budget(judge_id, n_cases=0, n_type_switches=0):
    """Where the judge's sitting minutes go. Lunch sits between blocks, outside sitting time."""
    blocks = JUDGES[judge_id]["blocks"]
    gross = sum(to_min(b["end"]) - to_min(b["start"]) for b in blocks)
    span = to_min(blocks[-1]["end"]) - to_min(blocks[0]["start"])
    lunch = span - gross
    opening = DAY["opening_minutes"]
    misc = DAY["misc_break_minutes"]
    change = n_cases * DAY["changeover_same_type"] + n_type_switches * (DAY["changeover_switch_type"] -
                                                                         DAY["changeover_same_type"])
    buffer = DAY["overrun_buffer"] * gross
    net = gross - opening - misc - change - buffer
    return {"court_span": span, "lunch": lunch, "gross_sitting": gross, "opening": opening,
            "misc_breaks": misc, "changeovers": change, "overrun_buffer": round(buffer, 1),
            "net_hearing_minutes": round(net, 1)}


def sequence_with_changeovers(items: pd.DataFrame, time_limit=10.0):
    """Order one courtroom's list to minimise priority-weighted start times plus changeover time,
    where switching case type costs more than staying on the same type (sequence-dependent setups).
    CP-SAT with a circuit constraint over the hearings. Needs columns: id, type, minutes, weight."""
    from ortools.sat.python import cp_model
    n = len(items)
    if n <= 1:
        return items.assign(order=range(n), start=0), {"changeover": 0, "switches": 0, "status": "trivial"}
    same, switch = DAY["changeover_same_type"], DAY["changeover_switch_type"]
    types = items.type.tolist()
    dur = items.minutes.round().astype(int).clip(lower=1).tolist()
    w = items.weight.round().astype(int).tolist()
    horizon = sum(dur) + switch * n
    m = cp_model.CpModel()
    start = [m.NewIntVar(0, horizon, f"s{i}") for i in range(n)]
    arcs, lits = [], {}
    for i in range(n):
        lit = m.NewBoolVar(f"first{i}")                 # depot -> i: i goes first
        arcs.append((0, i + 1, lit))
        m.Add(start[i] == 0).OnlyEnforceIf(lit)
        arcs.append((i + 1, 0, m.NewBoolVar(f"last{i}")))
        for j in range(n):
            if i == j:
                continue
            lit = m.NewBoolVar(f"a{i}_{j}")
            setup = same if types[i] == types[j] else switch
            arcs.append((i + 1, j + 1, lit))
            lits[(i, j)] = (lit, setup)
            m.Add(start[j] >= start[i] + dur[i] + setup).OnlyEnforceIf(lit)
    m.AddCircuit(arcs)
    change = sum(setup * lit for lit, setup in lits.values())
    m.Minimize(sum(wi * s for wi, s in zip(w, start)) + 50 * change)
    s = cp_model.CpSolver()
    s.parameters.max_time_in_seconds = time_limit
    s.parameters.num_workers = 8
    st = s.Solve(m)
    out = items.copy()
    out["start"] = [s.Value(v) for v in start]
    out = out.sort_values("start")
    switches = int(sum(a != b for a, b in zip(out.type, out.type.iloc[1:])))
    return out, {"status": s.StatusName(st), "switches": switches,
                 "changeover": (n - 1) * same + switches * (switch - same)}


def naive_changeover(items: pd.DataFrame):
    """Changeover minutes if the list is simply called in priority order."""
    o = items.sort_values("weight", ascending=False)
    switches = int(sum(a != b for a, b in zip(o.type, o.type.iloc[1:])))
    n = len(o)
    return {"switches": switches,
            "changeover": (n - 1) * DAY["changeover_same_type"] +
            switches * (DAY["changeover_switch_type"] - DAY["changeover_same_type"])}
