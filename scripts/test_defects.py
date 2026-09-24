"""End-to-end check of the pre-filing defect flow on a scratch DB (never touches data/court.db).
Run: .venv/bin/python scripts/test_defects.py [scratch_dir]"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import data  # noqa: E402
from core.data import DEMO_DAY  # noqa: E402
from core.defects import CASE_TYPES, check_filing, queue_position, refile, route_judge, submit_filing  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
EXPECT = {"01_wpc_clean.pdf": set(), "02_wpc_defective.pdf": {"court_fee_short", "unsigned_vakalatnama",
          "annexure_illegible", "duplicate_page_number"}, "03_wpc_fixed.pdf": set(),
          "04_arba_late.pdf": {"limitation"}, "05_bail_minor.pdf": {"missing_page_number"},
          "06_op_party_in_person.pdf": {"missing_affidavit"}}

results = {}
print(f"{'file':28} {'type':11} {'score':>5} {'fixed':>5}  defects")
for f in sorted(SAMPLES.glob("*.pdf")):
    r = check_filing(f, None, DEMO_DAY)
    results[f.name] = r
    ids = {d["id"] for d in r["defects"]}
    print(f"{f.name:28} {r['case_type']:11} {r['filing_score']:5.0f} {r['score_if_fixed']:5.0f}  "
          + ", ".join(f"{d['class']}:{d['id']}@p{d['page']}" for d in r["defects"]))
    assert ids == EXPECT[f.name], (f.name, ids)
    assert r["extracted"]["petitioner"] and r["extracted"]["respondent"], r["extracted"]
assert results["06_op_party_in_person.pdf"]["party_in_person"]

tmp = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp()) / "filing_test.db"
data.build_db(path=tmp, force=True)
conn = data.connect(tmp)
on = DEMO_DAY

for name in ["02_wpc_defective.pdf", "03_wpc_fixed.pdf", "04_arba_late.pdf", "05_bail_minor.pdf"]:
    r = results[name]
    ct = r["case_type"]
    purpose = CASE_TYPES[ct]["default_purpose"]
    jid = route_judge(ct, "J1")
    q = queue_position(conn, jid, ct, purpose, r["filing_score"], False, on, r["score_if_fixed"])
    print(f"{name:28} {jid} pool {q['pool_size']}: now #{q['as_filed']['rank']} {q['as_filed']['date']} "
          f"| fix first #{q['if_fixed']['rank']} {q['if_fixed']['date']}")
    if purpose == "bail":
        assert q["as_filed"] == q["if_fixed"], "urgent must not be pushed down"
    else:
        assert q["as_filed"]["rank"] >= q["if_fixed"]["rank"]

bad = results["02_wpc_defective.pdf"]
cid = submit_filing(conn, {"name": "02_wpc_defective.pdf"}, bad, "J1", "WP(C)", "admission", True, on)
cid2 = submit_filing(conn, {"name": "05_bail_minor.pdf"}, results["05_bail_minor.pdf"], "J1", "Bail Appl.", "bail", True, on)
assert (cid, cid2) == ("F0001", "F0002"), (cid, cid2)
row = conn.execute("SELECT * FROM cases WHERE id=?", (cid,)).fetchone()
print(dict(row))
print("defects v1:", [tuple(r) for r in conn.execute("SELECT class, page, message, fixed FROM defects WHERE case_id=?", (cid,))])
out = refile(conn, cid, results["03_wpc_fixed.pdf"], on)
print("refile:", out)
assert out["version"] == 2 and out["fixed"] == 4 and out["filing_score"] == 100
assert out["date"] <= data.date.fromisoformat(row["next_date"])
print("audit:", [tuple(r) for r in conn.execute("SELECT service, case_id, decision FROM audit_log WHERE service='filing'")])
from core.readiness import case_frame  # noqa: E402
fr = case_frame(conn, "J1", on=on).set_index("id")
print("F0001 in case_frame:", fr.loc[cid, ["filing_score", "readiness", "priority"]].to_dict())
print("OK")
