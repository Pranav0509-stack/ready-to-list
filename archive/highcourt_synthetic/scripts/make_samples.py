"""Generate dummy Kerala High Court filings into samples/ for the pre-filing check demo.

Deterministic: same spec, same PDFs. All names and parties are fictional.
Run: .venv/bin/python scripts/make_samples.py
"""
from pathlib import Path

import pymupdf

OUT = Path(__file__).resolve().parent.parent / "samples"

WP_BODY = {
    "facts": [
        "The petitioner is a retired school teacher residing at Kakkanad. She applied for a building permit "
        "for a single-storey residence on 12.01.2026 in respect of 4.2 cents of land in Sy. No. 118/3.",
        "Despite the application being complete in every respect, the second respondent has neither granted "
        "nor refused the permit within the thirty days fixed by Rule 15 of the Kerala Municipality Building "
        "Rules, 2019. The petitioner submitted a reminder on 20.03.2026 (Annexure P2), which remains unanswered.",
    ],
    "grounds": [
        "A. The inaction of the second respondent is arbitrary and violates Article 14 of the Constitution.",
        "B. Under the Rules a permit application not decided in time is deemed granted; the respondents are "
        "bound to issue the permit.",
        "C. The petitioner has no other equally efficacious remedy.",
    ],
    "prayer": [
        "i. Issue a writ of mandamus directing the second respondent to issue the building permit applied for "
        "on 12.01.2026 within two weeks.",
        "ii. Grant such other reliefs as this Court deems fit.",
    ],
}

