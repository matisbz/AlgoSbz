import streamlit as st
import pandas as pd
import numpy as np

from algosbz.core.config import load_config, load_instrument_config, ChallengePhaseConfig
from algosbz.data.loader import DataLoader
from algosbz.backtest.engine import BacktestEngine
from algosbz.risk.prop_firm import PropFirmSimulator
from algosbz.dashboard.components import (
    equity_curve_chart, drawdown_chart, profit_distribution_chart,
    sequential_challenge_chart, sequential_equity_chart,
)
from algosbz.dashboard.theme import section_title, kpi_row, phase_badge, COLORS


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
    st.markdown("## Challenge Simulator")

    loader = DataLoader()
    symbols = loader.available_symbols()
    config = load_config()

    # ── Config bar ─────────────────────────────────────────
    c1, c2, c3, c4, c5 = st.columns([2, 2, 1.5, 1.5, 1.5])
    with c1:
        strategy_name = st.selectbox("Strategy", list(STRATEGY_MAP.keys()),
                                     key="ch_strat", label_visibility="collapsed")
    with c2:
        symbol = st.selectbox("Symbol", symbols, key="ch_sym", label_visibility="collapsed")
    with c3:
        start_date = st.date_input("From", value=pd.Timestamp("2015-01-01"),
                                   key="ch_s", label_visibility="collapsed")
    with c4:
        end_date = st.date_input("To", value=pd.Timestamp("2024-12-31"),
                                 key="ch_e", label_visibility="collapsed")
    with c5:
        n_mc = st.number_input("MC Sims", value=1000, step=500, min_value=100,
                               label_visibility="collapsed", help="Monte Carlo simulations")

    # ── Challenge params ───────────────────────────────────
    with st.expander("Challenge Rules", expanded=False):
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            st.markdown("**Phase 1**")
            p1_target = st.number_input("Profit Target %", value=8.0, key="p1t") / 100
            p1_daily = st.number_input("Daily DD Limit %", value=5.0, key="p1d") / 100
            p1_total = st.number_input("Total DD Limit %", value=10.0, key="p1dd") / 100
            p1_days = st.number_input("Min Trading Days", value=4, key="p1td")
            p1_cal = st.number_input("Max Calendar Days", value=30, key="p1cd")
        with col_b:
            st.markdown("**Phase 2**")
            p2_target = st.number_input("Profit Target %", value=5.0, key="p2t") / 100
            p2_daily = st.number_input("Daily DD Limit %", value=5.0, key="p2d") / 100
            p2_total = st.number_input("Total DD Limit %", value=10.0, key="p2dd") / 100
            p2_days = st.number_input("Min Trading Days", value=4, key="p2td")
            p2_cal = st.number_input("Max Calendar Days", value=60, key="p2cd")
        with col_c:
            st.markdown("**Sequential Sim**")
            fee_per_attempt = st.number_input("Fee per Attempt ($)", value=500, key="ch_fee", step=50)
            cooldown = st.number_input("Cooldown Days", value=1, key="ch_cool", step=1)

    # ── Run ────────────────────────────────────────────────
    if st.button("Run Challenge Simulation", type="primary", use_container_width=True):
        phases = [
            ChallengePhaseConfig(name="Phase 1", profit_target=p1_target,
                                 daily_dd_limit=p1_daily, max_dd_limit=p1_total,
                                 min_trading_days=p1_days, max_calendar_days=p1_cal),
            ChallengePhaseConfig(name="Phase 2", profit_target=p2_target,
                                 daily_dd_limit=p2_daily, max_dd_limit=p2_total,
                                 min_trading_days=p2_days, max_calendar_days=p2_cal),
        ]

        with st.spinner(""):
            instrument = load_instrument_config(symbol)
            data = loader.load(symbol, start=str(start_date), end=str(end_date))
            strategy = _load_strategy(strategy_name)
            engine = BacktestEngine(config, instrument)
            result = engine.run(strategy, data, symbol)

        st.session_state["ch_result"] = result
        st.session_state["ch_phases"] = phases
        st.session_state["ch_nmc"] = n_mc
        st.session_state["ch_fee"] = fee_per_attempt
        st.session_state["ch_cool"] = cooldown

    # ── Results ────────────────────────────────────────────
    if "ch_result" not in st.session_state:
        return

    result = st.session_state["ch_result"]
    phases = st.session_state["ch_phases"]
    n_mc = st.session_state["ch_nmc"]
    fee = st.session_state.get("ch_fee", 500)
    cooldown = st.session_state.get("ch_cool", 1)

    st.markdown("---")

    # Equity + DD side by side
    c_eq, c_dd = st.columns([2, 1])
    with c_eq:
        st.plotly_chart(equity_curve_chart(result), use_container_width=True)
    with c_dd:
        st.plotly_chart(drawdown_chart(result), use_container_width=True)

    # ── Challenge Evaluation (full period) ────────────────
    simulator = PropFirmSimulator(phases)
    challenge = simulator.evaluate(result)

    section_title("Full Period Evaluation")

    cols = st.columns(len(challenge.phases))
    for i, pr in enumerate(challenge.phases):
        with cols[i]:
            status_text = f"{pr.phase_name}: PASS" if pr.passed else f"{pr.phase_name}: FAIL"
            phase_badge(pr.passed, status_text)

            st.markdown(f"<p style='font-size:0.8rem; color:{COLORS['text_muted']}; margin-top:8px;'>{pr.reason}</p>",
                        unsafe_allow_html=True)

            kpi_row([
                ("Profit", f"{pr.profit_pct:+.2f}%",
                 "positive" if pr.profit_pct >= phases[i].profit_target * 100 else "negative"),
                ("Daily DD", f"{pr.max_daily_dd_pct:.2f}%",
                 "negative" if pr.max_daily_dd_pct >= phases[i].daily_dd_limit * 100 else ""),
            ])
            kpi_row([
                ("Total DD", f"{pr.max_overall_dd_pct:.2f}%",
                 "negative" if pr.max_overall_dd_pct >= phases[i].max_dd_limit * 100 else ""),
                ("Trading Days", str(pr.trading_days), ""),
            ])
            if pr.days_to_target:
                st.caption(f"Target reached in {pr.days_to_target} days")

    # ── Sequential Challenge Simulation ───────────────────
    section_title("Sequential Challenge Simulation")
    st.caption(
        "Simulates real prop firm flow: start Phase 1 with $100K, FAIL → pay fee → restart, "
        "PASS → advance to Phase 2, PASS both → FUNDED."
    )

    with st.spinner(""):
        seq = simulator.sequential_challenge(result, fee_per_attempt=float(fee), cooldown_days=cooldown)

    # Funded status
    if seq.funded:
        phase_badge(True, f"FUNDED on {seq.funded_date}")
    else:
        phase_badge(False, "NOT FUNDED — ran out of data")

    # Summary KPIs
    kpi_row([
        ("P1 Attempts", str(seq.total_phase1_attempts),
         "positive" if seq.phase1_pass_rate > 0 else "negative"),
        ("P1 Pass Rate", f"{seq.phase1_pass_rate:.0f}%",
         "positive" if seq.phase1_pass_rate > 0 else "negative"),
        ("P2 Attempts", str(seq.total_phase2_attempts),
         "positive" if seq.phase2_pass_rate > 0 else "negative"),
        ("P2 Pass Rate", f"{seq.phase2_pass_rate:.0f}%",
         "positive" if seq.phase2_pass_rate > 0 else "negative"),
    ])
    kpi_row([
        ("Total Fees", f"${seq.total_fees:,.0f}", "negative" if seq.total_fees > 0 else ""),
        ("Total Attempts", str(seq.total_attempts), ""),
        ("Days to Funded", str(seq.total_calendar_days) if seq.total_calendar_days else "—", ""),
    ])

    # Timeline chart
    if seq.attempts:
        st.plotly_chart(
            sequential_challenge_chart(seq.attempts, "Challenge Attempts Timeline"),
            use_container_width=True,
        )

        # Equity overlay of attempts (show up to 30 to avoid clutter)
        display_attempts = seq.attempts[:30]
        st.plotly_chart(
            sequential_equity_chart(display_attempts, result.initial_balance,
                                    f"Attempt Equity Curves (showing {len(display_attempts)} of {len(seq.attempts)})"),
            use_container_width=True,
        )

    # Attempt log
    if seq.attempts:
        with st.expander(f"Attempt Log ({len(seq.attempts)} attempts)", expanded=False):
            log_data = []
            for a in seq.attempts:
                log_data.append({
                    "Phase": a.phase_name,
                    "#": a.attempt_number,
                    "Start": str(a.start_date),
                    "End": str(a.end_date),
                    "Days": a.calendar_days,
                    "Outcome": a.outcome,
                    "Profit %": f"{a.profit_pct:+.2f}",
                    "Max DD %": f"{a.max_overall_dd_pct:.2f}",
                    "Daily DD %": f"{a.max_daily_dd_pct:.2f}",
                    "Trades": a.trades_in_attempt,
                })
            st.dataframe(pd.DataFrame(log_data), use_container_width=True, height=300)

    # ── Rolling Window Analysis ────────────────────────────
    section_title("Rolling Window Analysis")

    for phase_cfg in phases:
        with st.spinner(""):
            rolling = simulator.rolling_simulation(
                result, phase_cfg,
                window_days=phase_cfg.max_calendar_days,
                step_days=5,
            )

        if not rolling:
            continue

        pass_count = sum(1 for r in rolling if r.passed)
        total = len(rolling)
        pass_rate = pass_count / total * 100 if total > 0 else 0
        profits = [r.profit_pct for r in rolling]

        rc1, rc2 = st.columns([1, 2])
        with rc1:
            kpi_row([
                (f"{phase_cfg.name} Pass Rate", f"{pass_rate:.1f}%",
                 "positive" if pass_rate >= 50 else "negative"),
            ])
            st.caption(f"{pass_count} / {total} windows")
        with rc2:
            st.plotly_chart(
                profit_distribution_chart(profits, phase_cfg.profit_target * 100,
                                          f"{phase_cfg.name} — Rolling Profit Distribution"),
                use_container_width=True,
            )

    # ── Monte Carlo ────────────────────────────────────────
    section_title("Monte Carlo Simulation")

    for phase_cfg in phases:
        with st.spinner(""):
            mc = simulator.monte_carlo(result, phase_cfg, n_mc)

        st.markdown(f"**{phase_cfg.name}**")

        pass_rate = mc["pass_rate"]
        pass_class = "positive" if pass_rate >= 50 else ("negative" if pass_rate < 30 else "")

        kpi_row([
            ("Pass Rate", f"{pass_rate:.1f}%", pass_class),
            ("Avg Profit", f"{mc['avg_profit']:+.2f}%",
             "positive" if mc["avg_profit"] > 0 else "negative"),
            ("Median Profit", f"{mc['median_profit']:+.2f}%",
             "positive" if mc["median_profit"] > 0 else "negative"),
            ("Avg Max DD", f"{mc['avg_max_dd']:.2f}%", "negative"),
        ])

        if "fail_breakdown" in mc and mc["fail_breakdown"]:
            fb = mc["fail_breakdown"]
            st.caption(
                f"P5-P95 Profit: {mc['p5_profit']:.2f}% to {mc['p95_profit']:.2f}%  |  "
                f"Fail reasons — Profit: {fb.get('profit', 0):.1f}%, "
                f"Daily DD: {fb.get('daily_dd', 0):.1f}%, "
                f"Total DD: {fb.get('total_dd', 0):.1f}%"
            )
