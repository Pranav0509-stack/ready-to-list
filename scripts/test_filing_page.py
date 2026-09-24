"""Headless AppTest of pages/filing.py against a scratch DB (never writes data/court.db).
Run from the repo root: .venv/bin/python scripts/test_filing_page.py [scratch_dir]"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from streamlit.testing.v1 import AppTest  # noqa: E402

from core import bootstrap, data  # noqa: E402

tmp = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp()) / "filing_page.db"
data.build_db(path=tmp, force=True)
bootstrap.build_db = lambda force=False: None
bootstrap.DB_PATH = tmp
bootstrap.connect = lambda: data.connect(tmp)

at = AppTest.from_file(str(ROOT / "pages" / "filing.py"), default_timeout=120).run()
assert not at.exception, at.exception
print("metric:", [(m.label, m.value) for m in at.metric])
print("markdown sample:", [m.value for m in at.markdown if "Queue #" in m.value])
submit = next(b for b in at.button if b.label in ("Submit anyway", "Submit filing"))
assert not submit.disabled
submit.click().run()
assert not at.exception, at.exception
print("success:", [s.value for s in at.success])
next(b for b in at.button if b.label == "Re-upload fixed version").click().run()
assert not at.exception, at.exception
print("after refile:", [m.value for m in at.markdown if "version" in m.value])
print("metric:", [(m.label, m.value) for m in at.metric])
# Guided mode sample
at.selectbox(key="sample_pick").set_value("06_op_party_in_person.pdf").run()
assert not at.exception, at.exception
print("pip metric:", [(m.label, m.value) for m in at.metric], "toggle:", [t.value for t in at.toggle])
at.selectbox(key="sample_pick").set_value("05_bail_minor.pdf").run()
assert not at.exception, at.exception
print("bail:", [m.value for m in at.markdown if "Queue #" in m.value or "urgent" in m.value])
print("OK")
