"""
Reusable Plotly chart components — styled for the AlgoSbz dark terminal theme.
"""
import plotly.graph_objects as go
import pandas as pd
import numpy as np

from algosbz.backtest.results import BacktestResult
from algosbz.dashboard.theme import COLORS


def equity_curve_chart(result: BacktestResult, title: str = "Equity Curve") -> go.Figure:
    eq = result.equity_curve
    fig = go.Figure()

    # Gradient fill area (beneath line)
    fig.add_trace(go.Scatter(
        x=eq.index, y=eq.values,
        fill="tozeroy",
        mode="none",
        fillcolor="rgba(0, 212, 170, 0.06)",
        showlegend=False,
        hoverinfo="skip",
    ))

    # Main equity line
    fig.add_trace(go.Scatter(
        x=eq.index, y=eq.values,
        mode="lines",
        name="Equity",
        line=dict(color=COLORS["accent"], width=2),
        hovertemplate="$%{y:,.0f}<extra></extra>",
    ))

    # Initial balance reference
    fig.add_hline(
        y=result.initial_balance,
        line_dash="dot",
        line_color=COLORS["text_muted"],
        line_width=1,
        annotation_text=f"Initial: ${result.initial_balance:,.0f}",
        annotation_font=dict(size=10, color=COLORS["text_muted"]),
    )

    fig.update_layout(title=title, height=380,
                      yaxis_title="", xaxis_title="",
                      yaxis_tickprefix="$", yaxis_tickformat=",.0f")
    return fig


def drawdown_chart(result: BacktestResult, title: str = "Drawdown") -> go.Figure:
    dd = result.drawdown_series()
    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=dd.index, y=dd.values,
        fill="tozeroy",
        mode="lines",
        name="Drawdown",
        line=dict(color=COLORS["negative"], width=1.5),
        fillcolor="rgba(255, 71, 87, 0.12)",
        hovertemplate="%{y:.2f}%<extra></extra>",
    ))

    fig.update_layout(title=title, height=240,
                      yaxis_title="", xaxis_title="",
                      yaxis_ticksuffix="%")
    return fig


