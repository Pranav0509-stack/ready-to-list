from datetime import date

import streamlit as st

from core.bootstrap import ensure_db
from core.config import JUDGES
from core.data import DEMO_DAY
from core.predict import Predictor

# Categorical slots (fixed order): ready, ageing quota, urgent, added by judge
VIA_COLORS = {"ready": "#2a78d6", "quota": "#eb6834", "urgent": "#1baf7a", "manual": "#eda100"}
VIA_LABELS = {"ready": "Ready", "quota": "Ageing quota (5+ yrs)", "urgent": "Urgent bypass", "manual": "Added by judge"}
BASE_COLOR, RTL_COLOR = "#eb6834", "#2a78d6"


@st.cache_resource
def resources():
    conn = ensure_db()
    return conn, Predictor(conn)


def sidebar() -> tuple[str, date]:
    with st.sidebar:
        jid = st.selectbox("Court", list(JUDGES), format_func=lambda j: f"{JUDGES[j]['name']}, Court {JUDGES[j]['court']}",
                           key="judge_id")
        day = st.date_input("Court date", value=DEMO_DAY, key="court_day")
        if st.button("Reset demo data", help="Rebuild the synthetic court"):
            st.cache_resource.clear()
            ensure_db(force=True)
            for k in list(st.session_state):
                if k not in ("judge_id", "court_day"):
                    del st.session_state[k]
            st.rerun()
    return jid, day
