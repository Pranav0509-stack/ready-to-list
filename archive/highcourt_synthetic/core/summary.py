"""Summary cover sheet for old cases.

Demo version assembles the sheet from the record. The production hook is an LLM
(self-hosted on court servers) over the case documents, returning the same shape
with page citations; its output is cached so a network failure can't break a hearing.
"""
from core.data import df

CACHED_LLM = {
    "D0007": {
        "agreed_facts": [
            "Suit property is 14 cents in Sy. No. 212/3, Kakkanad village (plaint p.3; written statement p.2).",
            "Sale deed of 2009 in favour of the appellant is not disputed (Ext. A1, p.41).",
        ],
        "open_issues": [
            "Whether the 2011 partition deed binds the appellant (trial court judgment para 14, p.88).",
            "Whether the first appellate court erred in rejecting Ext. B4 (appeal memo ground C, p.7).",
        ],
    }
}


def summarise(conn, case_id) -> dict:
    c = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
    parties = df(conn, """SELECT p.side, COALESCE(a.name, 'Party in person') counsel FROM case_parties p
                          LEFT JOIN advocates a ON a.id=p.advocate_id WHERE p.case_id=?""", (case_id,))
    events = df(conn, "SELECT date, purpose, outcome, reason_code FROM hearings WHERE case_id=? ORDER BY date",
                (case_id,))
    pre = df(conn, "SELECT item, done, due_date FROM prerequisites WHERE case_id=? AND purpose=?",
             (case_id, c["next_purpose"]))
    cached = CACHED_LLM.get(case_id, {})
    return {
        "title": c["title"], "filed": c["filing_date"], "stage": c["next_purpose"],
        "verified": bool(c["summary_verified"]), "parties": parties, "events": events,
        "prerequisites": pre,
        "agreed_facts": cached.get("agreed_facts", ["Generated from the paper book by the summary model (cached)."]),
        "open_issues": cached.get("open_issues", [f"Pending: {c['next_purpose']} stage."]),
        "adjournments": int((events.outcome != "effective").sum()) if not events.empty else 0,
    }
