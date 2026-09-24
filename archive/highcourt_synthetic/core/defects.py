"""Pre-filing defect check. Never reject, just re-rank.

All rules, penalties, fees and limitation periods come from config/defect_rules.yaml.
This module is a deterministic rules engine over the PDF text (PyMuPDF); no model calls.
"""
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pymupdf
import yaml

from core import audit
from core.config import HEARING_TYPES, JUDGES, LOCKED, PREREQ_DAYS, ROOT, URGENT_PURPOSES, capacity
from core.data import working_days
from core.readiness import URGENT_PRIORITY, case_frame

RULES = yaml.safe_load((ROOT / "config" / "defect_rules.yaml").read_text())
CLASSES = RULES["classes"]
CASE_TYPES = RULES["case_types"]
MINUTES_FACTOR = 0.7  # expected minutes = reference duration x 0.7


# ---------------------------------------------------------------- reading the PDF

def _open(pdf):
    if isinstance(pdf, (bytes, bytearray)):
        return pymupdf.open(stream=bytes(pdf), filetype="pdf")
    return pymupdf.open(str(pdf))


def _pages(pdf) -> list[str]:
    with _open(pdf) as doc:
        return [p.get_text("text") for p in doc]


def _parse_date(s: str) -> date | None:
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def detect_case_type(first_page: str) -> str | None:
    """Earliest match on the cause-title page wins; ties go to the longer match (OP(Arb) over OP)."""
    best = None
    for ctype, spec in CASE_TYPES.items():
        m = re.search(spec["title_pattern"], first_page)
        if m:
            key = (m.start(), -(m.end() - m.start()))
            if best is None or key < best[0]:
                best = (key, ctype)
    return best[1] if best else None


def extract_fields(pages: list[str]) -> dict:
    out = {}
    for name, spec in RULES["extract"].items():
        hay = pages[:1] if spec.get("page") == "first" else pages
        val = None
        for txt in hay:
            m = re.search(spec["pattern"], txt, flags=re.IGNORECASE if name != "party_in_person" else 0)
            if m:
                val = m.group(1).strip()
                break
        out[name] = val
    if out["court_fee_paid"] is not None:
        out["court_fee_paid"] = int(out["court_fee_paid"].replace(",", ""))
    out["party_in_person"] = bool(out["party_in_person"])
    if out["cause_of_action_date"]:
        out["cause_of_action_date"] = _parse_date(out["cause_of_action_date"])
    out["case_type"] = detect_case_type(pages[0]) if pages else None
    return out


def _section_pages(pages, marker) -> list[int]:
    pat = re.compile(RULES["section_heading"].format(marker=re.escape(marker)), re.MULTILINE)
    return [i for i, t in enumerate(pages) if pat.search(t)]


# ---------------------------------------------------------------- checks
# Each check returns a list of (page, detail) for defects found. page is 1-based, None for the whole filing.

def _chk_required_section(rule, pages, ctx):
    return [] if _section_pages(pages, rule["marker"]) else [(None, f"No {rule['marker'].title()} section found.")]


def _chk_signature(rule, pages, ctx):
    sig = re.compile(RULES["signature_pattern"])
    hits = _section_pages(pages, rule["section"])
    if not hits:
        return []  # missing section is its own rule
    # A section can run over several pages: the signature may be on the page or the one after it
    for i in hits:
        if sig.search(pages[i]) or (i + 1 < len(pages) and not _is_new_section(pages[i + 1]) and sig.search(pages[i + 1])):
            return []
    return [(hits[0] + 1, f"No signature found on the {rule['section'].lower()}.")]


def _is_new_section(text):
    return bool(re.search(r"^\s*[A-Z]{5,}(\s+[A-Z]?\d+)?\s*$", text, re.MULTILINE))


def _chk_court_fee(rule, pages, ctx):
    need, paid = CASE_TYPES.get(ctx["case_type"], {}).get("court_fee"), ctx["extracted"]["court_fee_paid"]
    if need is None or paid is None or paid >= need:
        return []
    page = next((i + 1 for i, t in enumerate(pages) if re.search(RULES["extract"]["court_fee_paid"]["pattern"], t, re.I)), 1)
    return [(page, f"Paid Rs. {paid:,}; Rs. {need:,} is due for {ctx['case_type']}. Short by Rs. {need - paid:,}.")]


def _chk_court_fee_missing(rule, pages, ctx):
    return [(1, "No court fee line found.")] if ctx["extracted"]["court_fee_paid"] is None else []


