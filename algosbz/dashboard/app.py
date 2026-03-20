import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st
from algosbz.dashboard.theme import inject_theme

st.set_page_config(
    page_title="AlgoSbz",
    page_icon="",
    layout="wide",
    initial_sidebar_state="expanded",
)

inject_theme()

# ── Sidebar ────────────────────────────────────────────────
with st.sidebar:
    st.markdown("""
    <div class="sidebar-brand">
        <h1>AlgoSbz</h1>
        <p>Prop Firm Challenge Lab</p>
    </div>
    """, unsafe_allow_html=True)

    page = st.radio(
        "Navigation",
        ["Backtest", "Comparison", "Challenge"],
        index=0,
        label_visibility="collapsed",
    )

    st.markdown("---")
    st.markdown("""
    <div style="padding: 12px 0;">
        <p style="font-size: 0.7rem; color: var(--text-muted); margin: 0;">
            Honest backtesting &middot; No look-ahead<br>
            Spread + Slippage + Commission
        </p>
    </div>
    """, unsafe_allow_html=True)

# ── Page Router ────────────────────────────────────────────
if page == "Backtest":
    from algosbz.dashboard.pages.backtest import render
    render()
elif page == "Comparison":
    from algosbz.dashboard.pages.comparison import render
    render()
elif page == "Challenge":
    from algosbz.dashboard.pages.challenge import render
    render()
