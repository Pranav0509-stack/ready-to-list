"""Headless check of pages/calendar.py.

1. plan_horizon(greedy, 5 days) on a scratch DB (never rebuilds data/court.db).
2. AppTest of the page against data/court.db (via resources()): the stored-plan fallback, then an
   in-session greedy plan across every tab, advocate highlight and filter, then the Plan button.
   Greedy planning on the real DB appends one row set to its `plans` table, nothing else.
Run from the repo root: .venv/bin/python scripts/test_calendar_page.py [scratch_dir]"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
from streamlit.testing.v1 import AppTest  # noqa: E402

from core import data, optimize, readiness  # noqa: E402
from core.predict import Predictor  # noqa: E402

# 1. scratch DB
tmp = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp()) / "calendar_page.db"
data.build_db(path=tmp, force=True)
sc = data.connect(tmp)
readiness.run_nudges(sc, data.DEMO_DAY)
r = optimize.plan_horizon(sc, Predictor(sc), data.DEMO_DAY, method="greedy", horizon=5)
assert len(r["plan"]) > 0 and "start_min" in r["seq"], "scratch plan empty"
print("scratch plan:", len(r["plan"]), "cases,", r["info"])

# 2. real DB, as the app sees it
assert data.DB_PATH.exists(), "data/court.db missing: start the app once first"
conn = data.connect()
real = optimize.plan_horizon(conn, Predictor(conn), data.DEMO_DAY, method="greedy", horizon=5)
print("real plan:", len(real["plan"]), "cases")

PAGE = str(ROOT / "pages" / "calendar.py")

# a) no session plan: falls back to the latest stored plan
at = AppTest.from_file(PAGE, default_timeout=180).run()
assert not at.exception, at.exception
print("fallback tabs:", [t.label for t in at.tabs], "| captions:", len(at.caption))
assert "latest stored plan" in " ".join(c.value for c in at.caption)

# b) in-session plan: full UI
at = AppTest.from_file(PAGE, default_timeout=180)
at.session_state["cal_plan"] = real
at.run()
assert not at.exception, at.exception
assert [t.label for t in at.tabs] == ["Year", "Fortnight", "Day view", "Priority order", "Plan quality"]
print("solver caption:", next(c.value for c in at.caption if "status" in c.value))
print("metrics:", [(m.label, m.value) for m in at.metric][:6], "... total", len(at.metric))
assert any(m.label == "Predictability" for m in at.metric)

# drill into each horizon day
day_box = at.selectbox(key="cal_day")
for d in day_box.options[:3]:
    at.selectbox(key="cal_day").set_value(next(o for o in real["inst"].days if f"{o:%A %d %B}" in d)).run()
    assert not at.exception, at.exception
pick = at.selectbox(key="cal_day").value
adv_box = at.selectbox(key=f"cal_adv_{pick.isoformat()}")
print("advocate options (top 3):", adv_box.options[1:4])
# pick the first real advocate (index 1; index 0 is Everyone)
at.selectbox(key=f"cal_adv_{pick.isoformat()}").select_index(1).run()
assert not at.exception, at.exception
print("itinerary shown:", any("Itinerary" in m.value for m in at.markdown))
assert any("Itinerary" in m.value for m in at.markdown)

# priority filter
at.text_input[0].input("Rao").run()
assert not at.exception, at.exception
print("filter caption:", next(c.value for c in at.caption if "planned cases" in c.value))

# c) the Plan button (greedy, 5 days)
next(s for s in at.selectbox if s.label == "Working days").set_value(5)
next(s for s in at.selectbox if s.label == "Method").set_value("greedy")
next(b for b in at.button if b.label == "Plan these days").click().run()
assert not at.exception, at.exception
assert at.session_state["cal_plan"]["info"]["method"] == "greedy"
print("button plan:", len(at.session_state["cal_plan"]["plan"]), "cases")
print("OK")
