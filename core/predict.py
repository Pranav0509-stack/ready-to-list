"""P(show), P(effective) and expected minutes.

Two logistic regressions trained on the hearings table; duration is the reference
table times the observed overrun factor. Trains in well under a second.
"""
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from core import taxonomy as T
from core.config import HEARING_TYPES
from core.data import df

FEATURES = ["show_rate", "res_show_rate", "confirmed", "bundled", "clashes", "fixed_slot", "prereq_frac", "old_unsum"]
ADJOURN_MINUTES = 2  # a call-over and adjournment still costs the court a couple of minutes


def reference_minutes(frame, purpose_col):
    """Expected hearing minutes: the case taxonomy (type x sub-type x stage x paper book x parties x
    cover sheet) when the case has a type, else the purpose-level reference table."""
    out = []
    for r in frame.itertuples():
        purpose = getattr(r, purpose_col)
        cat = getattr(r, "category", None)
        if isinstance(cat, str) and cat in T.TYPES:
            age = getattr(r, "age_years", 0) or 0
            out.append(T.mean_minutes(cat, r.subtype, T.PURPOSE_STAGE[purpose], pages=getattr(r, "pages", None),
                                      parties=int(getattr(r, "parties", 2) or 2),
                                      cover_sheet=bool(age >= 4 and getattr(r, "summary_ok", False))))
        else:
            out.append(HEARING_TYPES[purpose]["duration"])
    return pd.Series(out, index=frame.index, dtype=float)


class Predictor:
    def __init__(self, conn):
        h = df(conn, "SELECT * FROM hearings WHERE showed IS NOT NULL").dropna(subset=FEATURES)
        X = h[FEATURES].astype(float)
        self.show = LogisticRegression(max_iter=500).fit(X, h.showed)
        heard = h[h.showed == 1]
        self.eff = LogisticRegression(max_iter=500).fit(heard[FEATURES].astype(float), heard.effective)
        heard = heard.join(df(conn, "SELECT id, category, subtype, pages, parties FROM cases").set_index("id"),
                           on="case_id")
        ratio = (heard.minutes_used / reference_minutes(heard, "purpose")).mean()
        self.overrun = float(ratio) if np.isfinite(ratio) else 1.0
        self.n_train = len(h)

    def coefficients(self) -> pd.DataFrame:
        return pd.DataFrame({"feature": FEATURES, "P(show) weight": self.show.coef_[0].round(2),
                             "P(effective) weight": self.eff.coef_[0].round(2)})

    def predict(self, feats: pd.DataFrame) -> pd.DataFrame:
        """feats needs FEATURES plus next_purpose. Returns p_show, p_effective, expected_minutes."""
        X = feats[FEATURES].astype(float)
        p_show = self.show.predict_proba(X)[:, 1]
        p_eff = p_show * self.eff.predict_proba(X)[:, 1]
        dur = reference_minutes(feats, "next_purpose").values * self.overrun
        # Each case also costs a changeover: calling it, counsel stepping up
        exp_min = dur * p_show + ADJOURN_MINUTES * (1 - p_show) + T.DAY["changeover_same_type"]
        return pd.DataFrame({"p_show": p_show.round(2), "p_effective": p_eff.round(2),
                             "duration": dur.round(1), "expected_minutes": exp_min.round(1)},
                            index=feats.index)


def learn_from_outcome(conn, advocate_id, showed: bool, alpha=0.1):
    """After each hearing, move the advocate's show rate toward what they actually did."""
    conn.execute("UPDATE advocates SET show_rate = show_rate*(1-?) + ?*? WHERE id=?",
                 (alpha, alpha, float(showed), advocate_id))
