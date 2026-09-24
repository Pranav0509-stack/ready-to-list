# Samay

Product name Samay; team name ready-to-list.

A scheduling layer for one judge's docket, built on the PUCAR "Scheduling Justice" hackathon data: 100 real
cheque-dishonour cases (NI Act s.138) from a Kerala magistrate court, their hearing-type table, their measured
substantiveness and failure-reason rates, and their calendar. Nothing in the product runs on dummy data.

"We never say no to a litigant. We stop listing hearings that were never going to happen."

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run app.py
```

The organisers' files are read from `data/pucar/` (or from `../../data/` when this folder sits inside their repo).

## What is in the app

| Page | What it does |
|---|---|
| Ready-to-List (prototype) | One judge, four screens: Today's list with time windows, listing number and a reason per case, remove and approve; Docket with readiness read from the last order; Calendar with holidays, leave and load; Results on the five case-study measures |
| Upload a docket | Upload a CSV or Excel roster, get it checked, read readiness, plan a week to a year, see what changes |
| Data, lifecycle, registry intake | Every file and column, the case lifecycle from the data, the registry's pre-filing scrutiny of a complaint, each case's file, plans by day, week, month and year |
| Which lever does what | Each Ready-to-List lever switched off in turn |
| Scheduler and model | The plain-language design note (`docs/HOW_IT_WORKS.md`) |

## How the scheduling works

Each case first gets a Samay priority score from 0 to 100 on its own details (age 35, readiness 25, disposal proximity 15, hearing churn 15, court-set urgency 10; `docs/PRIORITY_SCORE.md`). Then, each evening, for one sitting day: take the cases due, hold back those whose summons or warrant is not back, list bail
first, keep a quarter of each sitting for cases over four years old, then pack the rest by score times the chance the
hearing moves the case, per minute, with a CP-SAT solver, to 95 percent of the sitting's minutes. Group each advocate's
matters and each hearing type. After the day, set the next date from the next purpose's reference gap or the reason the
hearing failed, never a flat 60 days. Count how many times a case has been listed for the same purpose: a deferred case
(3rd or later) is held until its last failure is cured, then given priority and a fixed slot.

The model is the organisers' measured probability that each hearing type moves the case, adjusted per case by signals
read from the last order ("Await warrant", "Absent: Accused", "not ready"), with the failure reasons in their real
proportions. No machine learning is trained on 100 cases. Lever strengths are stated assumptions in `config/pucar.yaml`.

Full detail, including a stage-by-stage table of every feature: `docs/HOW_IT_WORKS.md`. Results: `outputs/` after running
`python -m scripts.run_pucar` and `python -m scripts.run_one_judge`.

## Layout

| Folder | What |
|---|---|
| `core/pucar_engine.py` | Data loading, truth model, levers, CP-SAT packing, next dates, listing rule, leave, metrics |
| `core/priority.py` | The Samay priority score and its rules |
| `core/registry.py` | Pre-filing scrutiny of an NI Act s.138 complaint |
| `config/` | `pucar.yaml` (day, flow, priorities, levers, escalation, leave, clock), `registry_ni138.yaml`, `calendar.yaml` (official Kerala HC 2026) |
| `pages/` | The five Streamlit pages |
| `scripts/` | `run_pucar.py`, `run_one_judge.py`, `data_profile.py`, the organisers' roster generator |
| `docs/` | `HOW_IT_WORKS.md`, `PRIORITY_SCORE.md`, `DATA_PROFILE.md`, `presentation.html` |
| `design-system/` | Palette and type, from the UI/UX Pro Max skill database (Legal Services) |
| `archive/highcourt_synthetic/` | The earlier High Court version on synthetic data, kept for reference, not part of the product |
