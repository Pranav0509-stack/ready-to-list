"""Daily cause list builder.

Order of filling, each step protected from the next:
  1. Urgent liberty matters (bail, habeas corpus, stay): always listed.      [locked]
  2. Ageing quota: a fixed share of capacity to 5+ year cases, any readiness. [locked]
  3. Everything else with readiness >= gate: CP-SAT knapsack per block maximising
     priority x P(effective), filled to
     ~95% of expected minutes, greedy fallback if the solver is unavailable.
Then advocate clustering into one-hour windows, a reason per item, and a waitlist.
"""
from datetime import date

import numpy as np
import pandas as pd

from core import agents, audit
from core.config import BALANCE, FILL_TARGET, JUDGES, LOCKED, to_hhmm, to_min
from core.data import df
from core.readiness import case_frame

try:
    from ortools.sat.python import cp_model
except ImportError:  # greedy fallback keeps the demo alive
    cp_model = None


def judge_config(judge_id, overrides=None):
    cfg = {k: v for k, v in JUDGES[judge_id].items()}
    cfg.update(overrides or {})
    return cfg


def block_for(purpose, blocks):
    for b in blocks:
        if purpose in b["purposes"]:
            return b["name"]
    return blocks[-1]["name"]


def block_caps(cfg):
    fill = FILL_TARGET + cfg.get("overbooking", 0)
    return {b["name"]: (to_min(b["end"]) - to_min(b["start"])) * fill for b in cfg["blocks"]}


def _features(conn, pool, judge_id, day, cfg):
    d = day.isoformat()
    # Clashes: other courts where the same advocate has a matter that day
    other = df(conn, """SELECT p.advocate_id adv, COUNT(DISTINCT c.judge_id) n FROM cases c
                        JOIN case_parties p ON p.case_id=c.id AND p.side='petitioner'
                        WHERE c.status='pending' AND c.next_date=? AND c.judge_id<>?
                        GROUP BY p.advocate_id""", (d, judge_id)).set_index("adv")["n"]
    pool = pool.copy()
    pool["clashes"] = pool.pet_adv.map(other).fillna(0).astype(int)
    per_adv = pool.groupby("pet_adv").id.transform("count")
    pool["n_here"] = per_adv
    pool["bundled"] = ((per_adv >= 2) & cfg.get("clustering", True)).astype(int)
    pool["fixed_slot"] = int(cfg.get("clustering", True))
    pool["show_rate"] = pool.pet_show
    pool["res_show_rate"] = np.where(pool.pip == 1, agents.PARTY_IN_PERSON_SHOW,
                                     pool.res_type.map(agents.BASE_SHOW).fillna(agents.PARTY_IN_PERSON_SHOW))
    pool["old_unsum"] = ((pool.age_years >= 4) & ~pool.summary_ok).astype(int)
    return pool


def _knapsack(cands, cap, balance=BALANCE):
    if cands.empty or cap <= 0:
        return []
    cands = cands.sort_values("priority", ascending=False).head(250)
    w = (cands.expected_minutes * 10).round().astype(int).tolist()
    # Value = priority x P(effective) x minutes^(1-balance), so value per minute is
    # priority x P(effective) / minutes^balance (see `balance` in judge_rules.yaml)
    v = (cands.priority * cands.p_effective * cands.expected_minutes ** (1 - cfg_balance(cands)) * 10).round().astype(int).tolist()
    if cp_model is not None:
        m = cp_model.CpModel()
        x = [m.NewBoolVar(f"x{i}") for i in range(len(w))]
        m.Add(sum(wi * xi for wi, xi in zip(w, x)) <= int(cap * 10))
        m.Maximize(sum(vi * xi for vi, xi in zip(v, x)))
        s = cp_model.CpSolver()
        s.parameters.max_time_in_seconds = 2.0
        s.parameters.num_workers = 4
        if s.Solve(m) in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return [cid for cid, xi in zip(cands.id, x) if s.Value(xi)]
    chosen, used = [], 0.0
    for r in cands.itertuples():
        if used + r.expected_minutes <= cap:
            chosen.append(r.id)
            used += r.expected_minutes
    return chosen


def cfg_balance(cands):
    return cands.attrs.get("balance", BALANCE)


