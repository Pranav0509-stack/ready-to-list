import streamlit as st

from pages.shared import page_setup

page_setup()
st.markdown("<style>section.stSidebar {display: none;}</style>", unsafe_allow_html=True)

left, mid, right = st.columns([1, 1.2, 1])
with mid:
    st.markdown("<div style='height: 14vh'></div>", unsafe_allow_html=True)
    st.markdown("# Samay")
    st.markdown("Court scheduling for one judge's docket.")
    role = st.radio("Sign in as", ["Judge", "Court master"], horizontal=True)
    name = st.text_input("Name", value="Justice Sehgal" if role == "Judge" else "Court master, Court 12")
    if st.button("Sign in", type="primary", width="stretch"):
        st.session_state.user = {"role": role, "name": name.strip() or role}
        st.switch_page("pages/judge.py" if role == "Judge" else "pages/court_master.py")
