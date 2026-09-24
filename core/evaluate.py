"""How accurate are the predictions the scheduler relies on?

Three checks, all pure functions (no Streamlit):
  holdout()             time-based split of the hearings table: train on the older 75%,
                        score on the newest 25%, as in real deployment. AUC, Brier, log loss,
                        a naive base-rate baseline, calibration bins, and duration error.
  backtest_causelists() scheduler-level check: build real cause lists, draw what happens from
                        the advocate behaviour model (core.agents), compare predicted vs actual
                        heard, effective and court minutes per day.
  learning_curve()      P(show) accuracy vs amount of training history, i.e. how the model
                        improves as the court feeds real outcomes back.

The ground truth here is synthetic (core.agents drives both the history and the backtest)
until PUCAR's hearing-failure data arrives. Once it is loaded through core.data.load_csvs,
holdout() and learning_curve() run unchanged on the real hearings table.
"""
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

from core import agents
from core.config import HEARING_TYPES, JUDGES
from core.data import DEMO_DAY, build_db, connect, df, working_days
from core.predict import ADJOURN_MINUTES, FEATURES, Predictor

EPS = 1e-6


def _history(conn) -> pd.DataFrame:
    h = df(conn, "SELECT * FROM hearings WHERE showed IS NOT NULL").dropna(subset=FEATURES)
    return h.sort_values("date", kind="stable").reset_index(drop=True)


def _time_split(h, test_size):
    cut = int(round(len(h) * (1 - test_size)))
    return h.iloc[:cut], h.iloc[cut:]


def _fit(X, y):
    return LogisticRegression(max_iter=500).fit(X[FEATURES].astype(float), y)


def _scores(y, p, train_rate):
    y = np.asarray(y, int)
    p = np.clip(np.asarray(p, float), EPS, 1 - EPS)
    two_class = len(np.unique(y)) == 2
    return {
        "auc": float(roc_auc_score(y, p)) if two_class else float("nan"),
        "brier": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "base_rate": float(y.mean()),
        "baseline_brier": float(brier_score_loss(y, np.full(len(y), train_rate))),
        "accuracy": float(((p >= 0.5).astype(int) == y).mean()),
    }


def calibration_table(y, p, bins=10) -> pd.DataFrame:
    """Quantile bins of predicted probability: mean predicted, observed rate, count."""
    d = pd.DataFrame({"y": np.asarray(y, float), "p": np.asarray(p, float)})
    d["bin"] = pd.qcut(d.p.rank(method="first"), bins, labels=False)
    t = d.groupby("bin").agg(mean_predicted=("p", "mean"), observed=("y", "mean"), count=("y", "size"))
    return t.reset_index(drop=True)


def holdout(conn, test_size=0.25, seed=0) -> dict:
    """Time-based holdout of the two Predictor models plus the duration estimate.
    seed is accepted for API symmetry; the split and the solver are deterministic."""
    h = _history(conn)
    train, test = _time_split(h, test_size)

    show = _fit(train, train.showed)
    p_show = show.predict_proba(test[FEATURES].astype(float))[:, 1]

    tr_heard, te_heard = train[train.showed == 1], test[test.showed == 1]
    eff = _fit(tr_heard, tr_heard.effective)
    p_eff = eff.predict_proba(te_heard[FEATURES].astype(float))[:, 1]

    out = {"split_date": test.date.iloc[0], "train_range": (train.date.iloc[0], train.date.iloc[-1]),
           "test_range": (test.date.iloc[0], test.date.iloc[-1])}
    out["show"] = {**_scores(test.showed, p_show, train.showed.mean()),
                   "n_train": len(train), "n_test": len(test)}
    out["effective"] = {**_scores(te_heard.effective, p_eff, tr_heard.effective.mean()),
                        "n_train": len(tr_heard), "n_test": len(te_heard)}
    out["calibration"] = {"show": calibration_table(test.showed, p_show),
                          "effective": calibration_table(te_heard.effective, p_eff)}
    for k, t in out["calibration"].items():  # expected calibration error, count-weighted
        out[k]["ece"] = float((t.mean_predicted - t.observed).abs().mul(t["count"]).sum() / t["count"].sum())

    # Duration: reference minutes x overrun learnt on train, vs the plain reference table
    ref = lambda d: d.purpose.map(lambda p: HEARING_TYPES[p]["duration"]).astype(float)
    overrun = float((tr_heard.minutes_used / ref(tr_heard)).mean())
    actual = te_heard.minutes_used.astype(float)
    pred, base = ref(te_heard) * overrun, ref(te_heard)
    out["duration"] = {
        "overrun": overrun,
        "mae": float((pred - actual).abs().mean()),
        "mape": float(((pred - actual).abs() / actual).mean() * 100),
        "baseline_mae": float((base - actual).abs().mean()),
        "baseline_mape": float(((base - actual).abs() / actual).mean() * 100),
        "mean_actual": float(actual.mean()),
        "n_test": len(te_heard),
    }
    return out


