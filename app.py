import streamlit as st

st.set_page_config(page_title="Dashboard — ₹12L NSE Multi-Agent Platform", page_icon="📊", layout="wide")

pg = st.navigation([
    st.Page("Dashboard.py", title="Dashboard", icon="📊", default=True),
    st.Page("pages/1_Swing_10L_Desk.py", title="Desk 1: ₹10L Swing", icon="📈"),
    st.Page("pages/2_Intraday_2L_Desk.py", title="Desk 2: ₹2L Intraday & F&O", icon="⚡"),
])
pg.run()
