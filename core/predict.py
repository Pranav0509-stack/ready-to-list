"""P(show), P(effective) and expected minutes.

Two logistic regressions trained on the hearings table; duration is the reference
table times the observed overrun factor. Trains in well under a second.
"""
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from core.config import HEARING_TYPES
from core.data import df

FEATURES = ["show_rate", "res_show_rate", "confirmed", "bundled", "clashes", "fixed_slot", "prereq_frac", "old_unsum"]
ADJOURN_MINUTES = 2  # a call-over and adjournment still costs the court a couple of minutes


class Predictor:
    def __init__(self, conn):
        h = df(conn, "SELECT * FROM hearings WHERE showed IS NOT NULL").dropna(subset=FEATURES)
        X = h[FEATURES].astype(float)
        self.show = LogisticRegression(max_iter=500).fit(X, h.showed)
        heard = h[h.showed == 1]
        self.eff = LogisticRegression(max_iter=500).fit(heard[FEATURES].astype(float), heard.effective)
        ratio = (heard.minutes_used / heard.purpose.map(lambda p: HEARING_TYPES[p]["duration"])).mean()
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
        dur = feats.next_purpose.map(lambda p: HEARING_TYPES[p]["duration"]).values * self.overrun
        exp_min = dur * p_show + ADJOURN_MINUTES * (1 - p_show)
        return pd.DataFrame({"p_show": p_show.round(2), "p_effective": p_eff.round(2),
                             "duration": dur.round(1), "expected_minutes": exp_min.round(1)},
                            index=feats.index)


def learn_from_outcome(conn, advocate_id, showed: bool, alpha=0.1):
    """After each hearing, move the advocate's show rate toward what they actually did."""
    conn.execute("UPDATE advocates SET show_rate = show_rate*(1-?) + ?*? WHERE id=?",
                 (alpha, alpha, float(showed), advocate_id))