# One entry per sample. Flags switch individual defects on.
SPECS = [
    dict(file="01_wpc_clean.pdf", number="W.P.(C) No. ______ of 2026", kind="WRIT PETITION (CIVIL)",
         article="FILED UNDER ARTICLE 226 OF THE CONSTITUTION OF INDIA",
         petitioner="Thomas Kurian, aged 52, S/o Kurian Mathew, Pulickal House, Aluva, Ernakulam District",
         respondents=["State of Kerala, represented by the Secretary, Local Self Government Department, Thiruvananthapuram",
                      "Aluva Municipality, represented by its Secretary, Aluva"],
         counsel="S. Rao", fee=250, cause_date="02.08.2026",
         body={"facts": ["The petitioner runs a small ration shop licensed since 2009. By order dated 02.08.2026 the "
                         "licence was suspended without notice or hearing.",
                         "The order does not disclose any reason and was served only after the shop was sealed."],
               "grounds": ["A. The suspension without a hearing violates the principles of natural justice.",
                           "B. The order is unreasoned and cannot stand.",
                           "C. The petitioner's livelihood under Article 21 is affected."],
               "prayer": ["i. Quash the order dated 02.08.2026 (Annexure P1).",
                          "ii. Direct the respondents to restore the licence pending a fresh hearing."]},
         annexures=[("P1", "True copy of the order dated 02.08.2026 suspending the ration shop licence")]),
    dict(file="02_wpc_defective.pdf", number="W.P.(C) No. ______ of 2026", kind="WRIT PETITION (CIVIL)",
         article="FILED UNDER ARTICLE 226 OF THE CONSTITUTION OF INDIA",
         petitioner="Leela Varghese, aged 63, W/o late P. Varghese, Chirackal House, Kakkanad, Ernakulam District",
         respondents=["State of Kerala, represented by the Secretary, Local Self Government Department, Thiruvananthapuram",
                      "Thrikkakara Municipality, represented by its Secretary, Kakkanad"],
         counsel="S. Rao", fee=50, cause_date="12.01.2026", body=WP_BODY,
         annexures=[("P1", "True copy of the building permit application dated 12.01.2026"),
                    ("P2", "True copy of the reminder dated 20.03.2026")],
         unsigned_vakalatnama=True, illegible=["P2"], duplicate_page=5),
    dict(file="03_wpc_fixed.pdf", number="W.P.(C) No. ______ of 2026", kind="WRIT PETITION (CIVIL)",
         article="FILED UNDER ARTICLE 226 OF THE CONSTITUTION OF INDIA",
         petitioner="Leela Varghese, aged 63, W/o late P. Varghese, Chirackal House, Kakkanad, Ernakulam District",
         respondents=["State of Kerala, represented by the Secretary, Local Self Government Department, Thiruvananthapuram",
                      "Thrikkakara Municipality, represented by its Secretary, Kakkanad"],
         counsel="S. Rao", fee=250, cause_date="12.01.2026", body=WP_BODY,
         annexures=[("P1", "True copy of the building permit application dated 12.01.2026"),
                    ("P2", "True copy of the reminder dated 20.03.2026")]),
    dict(file="04_arba_late.pdf", number="Arb.A. No. ______ of 2026", kind="ARBITRATION APPEAL",
         article="FILED UNDER SECTION 37 OF THE ARBITRATION AND CONCILIATION ACT, 1996",
         petitioner="M/s Periyar Constructions Pvt. Ltd., represented by its Managing Director, Kalamassery",
         respondents=["Kerala Water Authority, represented by its Managing Director, Thiruvananthapuram"],
         counsel="R. Menon", fee=500, cause_date="20.05.2026", date_label="Date of impugned order",
         body={"facts": ["The appellant executed a pipeline contract for the respondent. Disputes on final bills "
                         "were referred to arbitration and an award was passed in its favour.",
                         "By order dated 20.05.2026 the District Court set aside the award under Section 34."],
               "grounds": ["A. The District Court re-appreciated evidence, which Section 34 does not permit.",
                           "B. No ground of patent illegality was made out."],
               "prayer": ["i. Set aside the order dated 20.05.2026 and restore the arbitral award."]},
         annexures=[("A1", "True copy of the order dated 20.05.2026 of the District Court, Ernakulam")]),
    dict(file="05_bail_minor.pdf", number="Bail Appl. No. ______ of 2026", kind="BAIL APPLICATION",
         article="FILED UNDER SECTION 483 OF THE BHARATIYA NAGARIK SURAKSHA SANHITA, 2023",
         petitioner="Anil Joseph, aged 29, S/o Joseph K.J., Kunnel House, Kalamassery (accused No. 2)",
         respondents=["State of Kerala, represented by the Public Prosecutor, High Court of Kerala"],
         counsel="S. Rao", fee=10, cause_date=None, crime="Crime No. 412/2026 of Kalamassery Police Station",
         body={"facts": ["The petitioner was arrested on 01.10.2026 in Crime No. 412/2026 of Kalamassery Police "
                         "Station. He has been in custody for eleven days.",
                         "Investigation is substantially complete and recovery, if any, is over."],
               "grounds": [],
               "prayer": ["i. Release the petitioner on bail on such conditions as this Court may impose."]},
         annexures=[("A1", "True copy of the remand report dated 02.10.2026")], unnumbered_page=3),
    dict(file="06_op_party_in_person.pdf", number="O.P. No. ______ of 2026", kind="ORIGINAL PETITION",
         article="FILED UNDER ARTICLE 227 OF THE CONSTITUTION OF INDIA",
         petitioner="Saraswathy Amma, aged 71, W/o late Gopalan Nair, Thekkedath House, Perumbavoor",
         respondents=["Perumbavoor Municipality, represented by its Secretary, Perumbavoor"],
         counsel=None, fee=200, cause_date="14.07.2026",
         body={"facts": ["I am a widow living alone. The Municipality stopped my widow pension from July 2026 "
                         "saying my papers are missing. I gave all papers again on 14.07.2026.",
                         "Nobody has told me what is wrong. I have no other income."],
               "grounds": ["A. The pension was stopped without telling me why or hearing me."],
               "prayer": ["i. Direct the Municipality to restart my widow pension and pay the arrears."]},
         annexures=[("P1", "Copy of the acknowledgement dated 14.07.2026 for papers submitted")],
         no_affidavit=True),
]

A4 = pymupdf.paper_rect("a4")
MARGIN = 64
FONT, BOLD = "tiro", "tibo"  # Times Roman and Times Bold


class Writer:
    def __init__(self, spec):
        self.doc = pymupdf.open()
        self.spec = spec
        self.n = 0

    def page(self, heading, lines, number=True):
        self.n += 1
        p = self.doc.new_page(width=A4.width, height=A4.height)
        y = MARGIN
        if heading:
            p.insert_text((MARGIN, y), heading, fontname=BOLD, fontsize=13)
            y += 28
        body = "\n\n".join(lines)
        p.insert_textbox(pymupdf.Rect(MARGIN, y, A4.width - MARGIN, A4.height - MARGIN - 20), body,
                         fontname=FONT, fontsize=11, lineheight=1.35)
        label = self.label()
        if number and label is not None:
            p.insert_text((A4.width / 2 - 18, A4.height - 36), f"Page {label}", fontname=FONT, fontsize=10)
        return p

    def label(self):
        s = self.spec
        if s.get("unnumbered_page") == self.n:
            return None
        if s.get("duplicate_page") == self.n:
            return self.n - 1
        return self.n


