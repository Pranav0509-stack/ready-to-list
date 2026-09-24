"""Pre-filing check for the advocate. Never reject, just re-rank."""
from pathlib import Path

import pandas as pd
import streamlit as st

from core.config import HEARING_TYPES, JUDGES, LOCKED, URGENT_PURPOSES
from core.defects import CASE_TYPES, CLASSES, RULES, check_filing, queue_position, refile, route_judge, submit_filing
from pages.common import resources, sidebar

SAMPLES = Path(__file__).resolve().parent.parent / "samples"

conn, predictor = resources()
jid, day = sidebar()
ss = st.session_state


@st.cache_data(show_spinner=False)
def run_check(data: bytes, case_type, on, pip):
    return check_filing(data, case_type, on, pip)


def sample_names():
    return sorted(p.name for p in SAMPLES.glob("*.pdf"))


def fixed_for(name):
    """The fixed sample that pairs with a defective one (02_wpc_defective -> 03_wpc_fixed)."""
    stem = name.split("_", 1)[-1].rsplit("_", 1)[0]
    return next((n for n in sample_names() if n != name and stem in n and "fixed" in n), name)


def reset_doc():
    for k in ("override", "filed", "refiled"):
        ss.pop(k, None)


def fmt_date(d):
    return f"{d:%a %d %b %Y}"


st.title("Pre-filing check")
st.caption("Check a petition before you file. Every defect shows its page and the fix. "
           "You can always file; defects only move the case down the queue.")

# ---------------------------------------------------------------- choose the document
src_col, ct_col, pur_col, pip_col = st.columns([2.2, 1, 1, 1])
with src_col:
    source = st.segmented_control("Document", ["Sample filing", "Upload a PDF"], default="Sample filing",
                                  key="source", on_change=reset_doc)
    names = sample_names()
    doc_name, doc_bytes = None, None
    if source == "Upload a PDF":
        up = st.file_uploader("Petition PDF", type=["pdf"], key="upload", on_change=reset_doc)
        if up:
            doc_name, doc_bytes = up.name, up.getvalue()
    elif names:
        default = next((i for i, n in enumerate(names) if "defective" in n), 0)
        pick = st.selectbox("Sample", names, index=default, key="sample_pick", on_change=reset_doc)
        doc_name, doc_bytes = pick, (SAMPLES / pick).read_bytes()
    else:
        st.info("No samples yet. Run scripts/make_samples.py to create them.")
    if ss.get("override"):
        doc_name, doc_bytes = ss.override

if not doc_bytes:
    st.stop()

detected = run_check(doc_bytes, None, day, False)
types = list(CASE_TYPES)
with ct_col:
    ct = st.selectbox("Case type", types, index=types.index(detected["case_type"]) if detected["case_type"] in types else 0,
                      key=f"ct_{doc_name}")
purposes = list(HEARING_TYPES)
with pur_col:
    purpose = st.selectbox("Purpose", purposes, index=purposes.index(CASE_TYPES[ct]["default_purpose"]),
                           key=f"purpose_{doc_name}_{ct}")
with pip_col:
    pip = st.toggle("Party in person", value=detected["party_in_person"], key=f"pip_{doc_name}",
                    help="Guided mode: plain-language explanations, and only critical defects affect the score.")

result = run_check(doc_bytes, ct, day, pip)
urgent = purpose in URGENT_PURPOSES
judge = route_judge(ct, jid)
jname = JUDGES[judge]["name"]
if judge != jid:
    st.caption(f":material/alt_route: {ct} goes to {jname}, Court {JUDGES[judge]['court']}, "
               "who hears this case type.")

st.caption(f"Checking {doc_name}, {result['pages']} pages, as filed on {fmt_date(day)}.")

# ---------------------------------------------------------------- score and queue card
defects = result["defects"]
scored = RULES["scored_classes_party_in_person"] if pip else RULES["scored_classes"]
counts = {c: sum(d["class"] == c for d in defects) for c in CLASSES}
q = queue_position(conn, judge, ct, purpose, result["filing_score"], urgent, day, result["score_if_fixed"])

left, right = st.columns([1, 2.2])
with left:
    st.metric("Filing score", f"{result['filing_score']:.0f} / 100",
              f"{result['filing_score'] - 100:.0f} from defects" if result["filing_score"] < 100 else None,
              delta_color="inverse" if result["filing_score"] < 100 else "off")
    st.markdown(" · ".join(f":{CLASSES[c]['color']}[{n} {CLASSES[c]['label'].lower()}]" for c, n in counts.items()))