def trades_on_price_chart(
    result: BacktestResult,
    price_data: pd.DataFrame,
    title: str = "Trades on Price",
) -> go.Figure:
    fig = go.Figure()

    df = price_data
    if len(df) > 5000:
        df = df.iloc[::max(1, len(df) // 5000)]

    # Candlesticks
    fig.add_trace(go.Candlestick(
        x=df.index,
        open=df["open"], high=df["high"],
        low=df["low"], close=df["close"],
        name="Price",
        increasing=dict(line=dict(color=COLORS["green_candle"], width=1),
                        fillcolor=COLORS["green_candle"]),
        decreasing=dict(line=dict(color=COLORS["red_candle"], width=1),
                        fillcolor=COLORS["red_candle"]),
    ))

    # Entries
    longs = [t for t in result.trades if t.direction.name == "LONG"]
    shorts = [t for t in result.trades if t.direction.name == "SHORT"]

    if longs:
        fig.add_trace(go.Scatter(
            x=[t.entry_time for t in longs],
            y=[t.entry_price for t in longs],
            mode="markers",
            marker=dict(symbol="triangle-up", size=9, color=COLORS["accent"],
                        line=dict(width=1, color=COLORS["accent_dim"])),
            name="Long Entry",
            hovertemplate="Long @ %{y:.5f}<extra></extra>",
        ))
    if shorts:
        fig.add_trace(go.Scatter(
            x=[t.entry_time for t in shorts],
            y=[t.entry_price for t in shorts],
            mode="markers",
            marker=dict(symbol="triangle-down", size=9, color=COLORS["negative"],
                        line=dict(width=1, color=COLORS["negative_dim"])),
            name="Short Entry",
            hovertemplate="Short @ %{y:.5f}<extra></extra>",
        ))

    # Exits
    wins = [t for t in result.trades if t.pnl >= 0]
    losses = [t for t in result.trades if t.pnl < 0]

    if wins:
        fig.add_trace(go.Scatter(
            x=[t.exit_time for t in wins],
            y=[t.exit_price for t in wins],
            mode="markers",
            marker=dict(symbol="circle", size=6, color=COLORS["accent"],
                        line=dict(width=1, color=COLORS["accent"])),
            name="Win Exit",
            hovertemplate="Exit (win) @ %{y:.5f}<extra></extra>",
        ))
    if losses:
        fig.add_trace(go.Scatter(
            x=[t.exit_time for t in losses],
            y=[t.exit_price for t in losses],
            mode="markers",
            marker=dict(symbol="circle", size=6, color=COLORS["negative"],
                        line=dict(width=1, color=COLORS["negative"])),
            name="Loss Exit",
            hovertemplate="Exit (loss) @ %{y:.5f}<extra></extra>",
        ))

    fig.update_layout(title=title, height=500,
                      xaxis_rangeslider_visible=False,
                      yaxis_title="", xaxis_title="")
    return fig


def comparison_equity_chart(
    results: dict[str, BacktestResult],
    title: str = "Strategy Comparison",
) -> go.Figure:
    fig = go.Figure()

    for i, (name, result) in enumerate(results.items()):
        if result.equity_curve.empty:
            continue
        color = [COLORS["blue"], COLORS["accent"], COLORS["orange"],
                 COLORS["purple"], COLORS["cyan"], COLORS["negative"]][i % 6]
        fig.add_trace(go.Scatter(
            x=result.equity_curve.index,
            y=result.equity_curve.values,
            mode="lines",
            name=name,
            line=dict(color=color, width=2),
            hovertemplate=f"{name}: $%{{y:,.0f}}<extra></extra>",
        ))

    initial = list(results.values())[0].initial_balance if results else 100000
    fig.add_hline(y=initial, line_dash="dot", line_color=COLORS["text_muted"], line_width=1)

    fig.update_layout(title=title, height=420,
                      yaxis_title="", xaxis_title="",
                      yaxis_tickprefix="$", yaxis_tickformat=",.0f")
    return fig


def sequential_challenge_chart(attempts, title: str = "Sequential Challenge Timeline") -> go.Figure:
    """
    Gantt-style timeline of sequential challenge attempts.
    Each bar = one attempt, colored by outcome, grouped by phase.
    """
    from algosbz.risk.prop_firm import ChallengeAttempt

    fig = go.Figure()

    outcome_colors = {
        "PASS": COLORS["accent"],
        "FAIL_DAILY_DD": COLORS["negative"],
        "FAIL_TOTAL_DD": "#cc3a47",
        "FAIL_TIME": COLORS["orange"],
        "INCOMPLETE": COLORS["text_muted"],
    }
    outcome_labels = {
        "PASS": "Pass",
        "FAIL_DAILY_DD": "Fail: Daily DD",
        "FAIL_TOTAL_DD": "Fail: Total DD",
        "FAIL_TIME": "Fail: Time Limit",
        "INCOMPLETE": "Incomplete",
    }

    # Group by outcome for legend
    shown_outcomes = set()
    for a in attempts:
        color = outcome_colors.get(a.outcome, COLORS["text_muted"])
        label = outcome_labels.get(a.outcome, a.outcome)
        show_legend = a.outcome not in shown_outcomes
        shown_outcomes.add(a.outcome)

        y_label = f"{a.phase_name}"
        hover = (
            f"{a.phase_name} #{a.attempt_number}<br>"
            f"Outcome: {label}<br>"
            f"Profit: {a.profit_pct:+.2f}%<br>"
            f"Max DD: {a.max_overall_dd_pct:.2f}%<br>"
            f"Daily DD: {a.max_daily_dd_pct:.2f}%<br>"
            f"Trades: {a.trades_in_attempt}<br>"
            f"{a.start_date} → {a.end_date}"
        )

        fig.add_trace(go.Bar(
            x=[(pd.Timestamp(a.end_date) - pd.Timestamp(a.start_date)).days or 1],
            y=[y_label],
            base=[pd.Timestamp(a.start_date)],
            orientation="h",
            marker=dict(color=color, line=dict(width=0.5, color=COLORS["border"])),
            name=label,
            showlegend=show_legend,
            legendgroup=a.outcome,
            hovertext=hover,
            hoverinfo="text",
        ))

    fig.update_layout(
        title=title,
        height=220,
        barmode="stack",
        xaxis=dict(type="date", title=""),
        yaxis=dict(title="", autorange="reversed"),
        bargap=0.3,
    )
    return fig


def sequential_equity_chart(attempts, initial_balance: float,
                            title: str = "Challenge Equity Curves") -> go.Figure:
    """Plot equity curves of each challenge attempt overlaid, colored by outcome."""
    from algosbz.risk.prop_firm import ChallengeAttempt

    fig = go.Figure()

    outcome_colors = {
        "PASS": COLORS["accent"],
        "FAIL_DAILY_DD": COLORS["negative"],
        "FAIL_TOTAL_DD": "#cc3a47",
        "FAIL_TIME": COLORS["orange"],
        "INCOMPLETE": COLORS["text_muted"],
    }

    for a in attempts:
        if a.equity_curve.empty:
            continue
        color = outcome_colors.get(a.outcome, COLORS["text_muted"])
        # Normalize x-axis to days from start for overlay
        days = [(t - a.equity_curve.index[0]).total_seconds() / 86400
                for t in a.equity_curve.index]

        fig.add_trace(go.Scatter(
            x=days,
            y=a.equity_curve.values,
            mode="lines",
            line=dict(color=color, width=1.2),
            opacity=0.5 if a.outcome != "PASS" else 0.9,
            showlegend=False,
            hovertemplate=f"{a.phase_name} #{a.attempt_number}<br>"
                          f"Day %{{x:.0f}}: $%{{y:,.0f}}<extra></extra>",
        ))

    # Target and initial lines
    fig.add_hline(y=initial_balance, line_dash="dot",
                  line_color=COLORS["text_muted"], line_width=1)
    fig.add_hline(y=initial_balance * 1.08, line_dash="dash",
                  line_color=COLORS["accent"], line_width=1,
                  annotation_text="P1 Target (8%)",
                  annotation_font=dict(size=10, color=COLORS["accent"]))
    fig.add_hline(y=initial_balance * 0.90, line_dash="dash",
                  line_color=COLORS["negative"], line_width=1,
                  annotation_text="Max DD (10%)",
                  annotation_font=dict(size=10, color=COLORS["negative"]))

    fig.update_layout(
        title=title,
        height=350,
        xaxis_title="Days from Attempt Start",
        yaxis_title="",
        yaxis_tickprefix="$",
        yaxis_tickformat=",.0f",
    )
    return fig


def profit_distribution_chart(
    profits: list[float],
    target_pct: float,
    title: str = "Profit Distribution",
) -> go.Figure:
    fig = go.Figure()

    # Color bars by whether they meet target
    fig.add_trace(go.Histogram(
        x=profits,
        nbinsx=35,
        marker=dict(
            color=COLORS["blue"],
            line=dict(width=0.5, color=COLORS["border_accent"]),
        ),
        opacity=0.85,
        name="Simulations",
        hovertemplate="%{x:.1f}%<extra></extra>",
    ))

    fig.add_vline(
        x=target_pct,
        line_dash="dash",
        line_color=COLORS["accent"],
        line_width=2,
        annotation_text=f"Target: {target_pct:.0f}%",
        annotation_font=dict(size=11, color=COLORS["accent"]),
    )

    fig.update_layout(title=title, height=280,
                      xaxis_title="Profit (%)", yaxis_title="",
                      bargap=0.05)
    return fig
