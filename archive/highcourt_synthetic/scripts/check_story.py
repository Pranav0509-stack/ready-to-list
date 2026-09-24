"""End-to-end check of the demo story. Rebuilds data/court.db, so stop the app first.

Run: .venv/bin/python -m scripts.check_story
"""
from core.bootstrap import ensure_db
from core.data import DEMO_DAY, df
from core.nextdate import confirm_next_date, next_date, plan_after, record_outcome
from core.predict import Predictor
from core.scheduler import approve, build_causelist, impact

conn = ensure_db(force=True)
p = Predictor(conn)
r = build_causelist(conn, p, "J1", DEMO_DAY)
add = r["waitlist"].id.head(5).tolist()
old_id = r["items"][r["items"].old].id.head(1).tolist()
before, after, items, removed_old = impact(r, add=add, remove=old_id)
print("impact", before, "->", after, "removed_old", removed_old)
approve(conn, "J1", DEMO_DAY, items, removed=old_id, judge_name="Justice Sehgal")
# Rao's notice matter if it made the list, else the first non-urgent case listed
pick = items[items.id == "D0003"] if (items.id == "D0003").any() else items[~items.urgent].head(1)
case = pick.iloc[0]
print(case.id, "slot", case.slot_start, case.slot_end)
record_outcome(conn, "J1", DEMO_DAY, case.id, "effective")
purpose = plan_after("effective", case.next_purpose)
rec = next_date(conn, case.id, "effective", purpose, DEMO_DAY)
print("next", purpose, rec)
confirm_next_date(conn, case.id, rec, DEMO_DAY)
cid = items.id.iloc[0]
record_outcome(conn, "J1", DEMO_DAY, cid, "adjourned", "service pending")
print("adjourned next", next_date(conn, cid, "adjourned", items.next_purpose.iloc[0], DEMO_DAY, "service pending"))
print(df(conn, "SELECT service, COUNT(*) n, COUNT(overridden_by) overrides FROM audit_log GROUP BY service"))
print(conn.execute("SELECT next_date,next_purpose FROM cases WHERE id=?", (case.id,)).fetchone()[:])