def build(spec) -> pymupdf.Document:
    w = Writer(spec)
    pip = spec["counsel"] is None
    resp = [f"{i}. {r}" for i, r in enumerate(spec["respondents"], 1)]
    title = [
        "IN THE HIGH COURT OF KERALA AT ERNAKULAM",
        spec["number"],
        spec["kind"] + "\n" + spec["article"],
        "PETITIONER:\n" + spec["petitioner"],
        "RESPONDENTS:\n" + "\n".join(resp),
        "PARTY IN PERSON" if pip else f"BY ADV. {spec['counsel']}",
        f"Court fee paid: Rs. {spec['fee']}",
    ]
    if spec.get("cause_date"):
        title.append(f"{spec.get('date_label', 'Cause of action arose on')}: {spec['cause_date']}")
    if spec.get("crime"):
        title.append(spec["crime"])
    w.page(None, title)

    # Page plan for the index
    sections = [("Synopsis and list of dates", 3), (spec["kind"].title(), 4)]
    nxt = 5
    if not spec.get("no_affidavit"):
        sections.append(("Affidavit", nxt)); nxt += 1
    if not pip:
        sections.append(("Vakalatnama", nxt)); nxt += 1
    for code, desc in spec["annexures"]:
        sections.append((f"Annexure {code}: {desc}", nxt)); nxt += 1
    w.page("INDEX", [f"{i}. {name} .......... page {pg}" for i, (name, pg) in enumerate(sections, 1)])

    b = spec["body"]
    w.page("SYNOPSIS", [
        "The petitioner approaches this Court aggrieved by the matters set out below. "
        + b["facts"][0],
        "LIST OF DATES",
        *(["{}: cause of action".format(spec["cause_date"])] if spec.get("cause_date") else []),
        "Filing: date of presentation",
    ])
    body = ["STATEMENT OF FACTS", *[f"{i}. {f}" for i, f in enumerate(b["facts"], 1)]]
    if b["grounds"]:
        body += ["GROUNDS", *b["grounds"]]
    body += ["PRAYER", "For the reasons stated above, it is respectfully prayed that this Court may:", *b["prayer"]]
    signer = "Petitioner in person" if pip else f"Counsel for the petitioner, Adv. {spec['counsel']}"
    body.append(f"Dated this 10th day of October, 2026.\n[Signed: {signer}]")
    w.page(spec["kind"], body)

    name = spec["petitioner"].split(",")[0]
    if not spec.get("no_affidavit"):
        sig = "Signature of deponent: ____________________" if spec.get("unsigned_affidavit") else f"[Signed: {name}, deponent]"
        w.page("AFFIDAVIT", [
            f"I, {spec['petitioner']}, do hereby solemnly affirm and state as follows:",
            "1. I am the petitioner in the above case and I know the facts of the case.",
            "2. The facts stated in the petition are true to my knowledge, information and belief.",
            "3. The annexures produced are true copies of their originals.",
            f"Solemnly affirmed at Ernakulam on 10.10.2026.\n{sig}",
            "Identified by me. Attested before me, Notary Public, Ernakulam.",
        ])
    if not pip:
        sig = ("Signature of client: ____________________\nAccepted: ____________________" if spec.get("unsigned_vakalatnama")
               else f"[Signed: {name}, client]\nAccepted: [Signed: Adv. {spec['counsel']}]")
        w.page("VAKALATNAMA", [
            f"I, {name}, the petitioner in the above case, appoint and retain Adv. {spec['counsel']} to appear, "
            "plead and act for me in the above case and in all proceedings arising from it.",
            "I agree to ratify all acts done by the advocate in pursuance of this authority.",
            sig,
        ])
    for code, desc in spec["annexures"]:
        if code in spec.get("illegible", []):
            w.page(f"ANNEXURE {code}", ["[scan unreadable]"])
        else:
            w.page(f"ANNEXURE {code}", [
                desc.upper(),
                f"This is a true copy of the document produced as Annexure {code}. The original document bears "
                "the seal and signature of the issuing authority and records the reference numbers, the date of "
                "issue and the particulars of the petitioner as set out in the petition.",
                "Certified true copy. [Signed: Adv. " + (spec["counsel"] or "Petitioner in person") + "]",
            ])
    w.doc.set_metadata({"title": spec["file"], "author": "Ready-to-List demo generator",
                        "creationDate": "D:20261010000000", "modDate": "D:20261010000000", "producer": "", "creator": ""})
    return w.doc


def main():
    OUT.mkdir(exist_ok=True)
    for spec in SPECS:
        doc = build(spec)
        doc.save(OUT / spec["file"], garbage=3, deflate=True, no_new_id=True)
        print(f"{spec['file']}: {doc.page_count} pages")
        doc.close()


if __name__ == "__main__":
    main()
