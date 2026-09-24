# Ready-to-List

Cause-list and hearing-day tools for the judge and the court master. The AI recommends and the judge decides.

"We never say no to a litigant. We just stop listing hearings that were never going to happen."

Built for "Scheduling Justice", the PUCAR hackathon at FOSS United Week. The hackathon manual is the source of truth; the team's build bible maps every feature to it (see `docs/BIBLE.md`).

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run app.py
```

The first run builds `data/court.db`: a synthetic court with 3 benches, 2,700 pending cases and 150 advocates. To load the organisers' CSVs instead, put them in `data/raw/` and edit `COLUMN_MAP` in `core/data.py`. Nothing else changes. The sidebar has a "Reset demo data" button.

## Screens

| Screen | For | What it does |
|---|---|---|
| Judge dashboard | Judge | KPIs, a timeline of the day by block, a reason for each listing, override with a live impact meter, Approve. Config tab with locked rules. Docket health tab. Case drawer with the summary cover sheet. |
| Court master | Court master | One-tap outcome (effective / heard, not effective / adjourned + reason code), live ETAs, next-date suggestion with the reason, Confirm or Change. |
| Simulator | Panel | 60 days of today's rules vs Ready-to-List, same roster and seed, with the five judging metrics. |
| Model accuracy | Panel | Time-based holdout, calibration, backtest of real cause lists, learning curve. |
| Audit log | Everyone | Every AI decision and every human override. |

## Core services (`core/`)

| Module | Function | Does |
|---|---|---|
| `readiness.py` | `case_frame`, `readiness`, `run_nudges` | Score 0-100 (filing 30, prerequisites 40, counsel confirmed 20, summary verified 10), state, priority, T-2 intent check |
| `predict.py` | `Predictor.predict` | Logistic regression for P(show) and P(effective), trained on past hearings; expected minutes |
| `scheduler.py` | `build_causelist`, `impact`, `approve` | Urgent bypass, then the ageing quota, then a CP-SAT knapsack per block (priority × P(effective), filled to 95%), advocate clustering into 1-hour windows, waitlist |
| `nextdate.py` | `next_date`, `record_outcome`, `confirm_next_date` | today + max(ideal gap, prerequisite time), then the first day with capacity, skipping holidays, the judge's leave and the advocate's other listings |
| `simulate.py` | `run`, `summary` | Agent simulation (diligent / busy / chronic adjourner advocates) |
| `summary.py` | `summarise` | Cover sheet for old cases. The LLM output is cached for the demo |
| `evaluate.py` | `holdout`, `backtest_causelists`, `learning_curve` | Model accuracy against naive baselines |

Locked rules in `config/judge_rules.yaml` (the UI can't turn them off): the 25% ageing quota for 5+ year cases, the urgent bypass for bail, habeas corpus and stay, and the readiness gate of 60.

## Results so far (synthetic court, 60 working days, default settings)

The simulated baseline is calibrated to the manual's case study: about 60 listed, 20 heard and 10 effective a day.

| Judge | Effective hearings a day | Cases disposed | Change in 5+ year cases | Listed cases heard |
|---|---|---|---|---|
| Sehgal | 8.5 today, 19.3 ours | 15 today, 59 ours | +23 today, -24 ours | 24% today, 72% ours |
| Dimakar | 10.8 today, 21.9 ours | 58 today, 68 ours | -24 today, -39 ours | 31% today, 76% ours |
| Joshi | 12.2 today, 31.6 ours | 17 today, 45 ours | +21 today, -10 ours | 38% today, 74% ours |

Model accuracy on a time-based holdout (6,402 train, 2,134 test hearings):

| Check | Ours | Naive baseline |
|---|---|---|
| P(show) AUC / Brier | 0.77 / 0.196 | 0.50 / 0.250 |
| P(effective given heard) AUC / Brier | 0.85 / 0.142 | 0.50 / 0.245 |
| Duration mean absolute error | 2.6 min | 2.6 min (reference table) |
| Heard per cause list, backtest error | 1.9 hearings (10.7%) | |

## Honest notes

- Every number above comes from synthetic data. Re-run the simulator and the Model accuracy page on the organisers' data before quoting them.
- Duration prediction does not beat the reference table yet.
- In production the summary model runs self-hosted on court servers (Kerala HC AI policy).