def learning_curve(conn, sizes=(250, 500, 1000, 2000, None), test_size=0.25, repeats=5, seed=0) -> pd.DataFrame:
    """P(show) AUC and Brier on the fixed newest-25% test set, training on random subsets of
    the older history of each size (None = all of it), averaged over a few repeats."""
    h = _history(conn)
    train, test = _time_split(h, test_size)
    Xte, yte = test[FEATURES].astype(float), test.showed.astype(int)
    rng = np.random.default_rng(seed)
    rows = []
    for n in sizes:
        n = len(train) if n is None else min(n, len(train))
        reps = 1 if n == len(train) else repeats
        aucs, briers = [], []
        for _ in range(reps):
            sub = train.iloc[rng.choice(len(train), n, replace=False)] if n < len(train) else train
            if sub.showed.nunique() < 2:
                continue
            p = _fit(sub, sub.showed).predict_proba(Xte)[:, 1]
            aucs.append(roc_auc_score(yte, p))
            briers.append(brier_score_loss(yte, p))
        rows.append({"n_train": n, "auc": float(np.mean(aucs)), "brier": float(np.mean(briers)),
                     "baseline_brier": float(brier_score_loss(yte, np.full(len(yte), train.showed.mean())))})
    return pd.DataFrame(rows).drop_duplicates("n_train").reset_index(drop=True)


def _truth_probs(r):
    """Ground-truth P(both sides show) and P(effective | heard) for one listed item."""
    ps = agents.p_show(r.pet_type, bool(r.fixed_slot), bool(r.bundled), bool(r.confirmed),
                       int(r.clashes), bool(r.pet_warned))
    if r.pip == 1 or not isinstance(r.res_type, str):
        pr = agents.PARTY_IN_PERSON_SHOW
    else:
        pr = agents.p_show(r.res_type, bool(r.fixed_slot), False, False, 0)
    return ps * pr, agents.p_effective_given_heard(float(r.prereq_frac), bool(r.old_unsum), r.pet_type,
                                                   bool(r.confirmed))


def backtest_causelists(seed_days=8, judges=("J1", "J2", "J3"), seed=0, db_seed=7, workdir=None) -> dict:
    """Build cause lists on a fresh synthetic court for `seed_days` working days from DEMO_DAY,
    draw outcomes from the ground-truth advocate model, compare with the predictions.

    Returns {"days": per judge-day DataFrame, "summary": dict of MAE and bias}.
    'truth_expected_*' is the ground-truth expectation, which separates model bias from
    day-to-day luck in the single 'actual' draw.
    """
    from core.readiness import run_nudges
    from core.scheduler import build_causelist

    rng = np.random.default_rng(seed)
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        path = Path(tmp) / "backtest.db"
        build_db(seed=db_seed, path=path, force=True)
        conn = connect(path)
        try:
            predictor = Predictor(conn)
            rows = []
            for day in working_days(DEMO_DAY, seed_days):
                run_nudges(conn, day, np.random.default_rng(seed + day.toordinal()))
                for jid in judges:
                    if jid not in JUDGES:
                        continue
                    items = build_causelist(conn, predictor, jid, day)["items"]
                    if items.empty:
                        continue
                    heard = eff = 0
                    minutes = exp_heard = exp_eff = exp_min = 0.0
                    for r in items.itertuples():
                        p_heard, p_eff = _truth_probs(r)
                        ref = HEARING_TYPES[r.next_purpose]["duration"]
                        exp_heard += p_heard
                        exp_eff += p_heard * p_eff
                        exp_min += p_heard * ref * np.exp(0.3 ** 2 / 2) + (1 - p_heard) * ADJOURN_MINUTES
                        if rng.random() < p_heard:
                            heard += 1
                            minutes += ref * rng.lognormal(0, 0.3)
                            eff += rng.random() < p_eff
                        else:
                            minutes += ADJOURN_MINUTES
                    rows.append({
                        "date": day.isoformat(), "judge": jid, "listed": len(items),
                        "pred_heard": float(items.p_show.sum()), "actual_heard": heard,
                        "truth_expected_heard": exp_heard,
                        "pred_effective": float(items.p_effective.sum()), "actual_effective": int(eff),
                        "truth_expected_effective": exp_eff,
                        "pred_minutes": float(items.expected_minutes.sum()), "actual_minutes": minutes,
                        "truth_expected_minutes": exp_min,
                    })
                    # Listed matters are dealt with today; push them out so tomorrow's list is new
                    conn.executemany("UPDATE cases SET next_date=? WHERE id=?",
                                     [("2099-01-01", cid) for cid in items.id])
                conn.commit()
        finally:
            conn.close()

    days = pd.DataFrame(rows)
    summary = {"judge_days": len(days)}
    for k in ("heard", "effective", "minutes"):
        err = days[f"pred_{k}"] - days[f"actual_{k}"]
        summary[f"{k}_mae"] = float(err.abs().mean())
        summary[f"{k}_bias"] = float(err.mean())  # positive = model over-predicts
        summary[f"{k}_mape"] = float((err.abs() / days[f"actual_{k}"].clip(lower=1)).mean() * 100)
        summary[f"{k}_bias_vs_truth"] = float((days[f"pred_{k}"] - days[f"truth_expected_{k}"]).mean())
    return {"days": days, "summary": summary}