with right:
    now, fix = q["as_filed"], q["if_fixed"]
    a, b = st.columns(2)
    with a.container(border=True):
        st.markdown("**Submit now**")
        st.markdown(f"### Queue #{now['rank']}")
        st.markdown(f"Likely listing **{fmt_date(now['date'])}**")
        st.caption(f"Readiness {now['readiness']} at score {now['filing_score']:.0f}")
    with b.container(border=True):
        st.markdown("**Fix critical defects first**")
        st.markdown(f"### Queue #{fix['rank']}")
        st.markdown(f"Likely listing **{fmt_date(fix['date'])}**")
        gain = now["rank"] - fix["rank"]
        st.caption(f"{gain} places earlier" if gain > 0 else "Same place: nothing critical to fix")
    if q["urgent"]:
        st.markdown(f":material/bolt: {purpose.title()} is urgent. Defects never push it down; it is listed "
                    f"on {fmt_date(now['date'])} either way.")
    elif now["gated"]:
        st.markdown(f":orange[:material/hourglass_empty: Readiness {now['readiness']} is under the gate of "
                    f"{LOCKED['readiness_gate']}. The case is filed but waits until defects or prerequisites are cleared.]")
    st.caption(f"Estimated against {q['pool_size']} pending cases before {jname} and the court's daily capacity.")

# ---------------------------------------------------------------- defects
st.subheader("Defects" if defects else "No defects found")
if not defects:
    st.markdown(":material/check_circle: Every rule passed. You can file now.")
for c, spec in CLASSES.items():
    items = [d for d in defects if d["class"] == c]
    if not items:
        continue
    cost = f"{spec['penalty']} each" if c in scored else "does not affect the score"
    st.markdown(f"**{spec['label']}** · {len(items)} · :gray[{cost}]")
    for d in items:
        page = f"page {d['page']}" if d["page"] else "whole filing"
        with st.container(border=True):
            st.markdown(f":{spec['color']}[:material/{spec['icon']}: **{d['title']}**] · :gray[{page}]")
            if pip:
                st.markdown(d["explanation"])
                st.markdown(f"**What to do:** {d['fix']}")
                st.caption(d["message"])
            else:
                st.markdown(d["message"])
                st.markdown(f"**Fix:** {d['fix']}")

# ---------------------------------------------------------------- actions
st.divider()
act_l, act_r = st.columns(2)


def do_reupload():
    choice = ss.get("reup_pick")
    up = ss.get("reup_file")
    new = (up.name, up.getvalue()) if up else (choice, (SAMPLES / choice).read_bytes()) if choice else None
    if not new:
        return
    ss.override = new
    if ss.get("filed"):
        r = check_filing(new[1], ss.filed["case_type"], day, ss.filed["pip"])
        ss.refiled = refile(conn, ss.filed["id"], r, day, {"name": new[0]})


with act_l:
    with st.container(border=True):
        st.markdown("**Re-upload fixed version**")
        opts = sample_names()
        st.selectbox("Fixed sample", opts, index=opts.index(fixed_for(doc_name)) if doc_name in opts else 0,
                     key="reup_pick")
        st.file_uploader("Or upload the corrected PDF", type=["pdf"], key="reup_file")
        st.button("Re-upload fixed version", on_click=do_reupload, width="stretch", icon=":material/upload_file:")
        if ss.get("refiled"):
            r = ss.refiled
            st.markdown(f":material/check_circle: {ss.filed['id']} version {r['version']}: {r['fixed']} defect(s) fixed, "
                        f"score {r['filing_score']:.0f}. Now queue #{r['rank']}, likely listing {fmt_date(r['date'])}.")

with act_r:
    with st.container(border=True):
        st.markdown("**File this petition**")
        st.caption("Filing is never blocked. Defects stay on record and can be fixed by re-uploading.")
        label = "Submit anyway" if defects else "Submit filing"
        if st.button(label, type="secondary" if defects else "primary", width="stretch", icon=":material/send:"):
            cid = submit_filing(conn, {"name": doc_name}, result, judge, ct, purpose, bool(defects), day)
            when = conn.execute("SELECT next_date FROM cases WHERE id=?", (cid,)).fetchone()[0]
            ss.filed = {"id": cid, "date": when, "judge": jname, "case_type": ct, "pip": pip}
            ss.pop("refiled", None)
        if ss.get("filed"):
            f = ss.filed
            st.success(f"Filed as {f['id']}. It enters {f['judge']}'s pool for {f['date']}.")
            st.markdown(f"Open the **Judge dashboard** or the **Calendar** and set the court date to {f['date']} to see it.")

with st.expander("What we read from the filing"):
    ex = {k: ("" if v is None else str(v)) for k, v in result["extracted"].items()}
    st.dataframe(pd.DataFrame(ex.items(), columns=["Field", "Value"]), hide_index=True, width="stretch")

st.caption("Guardrails: nothing is rejected, defects only re-rank. Bail, habeas corpus and stay are never pushed down. "
           "Only critical and major defects cost score, and a party in person is scored on critical defects only.")
