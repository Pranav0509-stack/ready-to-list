# Samay: how it works

A design note for the PUCAR "Scheduling Justice" panel. Everything here is in the code or marked as a proposal.

## 1. What the scheduler decides

Each evening, for one judge and one sitting day, the scheduler decides which due cases to list, in which sitting, in what order and time window, and the next date for each case not listed or not heard. It lists a case only when the hearing is likely to move it, holds back cases whose process is out, keeps a quarter of the day for 4+ year cases, and packs the day to 95 percent of net minutes. On the organisers' data, 60 working days from 1 October 2026 (3,000 cases, 330 minutes a day, five seeds, `python -m scripts.run_pucar --days 60`):

| Metric | Today's rules | Samay |
|---|---|---|
| Utilisation % | 94.3 | 94.6 |
| Reach rate % | 44.2 | 96.9 |
| Substantiveness % | 47.8 | 78.7 |
| 4+ year cases heard % | 32.1 | 47.3 |
| Predictability, days listed to heard | 23.3 | 2.1 |
| Substantive hearings a day | 12.0 | 13.9 |
| Wasted listings in 60 days | 2,694 | 259 |
| Disposed in 60 days | 196 | 356 |

Today's rules: 60 a day by due date, flat 60-day gap. Three judge-leave days fall in the 60, which is why utilisation is 94 percent in both arms.

## 2. The data it needs

| Field | Source now | Source in production |
|---|---|---|
| case_number, filing_date, advocate_id | roster_sample_100.csv | DRISTI case record, vakalat |
| current_stage, purpose_of_next_hearing, hearings_* counts, last_hearing_summary | roster_sample_100.csv | DRISTI last order, hearing history |
| Minutes and reference gap per type | hearing_type_reference.csv | Court master's table |
| P(substantive), failure reasons per type | substantiveness_by_hearing_type.csv, hearing_failure_reasons.csv | Recomputed nightly from POST /outcome |
| Working days, holidays, leave | court_calendar.csv, config/calendar.yaml, config/pucar.yaml | Calendar master, leave register |
| Process return status | Not in the data; simulated | Process server, e-summons return in DRISTI |
| Accused contact details, T-2 confirmation | Not in the data; simulated | e-filing form, SMS or WhatsApp reply |

## 3. The lifecycle and repeated listings

The data shows a cheque-dishonour complaint moving through Admission, Delay condonation (if filed late), Cognizance, Appearance, Plea, Examination under s.351 BNSS, Evidence complainant, Evidence accused, Arguments and Judgement. Appearance loops through Warrant when the accused does not come. Bail, Reports (mediation) and Application review sit to the side and return the case to its stage (`stage_flow`, `side_return` in config/pucar.yaml).

A purpose rarely finishes in one hearing. Mean hearings per purpose: Admission 2.12, Delay condonation 3.37, Appearance 3.26, Warrant 5.02, Evidence complainant 6.78, Evidence accused 6.42, Arguments 3.69, Judgement 3.58. So Samay treats each listing of a purpose differently.

- 1st listing: normal. Due on the reference gap, ranked by value per minute.
- 2nd listing for the same purpose: a readiness check. The T-2 confirmation asks the advocate whether the matter is ready, and any adjournment must carry a reason code. "Not ready" keeps it off the list.
- 3rd and later ("deferred"): a priority boost, a fixed time window, and the last failure must be cured before relisting. Process pending: relist only when the return is recorded. Absence: fixed slot, clustering, and a costs warning to a party who confirmed and did not appear. Unready: the missing filing must be on record.

In code today (`escalation` in config/pucar.yaml): listings per purpose are counted and reset on success. A 2nd listing adds 10 priority points; a deferred listing adds 25 and, when the last failure was process, holds the case until the return is due. The process filter, reason-coded next dates and text signals also exist in core/pucar_engine.py. Proposed: the mandatory reason code on the 2nd listing, the fixed window as a hard constraint, the cure gate for unready failures.

## 4. Pre-filing: registry scrutiny at e-filing