def reason_for(r, via):
    if via == "urgent":
        return f"Listed: {r.next_purpose}, liberty/urgent matter, bypasses the queue (locked rule)."
    prereq = "prerequisites complete" if r.prereq_frac >= 1 else f"pending: {r.pending_items}"
    conf = "counsel confirmed" if r.confirmed else "counsel not yet confirmed"
    base = f"Listed: {r.next_purpose} hearing, {prereq}, {conf}, {r.age_years:.1f} years old"
    if via == "quota":
        base += ". Ageing quota for 5+ year cases (locked rule)"
    if via == "manual":
        base = "Added by judge. " + base
    return base + "."


def layout(items: pd.DataFrame, cfg) -> pd.DataFrame:
    """Order within blocks, cluster by advocate, assign expected start and slot windows."""
    out = []
    for b in cfg["blocks"]:
        bi = items[items.block == b["name"]].copy()
        if bi.empty:
            continue
        start = to_min(b["start"])
        bi["grp_pri"] = bi.groupby("pet_adv").priority.transform("max")
        if cfg.get("clustering", True):
            bi = bi.sort_values(["urgent", "grp_pri", "pet_adv", "priority"], ascending=[False, False, True, False])
        else:
            bi = bi.sort_values(["urgent", "priority"], ascending=[False, False])
        bi["exp_start_min"] = start + bi.expected_minutes.cumsum() - bi.expected_minutes
        if cfg.get("clustering", True):
            g = bi.groupby("pet_adv", sort=False)
            first = g.exp_start_min.transform("min")
            last = g.exp_start_min.transform("max") + bi.expected_minutes
            s0 = (first // 30) * 30
            s1 = np.maximum(s0 + 60, np.ceil(last / 30) * 30)
            s1 = np.where(last <= to_min(b["end"]), np.minimum(s1, to_min(b["end"])), s1)
            s0 = np.minimum(s0, s1 - 60)
            s0, s1 = pd.Series(s0, index=bi.index), pd.Series(s1, index=bi.index)
            bi["slot_start"] = s0.map(to_hhmm)
            bi["slot_end"] = s1.map(to_hhmm)
        else:
            bi["slot_start"], bi["slot_end"] = b["start"], b["end"]
        bi["exp_start"] = bi.exp_start_min.map(to_hhmm)
        out.append(bi)
    res = pd.concat(out) if out else items.assign(exp_start=[], slot_start=[], slot_end=[])
    res["seq"] = range(1, len(res) + 1)
    return res


def build_causelist(conn, predictor, judge_id, day: date, overrides=None):
    cfg = judge_config(judge_id, overrides)
    cf = case_frame(conn, judge_id, on=day)
    if cfg.get("fresh_first"):
        cf["priority"] = cf.priority + (cf.age_years < 1) * 40  # Joshi's preference; the ageing quota still binds
    due = cf[cf.next_date <= day.isoformat()]
    pool = _features(conn, due, judge_id, day, cfg)
    if pool.empty:
        return {"items": pool, "waitlist": pool, "pool": pool, "meta": {}, "cfg": cfg}
    pool = pool.join(predictor.predict(pool))
    pool["block"] = pool.next_purpose.map(lambda p: block_for(p, cfg["blocks"]))

    caps = block_caps(cfg)
    total_cap = sum(caps.values())
    left = dict(caps)
    picked, via = [], {}

    # 1. urgent: always
    for r in pool[pool.urgent].sort_values("priority", ascending=False).itertuples():
        picked.append(r.id); via[r.id] = "urgent"; left[r.block] -= r.expected_minutes

    # 2. ageing quota: 5+ year cases, whatever their readiness
    quota_min = LOCKED["ageing_quota"] * total_cap
    used = 0.0
    for r in pool[pool.old & ~pool.urgent].sort_values("priority", ascending=False).itertuples():
        if used + r.expected_minutes > quota_min:
            break
        blk = r.block if left[r.block] >= r.expected_minutes else max(left, key=left.get)
        picked.append(r.id); via[r.id] = "quota"; left[blk] -= r.expected_minutes
        pool.loc[pool.id == r.id, "block"] = blk
        used += r.expected_minutes

    # 3. knapsack over ready cases, per block
    gate = LOCKED["readiness_gate"]
    rest = pool[~pool.id.isin(picked) & (pool.readiness >= gate)]
    if cfg.get("cover_sheet_required"):  # Dimakar: no arguments in a 4+ year case without a verified cover sheet
        rest = rest[~((rest.age_years >= 4) & ~rest.summary_ok & rest.next_purpose.isin(["arguments", "final"]))]
    for bname, cap in left.items():
        cands = rest[rest.block == bname]
        cands.attrs["balance"] = cfg.get("balance", BALANCE)
        for cid in _knapsack(cands, cap):
            picked.append(cid); via[cid] = "ready"

    items = pool[pool.id.isin(picked)].copy()
    items["via"] = items.id.map(via)
    items["reason"] = [reason_for(r, r.via) for r in items.itertuples()]
    items = layout(items, cfg)

    not_picked = pool[~pool.id.isin(picked)]
    waitlist = not_picked[not_picked.readiness >= gate].sort_values("priority", ascending=False).head(10)
    held = not_picked[not_picked.readiness < gate]
    true_cap = sum(to_min(b["end"]) - to_min(b["start"]) for b in cfg["blocks"])
    meta = metrics(items, true_cap)
    meta.update({"due": len(pool), "held_back": len(held), "capacity": true_cap,
                 "quota_minutes": round(quota_min)})
    return {"items": items, "waitlist": waitlist, "pool": pool, "held": held, "meta": meta, "cfg": cfg}


def metrics(items, cap):
    if items.empty:
        return {"listed": 0, "utilisation": 0, "predicted_heard": 0, "predictability": 0,
                "predicted_effective": 0, "old_listed": 0, "planned_minutes": 0}
    return {"listed": len(items),
            "planned_minutes": round(items.expected_minutes.sum()),
            "utilisation": round(100 * items.expected_minutes.sum() / cap),
            "predicted_heard": round(items.p_show.sum()),
            "predictability": round(100 * items.p_show.mean()),
            "predicted_effective": round(items.p_effective.sum()),
            "old_listed": int(items.old.sum())}


def impact(result, add=(), remove=()):
    """Change in utilisation, predictability and 5+ year cases for a proposed override.
    Utilisation is against the true block minutes, so over 100% means overbooked."""
    cfg = result["cfg"]
    true_cap = sum(to_min(b["end"]) - to_min(b["start"]) for b in cfg["blocks"])
    before = metrics(result["items"], true_cap)
    items = result["items"][~result["items"].id.isin(remove)]
    extra = result["pool"][result["pool"].id.isin(add) & ~result["pool"].id.isin(items.id)].copy()
    if not extra.empty:
        extra["via"] = "manual"
        extra["reason"] = [reason_for(r, "manual") for r in extra.itertuples()]
    new = pd.concat([items, extra]) if not extra.empty else items.copy()
    after = metrics(new, true_cap)
    removed_old = int(result["items"][result["items"].id.isin(remove)].old.sum())
    return before, after, layout(new.drop(columns=["seq"], errors="ignore"), cfg), removed_old


def approve(conn, judge_id, day: date, items, removed=(), judge_name="Judge"):
    d = day.isoformat()
    conn.execute("DELETE FROM causelists WHERE judge_id=? AND date=?", (judge_id, d))
    for r in items.itertuples():
        conn.execute("INSERT INTO causelists VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (judge_id, d, r.seq, r.id, r.block, r.slot_start, r.slot_end, r.exp_start,
                      float(r.expected_minutes), float(r.p_show), float(r.p_effective), r.reason,
                      "approved", None))
        audit.log(conn, "scheduler", r.id, f"listed {d} {r.slot_start}-{r.slot_end}", r.reason,
                  judge_name if r.via == "manual" else None)
        court = JUDGES[judge_id]["court"]
        conn.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?)",
                     (r.id, "litigant", "whatsapp",
                      f"Your case is listed on {day:%d %b}, {r.slot_start}-{r.slot_end}, Court {court}.",
                      d, None))
    for cid in removed:
        audit.log(conn, "scheduler", cid, f"removed from {d} list", "Judge override", judge_name)
    conn.commit()
