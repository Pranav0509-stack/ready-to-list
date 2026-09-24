"""SQLite store and adapter layer.

Their synthetic CSVs load through load_csvs() only. If their columns differ from
this model, edit COLUMN_MAP here and nothing else changes. Until then, build_db()
generates a synthetic court with the same shape.
"""
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from core import agents
from core.config import HEARING_TYPES, JUDGES, PREREQ_DAYS, ROOT, STAGE_FLOW, URGENT_PURPOSES

DB_PATH = ROOT / "data" / "court.db"
RAW_DIR = ROOT / "data" / "raw"
DEMO_DAY = date(2026, 10, 12)

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
  id TEXT PRIMARY KEY, title TEXT, filing_date TEXT, case_type TEXT, stage TEXT,
  next_purpose TEXT, urgency_flag INT, judge_id TEXT, next_date TEXT,
  confirmed INT DEFAULT 0, summary_verified INT DEFAULT 0, status TEXT DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS advocates (
  id TEXT PRIMARY KEY, name TEXT, agent_type TEXT, show_rate REAL, warned INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS case_parties (
  case_id TEXT, advocate_id TEXT, side TEXT, party_in_person INT);
CREATE TABLE IF NOT EXISTS filings (
  case_id TEXT, version INT, filing_score REAL, submitted_anyway INT);
CREATE TABLE IF NOT EXISTS defects (
  case_id TEXT, version INT, class TEXT, page INT, message TEXT, fixed INT);
CREATE TABLE IF NOT EXISTS prerequisites (
  case_id TEXT, purpose TEXT, item TEXT, done INT, due_date TEXT);
CREATE TABLE IF NOT EXISTS hearings (
  case_id TEXT, judge_id TEXT, date TEXT, purpose TEXT, slot TEXT, outcome TEXT, reason_code TEXT,
  minutes_used REAL, confirmed INT, bundled INT, clashes INT, fixed_slot INT, prereq_frac REAL,
  show_rate REAL, res_show_rate REAL, old_unsum INT, showed INT, effective INT);
CREATE TABLE IF NOT EXISTS causelists (
  judge_id TEXT, date TEXT, seq INT, case_id TEXT, block TEXT, slot_start TEXT, slot_end TEXT,
  exp_start TEXT, expected_minutes REAL, p_show REAL, p_effective REAL, reason TEXT,
  status TEXT, outcome TEXT);
CREATE TABLE IF NOT EXISTS calendar (date TEXT, judge_id TEXT, holiday INT, judge_leave INT);
CREATE TABLE IF NOT EXISTS notifications (
  case_id TEXT, actor TEXT, channel TEXT, message TEXT, sent_at TEXT, response TEXT);
CREATE TABLE IF NOT EXISTS audit_log (
  ts TEXT, service TEXT, case_id TEXT, decision TEXT, reason TEXT, overridden_by TEXT);
"""

HOLIDAYS = {date(2026, 10, 2), date(2026, 10, 20), date(2026, 11, 9), date(2026, 11, 24),
            date(2026, 12, 25)}

FIRST = ["Rao", "Menon", "Nair", "Pillai", "Iyer", "Kurian", "Thomas", "Varghese", "Das", "Sharma",
         "Joseph", "Mathew", "George", "Krishnan", "Warrier", "Kamath", "Shenoy", "Bhat", "Reddy",
         "Gupta", "Verma", "Khan", "Ali", "Fernandes", "D'Souza", "Kapoor", "Mehta", "Chacko"]
INITIALS = "ABCDEGHJKLMNPRSTV"
CASE_TYPES = {"bail": "Bail Appl.", "habeas": "WP(Crl.)", "stay": "WP(C)"}
CIVIL_TYPES = ["WP(C)", "OP", "RSA", "MACA", "CRL.MC"]
ARB_TYPES = ["Arb.A", "OP(Arb)"]  # Justice Dimakar hears only arbitration
PARTIES = ["State of Kerala", "KSEB", "Union of India", "KSRTC", "Cochin Corporation",
           "District Collector", "Federal Bank", "LIC of India", "M/s Malabar Traders"]


def connect(path=DB_PATH):
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def is_working_day(d: date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS


def working_days(start: date, n: int, step=1):
    out, d = [], start
    while len(out) < n:
        if is_working_day(d):
            out.append(d)
        d += timedelta(days=step)
    return out


def build_db(seed=7, cases_per_judge=900, n_advocates=150, path=DB_PATH, force=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        return
    if path.exists():
        path.unlink()
    conn = connect(path)
    conn.executescript(SCHEMA)
    if RAW_DIR.exists() and any(RAW_DIR.glob("*.csv")):
        load_csvs(conn, RAW_DIR)
    else:
        _synthesise(conn, np.random.default_rng(seed), cases_per_judge, n_advocates)
    conn.commit()
    conn.close()


# Adapter: their column name -> our column name, per table. Fill in on the day.
COLUMN_MAP = {
    "cases": {},
    "advocates": {},
    "case_parties": {},
    "hearings": {},
    "hearing_types": {},
}


def load_csvs(conn, folder: Path):
    for table, mapping in COLUMN_MAP.items():
        f = folder / f"{table}.csv"
        if f.exists() and table != "hearing_types":
            pd.read_csv(f).rename(columns=mapping).to_sql(table, conn, if_exists="append", index=False)


def _synthesise(conn, rng, cases_per_judge, n_advocates):
    today = DEMO_DAY
    # Advocates
    advs = []
    for i in range(n_advocates):
        t = rng.choice(agents.AGENT_TYPES, p=agents.AGENT_MIX)
        name = f"{rng.choice(list(INITIALS))}. {rng.choice(FIRST)}"
        advs.append((f"A{i:03d}", name, t, agents.BASE_SHOW[t], 0))
    advs[0] = ("A000", "S. Rao", "diligent", 0.9, 0)  # demo advocate
    conn.executemany("INSERT INTO advocates VALUES (?,?,?,?,?)", advs)
    adv_type = {a[0]: a[2] for a in advs}

    # Calendar: working days and a couple of leave days per judge
    days = [today + timedelta(days=k) for k in range(-40, 180)]
    cal = []
    for jid in JUDGES:
        leave = set(rng.choice([d for d in days if d > today + timedelta(days=5)], 2, replace=False))
        for d in days:
            cal.append((d.isoformat(), jid, int(not is_working_day(d)), int(d in leave)))
    conn.executemany("INSERT INTO calendar VALUES (?,?,?,?)", cal)

    upcoming = working_days(today, 30)
    cases, parties, filings, defects, prereqs, hearings = [], [], [], [], [], []
    n = 0
    for jid in JUDGES:
        for _ in range(cases_per_judge):
            n += 1
            cid = f"C{n:05d}"
            # Manual: a quarter under a year old, 1 in 6 over four years
            age_years = rng.choice([rng.uniform(0, 1), rng.uniform(1, 3), rng.uniform(3, 4),
                                    rng.uniform(4, 5), rng.uniform(5, 11)], p=[0.25, 0.40, 0.18, 0.07, 0.10])
            filing = today - timedelta(days=int(age_years * 365))
            if rng.random() < 0.035:
                purpose = rng.choice(sorted(URGENT_PURPOSES), p=[0.6, 0.1, 0.3])
                age_years = min(age_years, 0.3)
                filing = today - timedelta(days=int(age_years * 365))
            else:
                # Older cases sit further down the stage flow
                idx = int(np.clip(rng.normal(age_years / 2, 1.2), 0, len(STAGE_FLOW) - 1))
                purpose = STAGE_FLOW[idx]
            ctype = CASE_TYPES.get(purpose, rng.choice(ARB_TYPES if JUDGES[jid].get("case_types") else CIVIL_TYPES))
            title = f"{ctype} {rng.integers(100, 9999)}/{filing.year}, {rng.choice(FIRST)} v. {rng.choice(PARTIES)}"
            # Next date: mostly in the next 30 working days, some overdue
            nd = upcoming[int(rng.integers(0, 30))] if rng.random() > 0.05 else today - timedelta(days=int(rng.integers(1, 20)))
            if nd == today or rng.random() < 0.03:
                nd = today
            urgent = int(purpose in URGENT_PURPOSES)
            if urgent:  # urgent matters come up within days, never months
                nd = upcoming[int(rng.integers(0, 4))]
            summary_verified = int(age_years < 4 or rng.random() < 0.4)
            cases.append([cid, title, filing.isoformat(), ctype, purpose, purpose, urgent, jid,
                          nd.isoformat(), 0, summary_verified, "pending"])

            pet = f"A{rng.integers(0, n_advocates):03d}"
            res = f"A{rng.integers(0, n_advocates):03d}"
            pip = int(rng.random() < 0.05)
            parties.append((cid, pet, "petitioner", 0))
            parties.append((cid, None if pip else res, "respondent", pip))

            score = float(np.clip(rng.normal(85, 12), 30, 100))
            filings.append((cid, 1, score, int(score < 70)))
            if score < 90:
                defects.append((cid, 1, "minor", int(rng.integers(2, 40)), "Double page numbering", 0))
            if score < 75:
                defects.append((cid, 1, "major", int(rng.integers(2, 40)), "Annexure illegible", 0))
            if score < 60:
                defects.append((cid, 1, "critical", 2, "Court fee short", 0))

            prereq_done = True
            for item in HEARING_TYPES[purpose]["prereqs"]:
                done = int(rng.random() < 0.62)
                prereq_done &= bool(done)
                due = nd - timedelta(days=PREREQ_DAYS[item] // 2)
                prereqs.append((cid, purpose, item, done, due.isoformat()))

            # Past hearings, simulated with the advocate model (the predictor trains on these)
            for k in range(int(rng.integers(1, 7)) if age_years > 0.3 else 0):
                hd = today - timedelta(days=int(rng.integers(20, 60)) * (k + 1))
                t = adv_type[pet]
                conf = agents.will_confirm(rng, t) and rng.random() < 0.3
                bundled = rng.random() < 0.25
                clashes = int(rng.poisson(0.5))
                pf = float(rng.random() < 0.55)
                fixed = rng.random() < 0.2  # pilot benches that already give fixed slots
                ps = agents.p_show(t, fixed, bundled, conf, clashes)
                res_rate = agents.PARTY_IN_PERSON_SHOW if pip else agents.BASE_SHOW[adv_type[res]]
                ps_res = agents.PARTY_IN_PERSON_SHOW if pip else agents.p_show(adv_type[res], fixed, bundled, False, clashes)
                showed = rng.random() < ps and rng.random() < ps_res
                old_unsum = age_years >= 4 and not summary_verified
                eff = showed and rng.random() < agents.p_effective_given_heard(pf, old_unsum, t, conf)
                if eff:
                    outcome, code = "effective", None
                elif showed:
                    outcome, code = "heard_not_effective", "not prepared"
                else:
                    outcome, code = "adjourned", rng.choice(["counsel absent", "counsel absent", "service pending", "time ran out"])
                mins = HEARING_TYPES[purpose]["duration"] * rng.lognormal(0, 0.3) * (1.3 if old_unsum else 1) if showed else 2
                hearings.append((cid, jid, hd.isoformat(), purpose, None, outcome, code, float(mins),
                                 int(conf), int(bundled), clashes, int(fixed), pf, agents.BASE_SHOW[t], res_rate,
                                 int(old_unsum), int(showed), int(eff)))

    _demo_cases(cases, parties, prereqs, filings, today)

    conn.executemany(f"INSERT INTO cases VALUES ({','.join('?' * 12)})", cases)
    conn.executemany("INSERT INTO case_parties VALUES (?,?,?,?)", parties)
    conn.executemany("INSERT INTO filings VALUES (?,?,?,?)", filings)
    conn.executemany("INSERT INTO defects VALUES (?,?,?,?,?,?)", defects)
    conn.executemany("INSERT INTO prerequisites VALUES (?,?,?,?,?)", prereqs)
    conn.executemany(f"INSERT INTO hearings VALUES ({','.join('?' * 18)})", hearings)


def _demo_cases(cases, parties, prereqs, filings, today):
    """Rao's writ petition (demo story) and a 7-year-old case for the summary drawer."""
    t = today.isoformat()
    cases += [
        ["D0001", "WP(C) 2231/2025, Rao (for Kurian) v. State of Kerala", "2025-06-30", "WP(C)",
         "admission", "admission", 0, "J1", t, 1, 1, "pending"],
        ["D0002", "WP(C) 1904/2025, Rao (for Menon) v. KSEB", "2025-04-11", "WP(C)",
         "admission", "admission", 0, "J1", t, 1, 1, "pending"],
        ["D0003", "OP 877/2025, Rao (for Pillai) v. Cochin Corporation", "2025-05-02", "OP",
         "notice", "notice", 0, "J1", t, 1, 1, "pending"],
        ["D0007", "RSA 412/2019, Varghese v. Mathew", "2019-06-14", "RSA",
         "arguments", "arguments", 0, "J1", t, 1, 1, "pending"],
    ]
    for cid in ["D0001", "D0002", "D0003", "D0007"]:
        parties.append((cid, "A000" if cid != "D0007" else "A001", "petitioner", 0))
        parties.append((cid, "A002", "respondent", 0))
        filings.append((cid, 1, 94.0, 0))
    prereqs.append(("D0003", "notice", "service", 1, today.isoformat()))
    prereqs.append(("D0007", "arguments", "written submissions", 1, today.isoformat()))


def df(conn, sql, params=()):
    return pd.read_sql_query(sql, conn, params=params)
