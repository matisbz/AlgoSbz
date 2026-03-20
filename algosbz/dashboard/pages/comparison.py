import streamlit as st
import pandas as pd

from algosbz.core.config import load_config, load_instrument_config
from algosbz.data.loader import DataLoader
from algosbz.backtest.engine import BacktestEngine
from algosbz.dashboard.components import comparison_equity_chart
from algosbz.dashboard.theme import section_title, kpi_row


STRATEGY_MAP = {
    "Vol Mean Reversion": ("algosbz.strategy.volatility_mean_reversion", "VolatilityMeanReversion"),
    "Trend Pullback": ("algosbz.strategy.trend_pullback", "TrendPullback"),
}


def _load_strategy(name: str):
    module_path, class_name = STRATEGY_MAP[name]
    import importlib
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    return cls()


def render():
    st.markdown("## Strategy Comparison")

    loader = DataLoader()
    symbols = loader.available_symbols()

    # ── Config ─────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns([2, 3, 1.5, 1.5])
    with c1:
        symbol = st.selectbox("Symbol", symbols, key="cmp_sym", label_visibility="collapsed")
    with c2:
        selected = st.multiselect("Strategies", list(STRATEGY_MAP.keys()),
                                  default=list(STRATEGY_MAP.keys()),
                                  label_visibility="collapsed")
    with c3:
        start_date = st.date_input("From", value=pd.Timestamp("2015-01-01"),
                                   key="cmp_s", label_visibility="collapsed")
    with c4:
        end_date = st.date_input("To", value=pd.Timestamp("2024-12-31"),
                                 key="cmp_e", label_visibility="collapsed")

    if st.button("Run Comparison", type="primary", use_container_width=True):
        if not selected:
            st.warning("Select at least one strategy")
            return

        config = load_config()
        instrument = load_instrument_config(symbol)
        data = loader.load(symbol, start=str(start_date), end=str(end_date))

        results = {}
        progress = st.progress(0)
        for i, name in enumerate(selected):
            strategy = _load_strategy(name)
            engine = BacktestEngine(config, instrument)
            results[name] = engine.run(strategy, data, symbol)
            progress.progress((i + 1) / len(selected))
        progress.empty()

        st.session_state["cmp_results"] = results
        st.session_state["cmp_symbol"] = symbol

    # ── Results ────────────────────────────────────────────
    if "cmp_results" not in st.session_state:
        return

    results = st.session_state["cmp_results"]
    symbol = st.session_state["cmp_symbol"]

    st.markdown("---")

    # Equity overlay
    st.plotly_chart(
        comparison_equity_chart(results, f"Equity  ·  {symbol}"),
        use_container_width=True,
    )

    # Per-strategy KPIs side by side
    section_title("Performance Summary")
    cols = st.columns(len(results))
    for i, (name, result) in enumerate(results.items()):
        m = result.metrics_summary()
        with cols[i]:
            ret = m["Total Return (%)"]
            pf = m["Profit Factor"]
            st.markdown(f"#### {name}")
            kpi_row([
                ("Trades", str(m["Total Trades"]), ""),
                ("Win Rate", f"{m['Win Rate (%)']:.1f}%", "positive" if m["Win Rate (%)"] >= 50 else ""),
            ])
            kpi_row([
                ("PF", f"{pf:.2f}", "positive" if pf >= 1.0 else "negative"),
                ("Sharpe", f"{m['Sharpe Ratio']:.2f}", "positive" if m["Sharpe Ratio"] > 0 else "negative"),
            ])
            kpi_row([
                ("Max DD", f"{m['Max Drawdown (%)']:.2f}%", "negative"),
                ("Return", f"{ret:+.2f}%", "positive" if ret >= 0 else "negative"),
            ])

    # Metrics table
    section_title("Detailed Metrics")
    metrics_data = {name: r.metrics_summary() for name, r in results.items()}
    df = pd.DataFrame(metrics_data).T
    st.dataframe(df, use_container_width=True)
