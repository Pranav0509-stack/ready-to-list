# Working on Ready-to-List

The hackathon manual (PUCAR, "Scheduling Justice") is the source of truth. The team's build bible maps every feature to it. When code and the manual disagree, the manual wins.

## Branches

- `main` is always demo-ready. Nobody pushes to it directly.
- Work on `feat/<short-name>` or `fix/<short-name>`, open a PR into `main`, and get one teammate's review.
- The submission branch is `hackathon/ready-to-list`. It is frozen at 3:30 PM for rehearsal and pushed by 4:30 PM.

## Commits

Small and named for what they do: `scheduler: weight knapsack by P(effective)`. No generated data (`data/court.db`) or `.venv` in commits.

## Where things live

| Folder | What |
|---|---|
| `core/` | Services with no UI: data adapter, readiness, predictor, scheduler, next date, simulator, evaluation |
| `pages/` | Streamlit screens, one per role |
| `config/judge_rules.yaml` | Judge styles, locked rules, the hearing-type reference table |
| `scripts/` | Checks and dataset helpers |
| `docs/` | The build bible and the pitch notes |

## Plugging in the organisers' data

Drop their CSVs into `data/raw/`, run `.venv/bin/python -m scripts.inspect_dataset` to see the columns, fill in `COLUMN_MAP` in `core/data.py`, then press "Reset demo data" in the app. No other file should need to change.
