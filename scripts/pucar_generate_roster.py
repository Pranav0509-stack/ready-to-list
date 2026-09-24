#!/usr/bin/env python3
"""Scale data/roster_sample_100.csv up to a bigger synthetic roster.

Method: bootstrap resampling. Each output row is a full row copied from the
100-case sample (preserving the real joint distribution of stage, purpose,
hearing counts and filing-date age mix, including the 4+ year backlog cases
already present in the sample) with a freshly minted case_number,
filing_number and party_id so nothing collides, and advocate_id redrawn from
a pool sized proportionally to the original ratio of cases per advocate.

This is a resampling of the real 100-case sample, not a generative model of
case behaviour -- treat outputs as "more of the same mix", not as
independently simulated cases.
"""
import argparse

import numpy as np
import pandas as pd


def generate(num_cases: int, seed: int, base_path: str) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    base = pd.read_csv(base_path, parse_dates=["filing_date"])

    picks = rng.integers(0, len(base), size=num_cases)
    out = base.iloc[picks].reset_index(drop=True).copy()

    years = out["filing_date"].dt.year.astype(str)
    out["case_number"] = [f"ST/{i + 1}/{y}" for i, y in enumerate(years)]
    out["filing_number"] = [f"KL-{i + 1:06d}-{y}" for i, y in enumerate(years)]
    out["party_id"] = [f"PARTY-{i + 1:05d}" for i in range(num_cases)]

    cases_per_advocate = len(base) / base["advocate_id"].nunique()
    n_advocates = max(1, round(num_cases / cases_per_advocate))
    out["advocate_id"] = [f"ADV-{a:03d}" for a in rng.integers(1, n_advocates + 1, size=num_cases)]

    return out.sort_values("filing_date").reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--num-cases", type=int, default=3000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--base", default="../data/roster_sample_100.csv")
    parser.add_argument("--out", default="../data/roster_3000.csv")
    args = parser.parse_args()

    df = generate(args.num_cases, args.seed, args.base)
    df.to_csv(args.out, index=False)
    print(f"Wrote {len(df)} cases to {args.out}")


if __name__ == "__main__":
    main()
