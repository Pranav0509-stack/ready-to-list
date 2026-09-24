"""First thing to run when the organisers' datasets arrive.

Prints every CSV in data/raw/ with its columns, types and a sample, then guesses
which of our fields each column maps to. Copy the guesses into COLUMN_MAP in
core/data.py, fix what is wrong, and press "Reset demo data" in the app.

Run: .venv/bin/python -m scripts.inspect_dataset
"""
import difflib

import pandas as pd

from core.data import RAW_DIR

# Our fields per expected dataset (manual section 5: roster, calendar, sample cause list,
# hearing-type reference table, hearing-failure distribution)
OURS = {
    "cases": ["id", "title", "filing_date", "case_type", "stage", "next_purpose", "urgency_flag", "judge_id", "next_date"],
    "case_parties": ["case_id", "advocate_id", "side", "party_in_person"],
    "advocates": ["id", "name"],
    "calendar": ["date", "judge_id", "holiday", "judge_leave"],
    "causelists": ["judge_id", "date", "seq", "case_id", "slot_start", "slot_end"],
    "hearing_types": ["purpose", "priority", "duration", "ideal_gap"],
    "hearings": ["case_id", "date", "purpose", "outcome", "reason_code", "minutes_used"],
}
ALIASES = {"case_no": "id", "case_number": "id", "cnr": "id", "filed_on": "filing_date", "date_of_filing": "filing_date",
           "purpose_of_hearing": "next_purpose", "next_hearing_date": "next_date", "advocate": "advocate_id",
           "time_required": "duration", "minutes": "duration", "gap": "ideal_gap", "reason": "reason_code"}


def guess(col):
    c = col.strip().lower().replace(" ", "_")
    if c in ALIASES:
        return ALIASES[c]
    fields = sorted({f for fs in OURS.values() for f in fs})
    m = difflib.get_close_matches(c, fields, n=1, cutoff=0.6)
    return m[0] if m else None


def main():
    files = sorted(RAW_DIR.glob("*.csv")) if RAW_DIR.exists() else []
    if not files:
        print(f"No CSVs in {RAW_DIR}. Put the organisers' files there and run again.")
        return
    for f in files:
        d = pd.read_csv(f)
        print(f"\n=== {f.name}: {len(d):,} rows, {len(d.columns)} columns")
        for col in d.columns:
            print(f"  {col!r:32} {str(d[col].dtype):10} e.g. {d[col].dropna().head(2).tolist()}  -> {guess(col)}")


if __name__ == "__main__":
    main()