def _chk_limitation(rule, pages, ctx):
    days = CASE_TYPES.get(ctx["case_type"], {}).get("limitation_days")
    coa = ctx["extracted"]["cause_of_action_date"]
    if not days or not coa:
        return []
    if rule.get("excused_by") and any(re.search(rule["excused_by"], t, re.I) for t in pages):
        return []
    elapsed = (ctx["filed_on"] - coa).days
    if elapsed <= days:
        return []
    page = next((i + 1 for i, t in enumerate(pages) if coa.strftime("%d.%m.%Y") in t), 1)
    return [(page, f"Order dated {coa:%d.%m.%Y}; filed {elapsed} days later. The limit for {ctx['case_type']} is "
                   f"{days} days, so it is {elapsed - days} days late.")]


def _chk_annexure_legibility(rule, pages, ctx):
    head = re.compile(RULES["annexure_heading"], re.MULTILINE)
    pn = re.compile(RULES["page_number_pattern"], re.MULTILINE)
    out, in_annex = [], False
    for i, t in enumerate(pages):
        in_annex = in_annex or bool(head.search(t))
        if not in_annex:
            continue
        body = pn.sub("", t).strip()
        if RULES["illegible_marker"] in t or len(re.sub(r"\s+", "", body)) < RULES["annexure_min_chars"]:
            name = head.search(t)
            label = name.group(0).strip().title() if name else "An annexure page"
            out.append((i + 1, f"{label} on page {i + 1} has almost no readable text."))
    return out


def _page_numbers(pages):
    pn = re.compile(RULES["page_number_pattern"], re.MULTILINE)
    nums = []
    for t in pages:
        m = pn.findall(t.strip())
        nums.append(int(m[-1]) if m else None)
    return nums


def _chk_page_numbers_duplicate(rule, pages, ctx):
    seen, out = {}, []
    for i, n in enumerate(_page_numbers(pages)):
        if n is None:
            continue
        if n in seen:
            out.append((i + 1, f"Page {i + 1} is numbered {n}, the same as page {seen[n] + 1}."))
        else:
            seen[n] = i
    return out


def _chk_page_numbers_missing(rule, pages, ctx):
    return [(i + 1, f"Page {i + 1} has no page number.") for i, n in enumerate(_page_numbers(pages)) if n is None]


def _chk_parties(rule, pages, ctx):
    ex = ctx["extracted"]
    missing = [k for k in ("petitioner", "respondent") if not ex.get(k)]
    return [(1, f"Could not find the {' or '.join(missing)} on the cause-title page.")] if missing else []


CHECKS = {k[5:]: v for k, v in globals().items() if k.startswith("_chk_")}


def semantic_check(text: str) -> list[dict]:
    """HOOK: semantic defects the rules engine cannot see (prayer inconsistent with grounds, wrong
    respondent impleaded, relief outside jurisdiction).

    Production plugs a self-hosted LLM here (no filing text leaves the court's servers). It must return
    defects in the same shape as check_filing and must never block a filing. The demo returns [].
    """
    return []


def score(defects, party_in_person=False, drop_classes=()) -> float:
    scored = RULES["scored_classes_party_in_person"] if party_in_person else RULES["scored_classes"]
    s = 100 + sum(CLASSES[d["class"]]["penalty"] for d in defects
                  if d["class"] in scored and d["class"] not in drop_classes)
    return float(max(RULES["min_score"], s))


def check_filing(pdf_path_or_bytes, case_type=None, filed_on: date | None = None, party_in_person=False) -> dict:
    pages = _pages(pdf_path_or_bytes)
    extracted = extract_fields(pages)
    case_type = case_type or extracted["case_type"]
    pip = bool(party_in_person or extracted["party_in_person"])
    ctx = {"case_type": case_type, "filed_on": filed_on or date.today(), "extracted": extracted, "pip": pip}
    defects = []
    for rule in RULES["rules"]:
        if pip and rule.get("skip_party_in_person"):
            continue
        if case_type in rule.get("skip_case_types", []):
            continue
        for page, detail in CHECKS[rule["check"]](rule, pages, ctx):
            defects.append({"id": rule["id"], "class": rule["class"], "page": page, "title": rule["title"],
                            "message": detail, "fix": rule["fix"], "explanation": rule["explanation"]})
    defects += semantic_check("\n".join(pages))
    order = list(CLASSES)
    defects.sort(key=lambda d: (order.index(d["class"]), d["page"] or 0))
    return {"defects": defects, "filing_score": score(defects, pip),
            "score_if_fixed": score(defects, pip, RULES["fix_first_classes"]),
            "pages": len(pages), "case_type": case_type, "party_in_person": pip, "extracted": extracted}


# ---------------------------------------------------------------- routing and queue

