import streamlit as st

from core.data import df
from pages.common import resources, sidebar

conn, _ = resources()
sidebar()
st.title("Audit log")
st.caption("Every AI decision and every human override: what was decided, why, and by whom.")
log = df(conn, "SELECT * FROM audit_log ORDER BY ts DESC")
only = st.toggle("Only human overrides")
if only:
    log = log[log.overridden_by.notna()]
st.dataframe(log, hide_index=True, width="stretch")

st.subheader("Notifications sent")
st.dataframe(df(conn, "SELECT * FROM notifications ORDER BY rowid DESC LIMIT 300"), hide_index=True,
             width="stretch")