config/registry_ni138.yaml lists the checks: the statutory timeline (cheque presented within 90 days, notice within 30 days of the return memo, complaint within 30 days after the 15-day payment window), the documents (cheque, return memo, notice, postal proof, proof affidavit, process fee, s.141 averments for a company), summons details (full address, phone or email for e-summons under BNSS s.64(2)), and jurisdiction (s.225 enquiry if the accused lives outside the court's area).

core/registry.py computes the timeline and the days late, so the condonation petition is filed with the complaint and decided at admission (about 5 extra minutes), not in a separate track. In the data 96 of 100 cases had a Delay condonation track, 3.37 hearings per case, 29.3 percent substantive. Complete summons details attack the largest failure pool: 196 of 293 failed Warrant hearings were awaiting process return. Nothing is refused. The result is a status ("Ready for admission", "Ready, with condonation heard at admission", "Accepted, items to cure") and what is missing.

## 5. The model

Two things are computed per case. The Samay priority score says how much the case deserves today's hearing (docs/PRIORITY_SCORE.md). The engine's probability model says how likely the hearing is to be substantive, and is used for expected minutes and for the value per minute.

- P(substantive) per hearing type comes straight from substantiveness_by_hearing_type.csv (Warrant 13.5 percent, Evidence complainant 29.4, Cognizance 90); Judgement and Application review are the organisers' estimates.
- The failure-reason mix per type comes from hearing_failure_reasons.csv, grouped into process, absence, unready, court and unclear. Process failures persist until the return; the others are drawn afresh.
- Text signals from the last order note: "warrant", "summons", "nbw", "notice", "await", "return of", "process", "steps" signal process risk; "not ready", "time sought", "time", "objections", "last chance", "adjourn" signal unready risk; an "Absent:" line naming the accused or complainant signals absence. A signalled case carries 2.5 times the risk of one without, normalised so each type's average stays at the real rate.
- Lever strengths are assumptions in config/pucar.yaml: process status known 90 percent of the time, T-2 confirmation removes half of unready failures, fixed slots and clustering remove 35 percent of absences, pre-filing removes 60 percent of unready and 50 percent of process failures at admission, 40 percent of summons failures. None is measured on this court.

Which model, in one line: a calibrated probability model per hearing type, adjusted per case by signals read from the last order, with a CP-SAT solver on top. No machine learning is trained on this data, because 100 cases and about 500 observed hearings cannot support it honestly; the rates are the organisers' measured rates. As the court records outcomes (POST /outcome), the same rates are recomputed nightly and the signal multipliers can be fitted instead of assumed.

## 6. The optimiser, step by step

1. Due pool: every pending case whose next date is on or before today.
2. Process filter: a case whose summons or warrant has not returned waits until it is expected back.
3. Urgent first: Bail is always listed.
4. Ageing quota: 25 percent of each sitting's minutes for 4+ year cases, best value first. Locked.
5. Value per minute: value = Samay priority score x P(substantive). The Samay score (0 to 100, docs/PRIORITY_SCORE.md, core/priority.py) is 35 points for case age (full at 8 years for this roster), 25 for hearing readiness (the type's real progress rate, reduced by at most 30 percent for absent required people), 15 for disposal proximity (how far along the 11 stages), 15 for hearing churn (hearings held against the median expected), 10 for court-set urgency ("last chance", "for judgment"). Two scheduling-layer boosts sit on top: the listing number (2nd listing +10, deferred +25) and the s.143 six-month clock (+15 while inside it). Ranking key: value over expected minutes to the power 0.25.
6. CP-SAT knapsack per sitting: the set that maximises total value within 95 percent of net minutes. Expected minutes = P(sub) x reference minutes, plus 2 for an adjournment, plus a changeover.
7. Waitlist: the next five ready cases per sitting stand by, called if time remains.
8. Grouping: order by urgent, then purpose, then advocate, so an advocate's matters are called together and the judge switches type less often (4 a day against 13).
9. Time windows: a 60-minute window from each case's expected start. Morning takes pre-trial purposes (Admission to Plea, Bail, Reports, Applications); afternoon takes s.351 examination, Evidence, Arguments, Judgement.
10. Next dates: after a substantive hearing, the reference gap for the next purpose (Appearance 21 days, Evidence 14, Reports 45); after a failure, the reason gap (process 21 or until the return, absence 7, unready 10, court 3); not reached, next day.

Day structure: 10:30 to 12:30 and 13:30 to 17:00, 330 minutes. Morning opens with 15 minutes for pronouncements and mentions. Changeover is 1 minute within a purpose, 3 on a switch. Calling and adjourning a matter costs 2 minutes.

## 7. The calendar

Working days come from the organisers' court_calendar.csv for September to December 2026. After that, config/calendar.yaml takes over with weekends, working Saturdays, holidays and the summer, Onam and Christmas vacations. The CSV's holiday names look like a Gujarat list, not Kerala's; production must use the court's own master.

Judge leave is in the engine (`judge_leave.dates` in config/pucar.yaml, three demo days). Under today's rules the cases due that day take the flat gap. Under Samay they become due the next sitting day and are re-planned, landing within the same week where there is room, never at the flat 60 days. Proposed: urgent matters go to the link judge the same day; the leave register replaces the demo dates.

## 8. What the judge and the court master see

Three pages, all on the uploaded docket. Sign in as Judge or Court master; nothing else sits in the sidebar.

Court master. Docket file: upload the judge's docket as Excel or CSV; the file is checked and any problem is named in plain words; the quarter is planned from it. Run today's list: the day's list by sitting with time, listing number and advocate; call each matter, record the outcome, and the next date follows by rule. New complaint: the registry's scrutiny at e-filing, with statutory dates computed, documents, summons details and jurisdiction.

Judge. A menu in the side panel: Today, Calendar, Cases, Priority, Insight. Today: the cause list for any sitting day with time, listing number, score, advocate and why it is listed; when a hearing is over the judge marks it Heard, or the case Disposed, and it is fixed (locked in the list, and a disposed case leaves every later list); remove or restore; approve. Calendar: a month grid with every date, sittings with their case count, holidays and leave; click a day for its list. Cases: every case with score, status, flags and history. Priority: the five weights, adjustable with age never below 20, the docket ranked live, and for any case the points of each factor with the evidence behind them. Insight: the case-study measures against today's rules.

The pages are fixed to the screen height; tables scroll inside their own frames.

## 9. Backend architecture for production

The code supports a nightly batch planner: load the docket, filter, solve per sitting, write the list and next dates. Proposed API:

- POST /plan/{judge}/{date}: plan one judge's day, return the proposed list and waitlist.
- GET /causelist?judge=&date=: the approved list, windows and reasons.
- POST /outcome: record outcome, reason code and minutes; updates the rates.
- GET /nextdate?case=&outcome=&reason=: the recommended next date and why.

The prototype reads the organisers' CSVs and keeps state in memory for a run; production would keep cases, hearings, cause lists, calendar and an audit table in Postgres. It bolts on to DRISTI: it reads case, order and process-return data through DRISTI's APIs and writes only a proposed list and a recommended next date. The court master approves in DRISTI, which stays the record. Proposed: every decision, override and confirmed next date goes to an audit log with actor, case, action and reason.

## 10. Known limits and assumptions

- Lever sizes (90, 50, 35, 60, 50, 40 percent) are assumptions, not measured on this court.
- Results are a simulation: 100 real cases scaled to 3,000 with the organisers' generator, over 60 days, short next to case lifetimes.
- The roster's next purposes are late-stage heavy (5 percent Appearance against 39 on the real day), so lists skew late.
- The tool proposes who to hear and when. It never changes what a judge decides.

## 11. Every feature built, stage by stage

| Stage of a case | What happens today | What Samay does | Where |
|---|---|---|---|
| Filing | Registry checks the complaint by hand after filing; limitation surfaces in a separate hearing track | Statutory dates computed at e-filing, documents and summons details checked, s.225 enquiry flagged, condonation heard at admission. Nothing is refused | core/registry.py, config/registry_ni138.yaml, page "Data, lifecycle, registry intake" |
| Admission, cognizance | Listed by due date | Listed when ready; a late complaint's condonation is decided at admission | core/pucar_engine.py (prefiling lever) |
| Appearance, warrant | Relisted every 60 days whether or not the summons is back | Held until the process return is due; deferred cases never relisted blind | process_tracking lever, escalation rule |
| Plea, s.351 examination | Listed by due date | Listed by value per minute in the morning sitting | _pack |
| Evidence, arguments, judgement | 60 a day listed, about 44 percent reached | Afternoon sitting, packed to 95 percent of net minutes, grouped by purpose and advocate | _pack, grouping |
| Any failed hearing | Flat 60-day next date | Next date by reason: process 21 days or the return, absence 7, unready 10, court 3 | _reschedule |
| Any successful hearing | Flat 60-day next date | The reference gap for the next purpose | _reschedule |
| Repeated listings | No memory of how many times | 1st normal, 2nd priority boost and readiness check, deferred held until cured then fixed slot | escalation |
| Cases past six months | No rule | Priority boost (NI Act s.143(3)) | statutory_clock |
| Cases over four years | No rule | A quarter of every sitting, locked | ageing_quota |
| Judge on leave | Cases take the flat gap | Cases move to the next sitting day with room | judge_leave |
| Holidays and vacations | Court calendar | Organisers' calendar for the quarter, then the official Kerala High Court 2026 calendar | config/calendar.yaml |
| Any docket | Manual | Upload CSV or Excel, checked and planned | pages/start.py |
| Reporting | None | The five handbook measures, per-lever ablation, per-listing success rates, per-case journeys | scripts/run_pucar.py, scripts/run_one_judge.py, pages |