def route_judge(case_type: str, selected: str) -> str:
    """Specialist benches (e.g. Justice Dimakar, arbitration only) take their case types."""
    for jid, j in JUDGES.items():
        if case_type in j.get("case_types", []):
            return jid
    if JUDGES[selected].get("case_types") and case_type not in JUDGES[selected]["case_types"]:
        return next(j for j, c in JUDGES.items() if not c.get("case_types"))
    return selected


def _new_priority(purpose, filing_score, urgent):
    """Same formula as core.readiness for a case filed today: no prerequisites done, not confirmed,
    summary not needed (under four years), no age boost."""
    items = HEARING_TYPES[purpose]["prereqs"]
    prereq_frac = 0.0 if items else 1.0
    readiness = round(30 * filing_score / 100 + 40 * prereq_frac + 0 + 10)
    base = HEARING_TYPES[purpose]["priority"] + readiness * 0.5
    return (URGENT_PRIORITY + base if urgent else base), readiness


def _listing(frame, judge_id, purpose, prio, on):
    ahead = frame[frame.priority >= prio]
    minutes_ahead = (ahead.next_purpose.map(lambda p: HEARING_TYPES[p]["duration"]) * MINUTES_FACTOR).sum()
    own = HEARING_TYPES[purpose]["duration"] * MINUTES_FACTOR
    cap = capacity(judge_id)
    day_index = int((minutes_ahead + own) // cap)
    first = on + timedelta(days=1)  # never listed the day it is filed
    return len(ahead) + 1, working_days(first, day_index + 1)[-1]


def queue_position(conn, judge_id, case_type, purpose, filing_score, urgent, on: date,
                   score_if_fixed=None, exclude=None) -> dict:
    """Where a new case would sit in this judge's queue, as filed and with critical defects fixed.
    Rank is among pending cases by core.readiness priority; the date walks working days at the
    judge's daily capacity. Urgent matters are never pushed down by defects."""
    frame = case_frame(conn, judge_id, on=on)
    if exclude:
        frame = frame[frame.id != exclude]
    urgent = bool(urgent or purpose in URGENT_PURPOSES)
    fixed_score = max(filing_score, score_if_fixed if score_if_fixed is not None else 100.0)
    now_score = fixed_score if urgent else filing_score  # guardrail
    out = {"pool_size": len(frame), "urgent": urgent}
    for key, s in (("as_filed", now_score), ("if_fixed", fixed_score)):
        prio, ready = _new_priority(purpose, s, urgent)
        rank, when = _listing(frame, judge_id, purpose, prio, on)
        out[key] = {"rank": rank, "date": when, "priority": round(prio, 1), "readiness": ready,
                    "filing_score": s, "gated": (not urgent) and ready < LOCKED["readiness_gate"]}
    return out


# ---------------------------------------------------------------- submission

def _taxonomy_for(case_type, purpose):
    """Map a filing's case-type code to the taxonomy type and its most common sub-type."""
    from core import taxonomy as T
    for tk, t in T.TYPES.items():
        if case_type in t["code"].replace(" / ", "/").split("/") or case_type == t["code"]:
            if tk == "a_bail" and "AB" in case_type:
                continue
            sk = max(t["subtypes"], key=lambda k: t["subtypes"][k]["share"])
            return tk, sk
    return "e_writ_civil", "licensing_local"


def _next_case_id(conn):
    row = conn.execute("SELECT MAX(CAST(SUBSTR(id, 2) AS INT)) FROM cases WHERE id LIKE 'F%'").fetchone()
    return f"F{(row[0] or 0) + 1:04d}"


def _advocate(conn, name):
    if not name:
        return None
    clean = re.sub(r"^(Adv\.?|Sri\.?|Smt\.?)\s*", "", name.strip(), flags=re.I)
    row = conn.execute("SELECT id FROM advocates WHERE name=?", (clean,)).fetchone()
    if row:
        return row[0]
    n = conn.execute("SELECT MAX(CAST(SUBSTR(id, 2) AS INT)) FROM advocates WHERE id LIKE 'A%'").fetchone()[0] or 0
    aid = f"A{n + 1:03d}"
    conn.execute("INSERT INTO advocates VALUES (?,?,?,?,?)", (aid, clean, "diligent", 0.9, 0))
    return aid


def _defect_rows(case_id, version, defects):
    return [(case_id, version, d["class"], d["page"], f"{d['title']}: {d['message']}", 0) for d in defects]


def submit_filing(conn, pdf_meta: dict, result: dict, judge_id, case_type, purpose, submitted_anyway, on: date) -> str:
    """Register the filing as a new pending case. Never refuses: defects only change its rank.
    pdf_meta: {"name": file name} plus anything else worth logging."""
    ex = result["extracted"]
    urgent = int(purpose in URGENT_PURPOSES)
    q = queue_position(conn, judge_id, case_type, purpose, result["filing_score"], urgent, on,
                       result.get("score_if_fixed"))
    when = q["as_filed"]["date"]
    cid = _next_case_id(conn)
    pet, res = ex.get("petitioner") or "Petitioner", ex.get("respondent") or "Respondent"
    title = f"{case_type} (filing {cid}), {pet} v. {res}"
    category, subtype = _taxonomy_for(case_type, purpose)
    conn.execute("INSERT INTO cases (id, title, filing_date, case_type, stage, next_purpose, urgency_flag, judge_id, "
                 "next_date, confirmed, summary_verified, status, category, subtype, pages, parties) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (cid, title, on.isoformat(), case_type, purpose, purpose, urgent, judge_id, when.isoformat(),
                  0, 0, "pending", category, subtype, int(result.get("pages") or 100), 2))
    pip = int(result.get("party_in_person", False))
    adv = None if pip else _advocate(conn, ex.get("counsel"))
    conn.execute("INSERT INTO case_parties VALUES (?,?,?,?)", (cid, adv, "petitioner", pip))
    conn.execute("INSERT INTO case_parties VALUES (?,?,?,?)", (cid, None, "respondent", 0))
    conn.execute("INSERT INTO filings VALUES (?,?,?,?)", (cid, 1, result["filing_score"], int(bool(submitted_anyway))))
    conn.executemany("INSERT INTO defects VALUES (?,?,?,?,?,?)", _defect_rows(cid, 1, result["defects"]))
    for item in HEARING_TYPES[purpose]["prereqs"]:
        due = when - timedelta(days=PREREQ_DAYS[item] // 2)
        conn.execute("INSERT INTO prerequisites VALUES (?,?,?,?,?)", (cid, purpose, item, 0, due.isoformat()))
    n = len(result["defects"])
    audit.log(conn, "filing", cid, f"Filed v1, score {result['filing_score']:.0f}, queue #{q['as_filed']['rank']}, "
              f"listing {when.isoformat()}",
              f"{n} defect(s) from {pdf_meta.get('name', 'upload')}; "
              + ("submitted anyway, re-ranked not rejected" if submitted_anyway and n else "no blocking"))
    conn.commit()
    return cid


def refile(conn, case_id, new_result: dict, on: date | None = None, pdf_meta: dict | None = None) -> dict:
    """Add version+1, mark defects that are gone as fixed, rescore and move the listing date."""
    case = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
    on = on or date.fromisoformat(case["filing_date"])
    v = conn.execute("SELECT MAX(version) FROM filings WHERE case_id=?", (case_id,)).fetchone()[0] or 0
    new_titles = {d["title"] for d in new_result["defects"]}
    old = conn.execute("SELECT rowid, message FROM defects WHERE case_id=? AND version=? AND fixed=0",
                       (case_id, v)).fetchall()
    fixed = [r["rowid"] for r in old if r["message"].split(":")[0] not in new_titles]
    conn.executemany("UPDATE defects SET fixed=1 WHERE rowid=?", [(r,) for r in fixed])
    conn.execute("INSERT INTO filings VALUES (?,?,?,?)", (case_id, v + 1, new_result["filing_score"], 0))
    conn.executemany("INSERT INTO defects VALUES (?,?,?,?,?,?)", _defect_rows(case_id, v + 1, new_result["defects"]))
    q = queue_position(conn, case["judge_id"], case["case_type"], case["next_purpose"], new_result["filing_score"],
                       case["urgency_flag"], on, new_result.get("score_if_fixed"), exclude=case_id)
    when = q["as_filed"]["date"]
    conn.execute("UPDATE cases SET next_date=? WHERE id=?", (when.isoformat(), case_id))
    for item in HEARING_TYPES[case["next_purpose"]]["prereqs"]:
        conn.execute("UPDATE prerequisites SET due_date=? WHERE case_id=? AND item=? AND done=0",
                     ((when - timedelta(days=PREREQ_DAYS[item] // 2)).isoformat(), case_id, item))
    audit.log(conn, "filing", case_id, f"Refiled v{v + 1}, score {new_result['filing_score']:.0f}, "
              f"queue #{q['as_filed']['rank']}, listing {when.isoformat()}",
              f"{len(fixed)} defect(s) fixed, {len(new_result['defects'])} open"
              + (f" ({pdf_meta['name']})" if pdf_meta else ""))
    conn.commit()
    return {"version": v + 1, "fixed": len(fixed), "filing_score": new_result["filing_score"],
            "rank": q["as_filed"]["rank"], "date": when}
