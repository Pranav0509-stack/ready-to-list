# Samay case priority score

Out of 100 points: 35 from the age of the case, 25 from how ready it is to make progress, 15 from how close it is to completion, 15 from repeated hearings, and 10 from explicit urgency. A case earns part or all of each factor's points depending on its own details. A higher score means the case deserves more priority for today's hearing. Implementation: `core/priority.py`.

| Factor | Weight | What it means |
|---|---|---|
| Case age | 35 | How long the case has been waiting |
| Hearing readiness | 25 | How likely today's hearing is to make actual progress |
| Disposal proximity | 15 | How close the case is to being completed |
| Hearing churn | 15 | Whether the case has taken more hearings than normally expected |
| Court-set urgency | 10 | Whether the court has given the case a specific urgency or commitment |

## What it does, and does not do

It answers one question: given this case's current information, what priority score from 0 to 100 should it receive for today's hearing? It does not estimate how long a hearing will take, look at the judge's time, order cases or fit them into the list. Those belong to the scheduling layer (`core/pucar_engine.py`). Every case is scored on its own, never compared with other cases: a case gets the same score whether the roster has 100 or 3,000 cases.

## Inputs

| Information | File and column |
|---|---|
| When the case was filed | roster_sample_100.csv, filing_date |
| Which stage the case is at | roster_sample_100.csv, current_stage |
| What the next hearing is for | roster_sample_100.csv, purpose_of_next_hearing |
| Who attended the last hearing, and what the court said | roster_sample_100.csv, last_hearing_summary |
| How many hearings the case has had | roster_sample_100.csv, total_hearings_held |
| How often each type of hearing makes progress | substantiveness_by_hearing_type.csv |
| How many hearings each stage normally takes | hearing_type_reference.csv, Median Hearings per Case |
| Why hearings fail (eligibility only) | hearing_failure_reasons.csv |

Every factor is first turned into a value between 0 and 1, then multiplied by its weight. "Capped at 1" means a value can go up to 1 but never beyond it.

## 1. Case age, 35 points

age_years = (as-of date minus filing_date) / 365.25
age_value = age_years / full_points_age, capped at 1
age_points = 35 x age_value

full_points_age is the age at which a case earns all 35 points: max(5, round(average age + 2 x standard deviation of ages)), computed once when the roster is received and then fixed. For this roster: average 3.39 years, spread 2.38, so 8.15 rounds to 8. With 8, 91 of the 100 cases get their own age score and 9 cases (8.3 to 9.9 years) share the full 35 points.

Example: ST/1261/2017, filed 2017-07-13, is 9.2 years old on 2026-09-24: 9.2 / 8 is capped at 1, so 35.0 points. ST/293/2026, filed 2026-04-01, is 0.48 years old: 0.06, so 2.1 points.

## 2. Hearing readiness, 25 points

progress_rate = the substantiveness rate for the next hearing's purpose / 100
required people = by purpose (table below)
attendance_share = required people present at the last hearing / required people
attendance_factor = 0.7 + 0.3 x attendance_share
readiness_value = progress_rate x attendance_factor, capped at 1
readiness_points = 25 x readiness_value

| Next hearing | People required |
|---|---|
| Evidence complainant | Complainant, Complainant's Advocate, Accused Advocate |
| Evidence accused | Accused, Accused Advocate, Complainant's Advocate |
| Plea, Examination u/s 351 | Accused, Accused Advocate |
| Arguments, Judgement | Complainant's Advocate, Accused Advocate |
| Bail | Accused Advocate |
| Anything else | Complainant's Advocate |

Absence can reduce readiness by at most 30 percent, so one side's absence cannot bury the other side's case. The type of hearing matters more than attendance: a Plea with nobody present still earns 15.8 points (90 percent x 0.7), while defence evidence with everyone present earns 4.2 (16.7 percent x 1.0).

Progress rates, from substantiveness_by_hearing_type.csv: Admission 48.6, Cognizance 90.0, Delay condonation 29.3, Appearance 40.1, Warrant 13.5, Plea 90.0, Examination u/s 351 40.7, Evidence complainant 29.4, Evidence accused 16.7, Arguments 13.0, Judgement 100 (estimated), Bail 31.3, Reports 8.3, Application review 85.0 (estimated).

## 3. Disposal proximity, 15 points

stage_number = position of current_stage in the 11 stages (0 to 10)
disposal_value = stage_number / 10
disposal_points = 15 x disposal_value

Stages: Admission 0, Delay condonation hearing 1, Cognizance 2, Appearance 3, Warrant 4, Plea 5, Examination under s.351 6, Evidence complainant 7, Evidence accused 8, Arguments 9, Judgement 10.

## 4. Hearing churn, 15 points

expected_hearings = sum of the median hearings for every stage up to and including the current stage
overrun = total_hearings_held / expected_hearings
churn_value = overrun minus 1, no lower than 0 and capped at 1
churn_points = 15 x churn_value

Expected hearings by stage (running total of medians): Admission 1, Delay condonation 4, Cognizance 5, Appearance 8, Warrant 11, Plea 12, Examination 14, Evidence complainant 19, Evidence accused 23, Arguments 26, Judgement 28. A case at the normal number scores 0; at twice the normal number, full points.

## 5. Court-set urgency, 10 points

urgency_value = 1.0 if the last order contains "last chance"; 0.8 if it contains "for judgment"; otherwise 0
urgency_points = 10 x urgency_value

## The complete formula

Priority score = 35 x age_value + 25 x readiness_value + 15 x disposal_value + 15 x churn_value + 10 x urgency_value

The points are added, not multiplied: the oldest cases are often at stages where hearings rarely make progress, and multiplying would push them to zero, which the case study says must not happen.

Worked example, ST/1261/2017: age 9.2 years (35.0), Judgement with both advocates present (25.0), Arguments stage (13.5), 43 hearings against 26 expected (9.8), "For judgment" (8.0): 91.3. Others: ST/608/2017 69.9, ST/1166/2023 40.9, ST/7/2025 12.5, ST/293/2026 7.2. Across the 100 cases: 7.2 to 91.3, average 37.7, median 35.2; cases 4+ years old average 56.0 against 29.8 for younger cases; 80 Eligible, 20 Conditional.

## Rules beside the score (they never change it)

- R1, eligibility: Conditional (flag Confirm service) when the last order shows a summons, warrant or notice not yet returned; Ineligible when the court records prerequisite_met = false (a new field); otherwise Eligible.
- R2, age cannot be switched off: judges may adjust weights, but age never falls below 20 percent; weights are rescaled to 100.
- R3, liberty lane: bail is always listed, never competes on score.
- R4, old cases flagged: Backlog 4y+ and Ageing 5y+, so the scheduler can protect time for them.
- R5, absence has a limited effect: at most 30 percent off readiness; repeated absence should lead to court action, not a lower priority.
- R6, no personal identifiers: advocate and party IDs are never used in the score.
- R7, no case waits forever: once the days since last listing are recorded, the scheduler forces a case onto the list after a set number of days.

Optional once service is recorded: with a = the share of the type's failures caused by waiting for process, the progress rate becomes progress_rate / (progress_rate + (1 minus progress_rate) x (1 minus a)). For Warrant hearings a = 196 / 293 = 0.67, so confirmed service lifts the rate from 13.5 to about 32 percent.

## How the scheduler uses it

The scheduling layer ranks Eligible cases by score x P(substantive) per minute^0.25, adds two boosts of its own (listing number and the NI Act s.143 six-month clock), lists the liberty lane first, reserves a quarter of each sitting for 4+ year cases, and packs to 95 percent of the sitting's minutes. Conditional cases wait for the process return.
