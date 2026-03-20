import streamlit as st
import pandas as pd

from algosbz.core.config import load_config, load_instrument_config
from algosbz.data.loader import DataLoader
from algosbz.data.resampler import resample
from algosbz.backtest.engine import BacktestEngine
from algosbz.dashboard.components import equity_curve_chart, drawdown_chart, trades_on_price_chart
from algosbz.dashboard.theme import section_title, kpi_row


STRATEGY_MAP = {
    "Vol Mean Reversion": ("algosbz.strategy.volatility_mean_reversion", "VolatilityMeanReversion"),
    "Trend Pullback": ("algosbz.strategy.trend_pullback", "TrendPullback"),
    "H4 Mean Reversion": ("algosbz.strategy.h4_mean_reversion", "H4MeanReversion"),
    "Swing Breakout": ("algosbz.strategy.swing_breakout", "SwingBreakout"),
}

RECOMMENDED = {
    "Vol Mean Reversion": ["USDCHF"],
    "Trend Pullback": ["GBPJPY", "XTIUSD"],
    "H4 Mean Reversion": ["XTIUSD"],
    "Swing Breakout": ["XTIUSD", "USDJPY"],
}


def _load_strategy(name: str, params: dict = None):
    module_path, class_name = STRATEGY_MAP[name]
    import importlib
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    return cls(params)


def render():
    st.markdown("## Backtest")

    loader = DataLoader()
    symbols = loader.available_symbols()

    # ── Config bar ─────────────────────────────────────────
    c1, c2, c3, c4 = st.columns([2, 2, 1.5, 1.5])
    with c1:
        strategy_name = st.selectbox("Strategy", list(STRATEGY_MAP.keys()), label_visibility="collapsed",
                                     help="Select trading strategy")
    with c2:
        rec = RECOMMENDED.get(strategy_name, symbols)
        default_idx = 0
        for i, s in enumerate(symbols):
            if s in rec:
                default_idx = i
                break
        symbol = st.selectbox("Symbol", symbols, index=default_idx, label_visibility="collapsed",
                              help="Trading instrument")
    with c3:
        start_date = st.date_input("From", value=pd.Timestamp("2015-01-01"), label_visibility="collapsed")
    with c4:
        end_date = st.date_input("To", value=pd.Timestamp("2024-12-31"), label_visibility="collapsed")

    # Recommendation hint
    if symbol not in RECOMMENDED.get(strategy_name, []):
        st.caption(f"Best pairs for {strategy_name}: {', '.join(RECOMMENDED.get(strategy_name, []))}")

    # ── Advanced params ────────────────────────────────────
    with st.expander("Parameters", expanded=False):
        strat_temp = _load_strategy(strategy_name)
        params = {}
        param_cols = st.columns(3)
        for i, (key, default) in enumerate(strat_temp.params.items()):
            with param_cols[i % 3]:
                if key == "timeframe":
                    params[key] = st.selectbox(key, ["M5", "M15", "H1", "H4", "D1"],
                                               index=["M5", "M15", "H1", "H4", "D1"].index(default))
                elif isinstance(default, float):
                    params[key] = st.number_input(key, value=default, format="%.4f")
                elif isinstance(default, int):
                    params[key] = st.number_input(key, value=default, step=1)

    # ── Run ────────────────────────────────────────────────
    if st.button("Run Backtest", type="primary", use_container_width=True):
        with st.spinner(""):
            config = load_config()
            instrument = load_instrument_config(symbol)
            data = loader.load(symbol, start=str(start_date), end=str(end_date))

            strategy = _load_strategy(strategy_name, params)
            engine = BacktestEngine(config, instrument)
            result = engine.run(strategy, data, symbol)

        st.session_state["bt_result"] = result
        st.session_state["bt_symbol"] = symbol
        st.session_state["bt_strategy"] = strategy_name
        st.session_state["bt_data"] = data
        st.session_state["bt_params"] = params

    # ── Results ────────────────────────────────────────────
    if "bt_result" not in st.session_state:
        return

    result = st.session_state["bt_result"]
    symbol = st.session_state["bt_symbol"]
    strategy_name = st.session_state["bt_strategy"]
    metrics = result.metrics_summary()

    st.markdown("---")

    # KPI row
    ret = metrics["Total Return (%)"]
    ret_class = "positive" if ret >= 0 else "negative"
    pf = metrics["Profit Factor"]
    pf_class = "positive" if pf >= 1.0 else "negative"

    kpi_row([
        ("Total Trades", str(metrics["Total Trades"]), ""),
        ("Win Rate", f"{metrics['Win Rate (%)']:.1f}%", "positive" if metrics["Win Rate (%)"] >= 50 else ""),
        ("Profit Factor", f"{pf:.2f}", pf_class),
        ("Sharpe Ratio", f"{metrics['Sharpe Ratio']:.2f}", "positive" if metrics["Sharpe Ratio"] > 0 else "negative"),
        ("Max Drawdown", f"{metrics['Max Drawdown (%)']:.2f}%", "negative"),
        ("Return", f"{ret:+.2f}%", ret_class),
    ])

    # Charts
    col_eq, col_dd = st.columns([2, 1])
    with col_eq:
        st.plotly_chart(
            equity_curve_chart(result, f"{strategy_name}  ·  {symbol}"),
            use_container_width=True,
        )
    with col_dd:
        st.plotly_chart(
            drawdown_chart(result),
            use_container_width=True,
        )

    # Trades on price
    if "bt_data" in st.session_state and result.trades:
        data = st.session_state["bt_data"]
        params = st.session_state.get("bt_params", {})
        tf = params.get("timeframe", "H1")
        price_df = resample(data, tf)

        first_trade = min(t.entry_time for t in result.trades)
        last_trade = max(t.exit_time for t in result.trades)
        margin = pd.Timedelta(days=5)
        price_df = price_df[
            (price_df.index >= first_trade - margin) &
            (price_df.index <= last_trade + margin)
        ]

        st.plotly_chart(
            trades_on_price_chart(result, price_df, "Trade Entries & Exits"),
            use_container_width=True,
        )

    # Trade log
    section_title("Trade Log")
    trades_df = result.to_trades_dataframe()
    if not trades_df.empty:
        st.dataframe(trades_df, use_container_width=True, height=400)
    else:
        st.info("No trades generated")
