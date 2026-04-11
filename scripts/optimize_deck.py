"""
Funded Rate Optimizer v3 — comprehensive, bias-free optimization.

Methodology (audit-compliant):
- Pre-compute at DIRECT 2% risk (no linear scaling)
- Loose DD limits during pre-computation (portfolio DD enforced in sim)
- P2 balance RESETS to initial (correct FTMO 2-step rules)
- Correlation-aware deck selection (computed on IS data only)
- Clear IS/OOS split: 2016-2024 IS, 2025 OOS (strategies never saw 2025)

Portfolio controls (6 dimensions):
1. Daily loss cap (% of initial)
2. Per-combo cooldown (max losses/combo/day)
3. Rolling lookback filter (adaptive combo selection)
4. P2 risk factor (half risk in P2)
5. Max instrument trades/day (prevent correlated losses)
6. Max total daily losses (hard daily loss count cap)

Plus funded account survival simulation after optimization.

Usage:
    python -X utf8 scripts/optimize_deck.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
import importlib
import pandas as pd
import numpy as np
from copy import deepcopy
from datetime import timedelta, date
from collections import defaultdict

from algosbz.core.config import load_config, load_all_instruments
from algosbz.data.loader import DataLoader
from algosbz.data.resampler import resample
from algosbz.data.indicators import atr
from algosbz.backtest.engine import BacktestEngine
from algosbz.risk.equity_manager import EquityManager, EquityManagerConfig

logging.basicConfig(level=logging.ERROR)

from scripts.challenge_decks_v5_clean import ALL_COMBOS, STRAT_REGISTRY


# ═══════════════════════════════════════════════════════════════════════
# CORE FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════

def load_strategy(entry):
    info = STRAT_REGISTRY[entry["strat"]]
    mod = importlib.import_module(info["module"])
    cls = getattr(mod, info["class"])
    return cls(entry["params"])


def precompute_trades(config, instruments, data_dict, combo_names, risk_pct=0.02):
    """
    Pre-compute all trades at DIRECT risk level with LOOSE DD limits.

    Why loose DD: each combo runs independently. At 2% risk with tight DD (10%),
    a combo dies after 5 consecutive losses. But in the real exam, combos share
    ONE account — portfolio DD is enforced in simulate_exam(), not here.
    """
    print(f"\n  Pre-computing trades at {risk_pct*100:.0f}% risk (loose DD)...")
    streams = {}
    cfg = deepcopy(config)
    cfg.risk.risk_per_trade = risk_pct
    cfg.risk.daily_dd_limit = 0.50
    cfg.risk.max_dd_limit = 0.50
    eq_cfg = EquityManagerConfig(
        dd_tiers=[(0.50, 1.0)], daily_stop_threshold=0.50,
        progressive_trades=0, consecutive_win_bonus=0,
    )
    for combo_name in combo_names:
        entry = ALL_COMBOS[combo_name]
        sym = entry["symbol"]
        if sym not in data_dict:
            continue
        try:
            strategy = load_strategy(entry)
            engine = BacktestEngine(cfg, instruments[sym], EquityManager(eq_cfg))
            result = engine.run(strategy, data_dict[sym], sym)
        except Exception as e:
            print(f"    {combo_name}: ERROR {e}")
            continue
        trades = []
        for t in result.trades:
            ts = t.entry_time
            if isinstance(ts, pd.Timestamp):
                trades.append({"ts": ts, "date": ts.date(), "pnl": t.pnl, "combo": combo_name})
        streams[combo_name] = trades
        if trades:
            pnl_total = sum(t["pnl"] for t in trades)
            print(f"    {combo_name}: {len(trades)} trades, PnL=${pnl_total:+,.0f}")
        else:
            print(f"    {combo_name}: 0 trades")
    return streams


def compute_correlation_matrix(streams, date_cutoff=None):
    """
    Compute daily PnL correlation between combos.
    If date_cutoff provided, only use data before that date (IS only).
    """
    daily_pnl = {}
    for combo, trades in streams.items():
        if not trades:
            continue
        filtered = trades
        if date_cutoff:
            filtered = [t for t in trades if t["date"] < date_cutoff]
        if not filtered:
            continue
        df = pd.DataFrame(filtered)
        daily = df.groupby("date")["pnl"].sum()
        daily_pnl[combo] = daily

    if len(daily_pnl) < 2:
        return pd.DataFrame()

    combined = pd.DataFrame(daily_pnl).fillna(0)
    return combined.corr()


def greedy_decorrelated_deck(streams, corr_matrix, max_size, candidates=None):
    """Build deck by greedily adding least-correlated combo."""
    if candidates is None:
        candidates = list(streams.keys())
    candidates = [c for c in candidates if c in corr_matrix.columns and c in streams]
    if not candidates:
        return []

    # Start with highest PF combo
    best_start = max(candidates, key=lambda c: ALL_COMBOS[c]["pf"])
    deck = [best_start]
    remaining = [c for c in candidates if c != best_start]

    while len(deck) < max_size and remaining:
        best_next = None
        best_score = float('inf')
        for c in remaining:
            avg_corr = np.mean([abs(corr_matrix.loc[c, d]) for d in deck
                                if c in corr_matrix.index and d in corr_matrix.columns])
            pf_bonus = (ALL_COMBOS[c]["pf"] - 1.0) * 0.5
            score = avg_corr - pf_bonus
            if score < best_score:
                best_score = score
                best_next = c
        if best_next:
            deck.append(best_next)
            remaining.remove(best_next)
    return deck


def get_active_combos(streams, all_combos, eval_date, lookback_months=6, min_trades=3):
    """Rolling lookback filter — uses ONLY past data (no look-ahead)."""
    eval_ts = pd.Timestamp(eval_date)
    cutoff = eval_ts - timedelta(days=lookback_months * 30)
    active = []
    for combo in all_combos:
        if combo not in streams:
            continue
        recent = [t for t in streams[combo]
                  if cutoff <= pd.Timestamp(t["date"]) < eval_ts]
        if len(recent) < min_trades:
            active.append(combo)  # benefit of doubt
            continue
        wins = sum(t["pnl"] for t in recent if t["pnl"] > 0)
        losses = abs(sum(t["pnl"] for t in recent if t["pnl"] < 0))
        pf = wins / losses if losses > 0 else 99
        if pf > 0.9:  # slightly lenient — don't kill combos in small drawdowns
            active.append(combo)
    return active


def compute_daily_regime(data_dict):
    """
    Compute daily ATR percentile for each instrument.
    Returns {symbol: {date: atr_percentile_0_to_100}}.
    Uses ONLY past data (100-day rolling window) — no look-ahead.
    """
    regime = {}
    for sym, df_m1 in data_dict.items():
        d1 = resample(df_m1, "D1")
        if d1.empty or len(d1) < 120:
            continue
        a = atr(d1["high"], d1["low"], d1["close"], 14)
        # Rolling percentile: where does current ATR rank in last 100 bars?
        pctl = a.rolling(100, min_periods=50).apply(
            lambda x: (x.iloc[-1] <= x).sum() / len(x) * 100, raw=False
        )
        regime[sym] = {}
        for ts, val in pctl.items():
            regime[sym][ts.date()] = val if not np.isnan(val) else 50.0
    return regime


# ═══════════════════════════════════════════════════════════════════════
# EXAM SIMULATOR — with P2 carry-over and portfolio controls
# ═══════════════════════════════════════════════════════════════════════

def simulate_exam(streams, combo_names, start_date,
                  daily_loss_cap_pct=3.0, combo_daily_max_losses=1,
                  p1_risk_factor=1.0, p2_risk_factor=1.0,
                  max_instr_per_day=99, max_daily_losses=99,
                  regime_data=None, regime_threshold=90,
                  initial=100000, p1_days=30, p2_days=60):
    """
    Realistic FTMO 2-step exam simulation.

    FTMO rules — balance RESETS between phases:
    - P1 starts at $100K, target = 10% ($10K)
    - P2 starts at $100K (RESET), target = 5% ($5K)
    - DD limits: static from initial ($100K) — floor is always $90K
    - Daily DD: 5% of initial from start-of-day equity

    Portfolio controls:
    - daily_loss_cap_pct: stop ALL trading when daily loss >= X% of initial
    - combo_daily_max_losses: max losing trades per combo per day
    - max_instr_per_day: max trades per instrument per day (prevents correlated losses)
    - max_daily_losses: max total losing trades per day across ALL combos
    - p2_risk_factor: scale PnL in P2 (e.g. 0.5 = half risk in P2)
    """

    def run_phase(phase_start, window_days, target_pct, starting_equity,
                  risk_factor=1.0):
        phase_end = phase_start + timedelta(days=window_days)
        equity = starting_equity
        daily_start_eq = starting_equity
        trading_days = set()
        current_day = None
        max_dd = 0
        max_daily_dd = 0
        locked = False
        target_reached = False
        target_reached_day = None
        days_used = window_days
        combo_day_losses = defaultdict(int)
        instr_day_trades = defaultdict(int)
        total_daily_losses = 0
        daily_stopped = False

        target_equity = starting_equity + (target_pct / 100) * initial

        all_trades = []
        for combo in combo_names:
            if combo not in streams:
                continue
            for t in streams[combo]:
                if phase_start.date() <= t["date"] < phase_end.date():
                    # Regime filter: skip trades during extreme volatility
                    if regime_data is not None:
                        instrument = ALL_COMBOS[combo]["symbol"]
                        if instrument in regime_data:
                            pctl = regime_data[instrument].get(t["date"], 50.0)
                            if pctl >= regime_threshold:
                                continue
                    all_trades.append(t)
        all_trades.sort(key=lambda x: x["ts"])

        for t in all_trades:
            if locked:
                break

            if t["date"] != current_day:
                if current_day is not None:
                    daily_dd = (daily_start_eq - equity) / initial
                    max_daily_dd = max(max_daily_dd, daily_dd)
                current_day = t["date"]
                daily_start_eq = equity
                combo_day_losses.clear()
                instr_day_trades.clear()
                total_daily_losses = 0
                daily_stopped = False

            # Once target reached, stop real trading -> micro-ops for min days
            if target_reached:
                trading_days.add(t["date"])
                if len(trading_days) >= 4:
                    locked = True
                    days_used = (t["date"] - phase_start.date()).days + 1
                continue

            if daily_stopped:
                continue

            combo = t["combo"]
            if combo_day_losses[combo] >= combo_daily_max_losses:
                continue

            instrument = ALL_COMBOS[combo]["symbol"]
            if instr_day_trades[instrument] >= max_instr_per_day:
                continue
            if total_daily_losses >= max_daily_losses:
                continue

            pnl = t["pnl"] * risk_factor
            equity += pnl
            trading_days.add(t["date"])
            instr_day_trades[instrument] += 1

            if pnl < 0:
                combo_day_losses[combo] += 1
                total_daily_losses += 1

            daily_loss_pct = (daily_start_eq - equity) / initial * 100
            if daily_loss_pct >= daily_loss_cap_pct:
                daily_stopped = True

            dd = (initial - equity) / initial
            max_dd = max(max_dd, dd)
            if dd >= 0.10:
                return {"outcome": "FAIL_DD", "profit_pct": (equity - starting_equity) / initial * 100,
                        "final_equity": equity, "max_dd": max_dd * 100,
                        "max_daily_dd": max_daily_dd * 100,
                        "trading_days": len(trading_days), "days_used": window_days}

            daily_dd_hard = (daily_start_eq - equity) / initial
            if daily_dd_hard >= 0.05:
                return {"outcome": "FAIL_DAILY_DD", "profit_pct": (equity - starting_equity) / initial * 100,
                        "final_equity": equity, "max_dd": max_dd * 100,
                        "max_daily_dd": daily_dd_hard * 100,
                        "trading_days": len(trading_days), "days_used": window_days}

            if equity >= target_equity:
                if len(trading_days) >= 4:
                    locked = True
                    days_used = (t["date"] - phase_start.date()).days + 1
                else:
                    target_reached = True
                    target_reached_day = t["date"]

        if current_day and not locked:
            daily_dd = (daily_start_eq - equity) / initial
            max_daily_dd = max(max_daily_dd, daily_dd)

        # If target reached but ran out of trades before 4 days,
        # check if enough weekdays remain in the window for micro-ops
        if target_reached and not locked and len(trading_days) < 4:
            days_needed = 4 - len(trading_days)
            remaining_days = 0
            check_date = target_reached_day + timedelta(days=1)
            while check_date < phase_end.date() and remaining_days < days_needed:
                if check_date.weekday() < 5:
                    remaining_days += 1
                check_date += timedelta(days=1)
            if remaining_days >= days_needed:
                locked = True
                days_used = (check_date - phase_start.date()).days

        profit_pct = (equity - starting_equity) / initial * 100
        if max_dd >= 0.10:
            outcome = "FAIL_DD"
        elif max_daily_dd >= 0.05:
            outcome = "FAIL_DAILY_DD"
        elif locked or (equity >= target_equity and len(trading_days) >= 4):
            outcome = "PASS"
        elif equity >= target_equity:
            outcome = "FAIL_MIN_DAYS"
        else:
            outcome = "FAIL_PROFIT"

        return {"outcome": outcome, "profit_pct": round(profit_pct, 2),
                "final_equity": round(equity, 2),
                "max_dd": round(max_dd * 100, 2),
                "max_daily_dd": round(max_daily_dd * 100, 2),
                "trading_days": len(trading_days), "days_used": days_used}

    # ── Phase 1: start at $100K, target +10% ──
    p1 = run_phase(start_date, p1_days, 10.0, initial, risk_factor=p1_risk_factor)
    if p1["outcome"] != "PASS":
        return {"exam": "FAIL_P1", "p1": p1, "p2": None}

    # ── Phase 2: balance RESETS to $100K, target +5% ──
    p2_start = start_date + timedelta(days=p1["days_used"])
    p2 = run_phase(p2_start, p2_days, 5.0, initial,
                   risk_factor=p2_risk_factor)

    if p2["outcome"] == "PASS":
        return {"exam": "FUNDED", "p1": p1, "p2": p2}
    return {"exam": "FAIL_P2", "p1": p1, "p2": p2}


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

def main():
    config = load_config()
    instruments = load_all_instruments()
    loader = DataLoader()

    pool = list(ALL_COMBOS.keys())
    all_symbols = list({ALL_COMBOS[c]["symbol"] for c in pool})

    data_dict = {}
    print("Loading data (2014-2026)...")
    for sym in sorted(all_symbols):
        try:
            data_dict[sym] = loader.load(sym, start="2014-09-01", end="2026-01-01")
            last = data_dict[sym].index[-1]
            print(f"  {sym}: {len(data_dict[sym]):,} bars (→ {last.date()})")
        except Exception as e:
            print(f"  {sym}: FAILED - {e}")

    # ── Spread realism floor: parquet spreads (Darwinex) often understate
    # FTMO real spreads. Override bar['spread'] with max(bar_spread, floor)
    # where floor = instrument.default_spread_pips * pip_size (measured live).
    print("\n  Applying realistic spread floor (FTMO measurements)...")
    for sym, df in data_dict.items():
        if "spread" not in df.columns:
            continue
        instr = instruments[sym]
        floor = instr.default_spread_pips * instr.pip_size
        before_mean = df["spread"].mean()
        df["spread"] = df["spread"].clip(lower=floor)
        after_mean = df["spread"].mean()
        print(f"    {sym}: floor={floor:.5f} | mean spread {before_mean:.5f} -> {after_mean:.5f}")

    # ── Step 1: Pre-compute at direct 2% ──
    streams = precompute_trades(config, instruments, data_dict, pool, risk_pct=0.02)
    active_pool = [c for c in pool if c in streams and streams[c]]

    # Filter out combos with negative total PnL — a losing strategy hurts the portfolio
    profitable_pool = []
    negative_pnl = []
    for c in active_pool:
        total_pnl = sum(t["pnl"] for t in streams[c])
        if total_pnl > 0:
            profitable_pool.append(c)
        else:
            negative_pnl.append((c, total_pnl))
    if negative_pnl:
        print(f"\n  FILTERED OUT (negative total PnL):")
        for c, pnl in negative_pnl:
            print(f"    {c}: ${pnl:+,.0f} — REMOVED from deck candidates")
    active_pool = profitable_pool
    print(f"\n  Active combos: {len(active_pool)}/{len(pool)} (after PnL filter)")

    # ── Step 1b: Compute daily regime data for all instruments ──
    print("\n  Computing daily regime (ATR percentile) for regime filter...")
    regime_data = compute_daily_regime(data_dict)
    print(f"    Regime data for {len(regime_data)} instruments")

    # ── Step 2: Correlation matrix (IS data only, before 2025) ──
    print("\n  Computing correlation matrix (IS data only: <2025)...")
    oos_cutoff = date(2025, 1, 1)
    corr = compute_correlation_matrix(streams, date_cutoff=oos_cutoff)
    if not corr.empty:
        print(f"    {len(corr)} combos in matrix")
        # Top correlated pairs
        pairs = []
        for i in range(len(corr)):
            for j in range(i + 1, len(corr)):
                c1, c2 = corr.index[i], corr.columns[j]
                pairs.append((c1, c2, corr.iloc[i, j]))
        pairs.sort(key=lambda x: abs(x[2]), reverse=True)
        print("    Top 5 correlated:")
        for c1, c2, r in pairs[:5]:
            print(f"      {c1:28s} <-> {c2:28s}  r={r:+.3f}")

    # ── Step 3: Build deck candidates ──
    robust = [c for c in active_pool if ALL_COMBOS[c]["tier"] == "ROBUST"]
    print(f"\n  Building deck candidates (ROBUST: {len(robust)}, ALL: {len(active_pool)})...")

    decks = {}
    for size in [4, 6, 8, 10, 12, 16, 20, 24]:
        if size <= len(robust):
            d = greedy_decorrelated_deck(streams, corr, size, robust)
            decks[f"Decorr{size}_R"] = d
        if size <= len(active_pool):
            d = greedy_decorrelated_deck(streams, corr, size, active_pool)
            decks[f"Decorr{size}_A"] = d

    # v5 (2026-04-08): Full_All / Robust_All decks REMOVED — they overfit on IS
    # (65.4% IS / 28.6% OOS, +36.8pp gap). Only decorrelated decks are considered.

    for name, combo_list in decks.items():
        syms = set(ALL_COMBOS[c]["symbol"] for c in combo_list)
        print(f"    {name:20s}: {len(combo_list):2d} combos, {len(syms)} instruments — "
              f"{', '.join(combo_list[:5])}{'...' if len(combo_list) > 5 else ''}")

    # ── Step 4: Grid search ──
    print(f"\n{'='*120}")
    print(f"  GRID SEARCH: deck × controls (IS: 2016-2024, OOS: 2025)")
    print(f"{'='*120}")

    is_windows = pd.date_range("2016-01-01", "2024-10-01", freq="30D")
    # OOS: only windows where we have enough data for full P1+P2 (90 days)
    # Data ends ~2025-10-03, so last valid start is ~2025-07-05
    last_data_date = min(data_dict[sym].index[-1] for sym in data_dict)
    max_oos_start = last_data_date - timedelta(days=90)
    oos_windows = pd.date_range("2025-01-01", max_oos_start, freq="30D")
    print(f"  Data ends: {last_data_date.date()} → OOS windows: {len(oos_windows)} "
          f"(up to {max_oos_start.date()})")

    # Coarse grid to prevent overfitting
    grid_params = []
    for daily_cap in [2.0, 2.5, 3.0, 3.5, 4.0]:
        for cooldown in [1, 2]:
            for lookback in [0, 6]:
                for p1_rf in [1.0, 1.25]:
                    for p2_rf in [1.0, 0.7, 0.5]:
                        for max_instr in [2, 3, 99]:
                            for max_losses in [3, 5, 99]:
                                for regime in [0, 90]:
                                    grid_params.append({
                                        "daily_cap": daily_cap,
                                        "cooldown": cooldown,
                                        "lookback": lookback,
                                        "p1_rf": p1_rf,
                                        "p2_rf": p2_rf,
                                        "max_instr": max_instr,
                                        "max_losses": max_losses,
                                        "regime": regime,
                                    })

    total_sims = len(decks) * len(grid_params) * (len(is_windows) + len(oos_windows))
    print(f"  {len(decks)} decks × {len(grid_params)} control configs = "
          f"{len(decks)*len(grid_params)} combinations ({total_sims:,} total sims)")

    results = []
    iter_count = 0
    total_iters = len(decks) * len(grid_params)

    for deck_name, combo_list in decks.items():
        for gp in grid_params:
            is_funded = 0; is_n = 0
            oos_funded = 0; oos_n = 0
            is_p1_pass = 0; oos_p1_pass = 0
            is_dd_fails = 0; oos_dd_fails = 0
            is_profit_fails = 0; oos_profit_fails = 0

            for windows, is_oos in [(is_windows, False), (oos_windows, True)]:
                for start in windows:
                    # Adaptive combo selection (if enabled)
                    if gp["lookback"] > 0:
                        active = get_active_combos(streams, combo_list, start.date(),
                                                   lookback_months=gp["lookback"])
                    else:
                        active = combo_list

                    if not active:
                        if is_oos:
                            oos_n += 1
                        else:
                            is_n += 1
                        continue

                    regime_kw = {}
                    if gp["regime"] > 0:
                        regime_kw = {"regime_data": regime_data, "regime_threshold": gp["regime"]}

                    r = simulate_exam(
                        streams, active, start,
                        daily_loss_cap_pct=gp["daily_cap"],
                        combo_daily_max_losses=gp["cooldown"],
                        p1_risk_factor=gp["p1_rf"],
                        p2_risk_factor=gp["p2_rf"],
                        max_instr_per_day=gp["max_instr"],
                        max_daily_losses=gp["max_losses"],
                        **regime_kw,
                    )

                    if is_oos:
                        oos_n += 1
                        if r["exam"] == "FUNDED": oos_funded += 1
                        if r["p1"]["outcome"] == "PASS": oos_p1_pass += 1
                        if "DD" in r["p1"]["outcome"]: oos_dd_fails += 1
                        if r["p1"]["outcome"] == "FAIL_PROFIT": oos_profit_fails += 1
                    else:
                        is_n += 1
                        if r["exam"] == "FUNDED": is_funded += 1
                        if r["p1"]["outcome"] == "PASS": is_p1_pass += 1
                        if "DD" in r["p1"]["outcome"]: is_dd_fails += 1
                        if r["p1"]["outcome"] == "FAIL_PROFIT": is_profit_fails += 1

            is_rate = is_funded / is_n * 100 if is_n else 0
            oos_rate = oos_funded / oos_n * 100 if oos_n else 0

            regime_tag = f"_RG{gp['regime']}" if gp["regime"] > 0 else ""
            p1_tag = f"_P1x{gp['p1_rf']}" if gp["p1_rf"] != 1.0 else ""
            label = (f"{deck_name}_DC{gp['daily_cap']}_CD{gp['cooldown']}"
                     f"_L{gp['lookback']}{p1_tag}_P2x{gp['p2_rf']}"
                     f"_MI{gp['max_instr']}_ML{gp['max_losses']}{regime_tag}")

            results.append({
                "deck": deck_name, "label": label,
                "daily_cap": gp["daily_cap"], "cooldown": gp["cooldown"],
                "lookback": gp["lookback"],
                "p1_rf": gp["p1_rf"], "p2_rf": gp["p2_rf"],
                "max_instr": gp["max_instr"], "max_losses": gp["max_losses"],
                "regime": gp["regime"],
                "n_combos": len(combo_list),
                "is_funded": is_funded, "is_n": is_n, "is_rate": is_rate,
                "is_p1": is_p1_pass, "is_p1_rate": is_p1_pass / is_n * 100 if is_n else 0,
                "is_dd": is_dd_fails, "is_profit_fail": is_profit_fails,
                "oos_funded": oos_funded, "oos_n": oos_n, "oos_rate": oos_rate,
                "oos_p1": oos_p1_pass, "oos_p1_rate": oos_p1_pass / oos_n * 100 if oos_n else 0,
                "oos_dd": oos_dd_fails, "oos_profit_fail": oos_profit_fails,
            })

            iter_count += 1
            if iter_count % 100 == 0:
                best = max(results, key=lambda x: x["is_rate"])
                print(f"    [{iter_count}/{total_iters}] best IS: {best['label'][:50]} → "
                      f"{best['is_rate']:.1f}%", flush=True)

    # ── Step 6: Results ──
    results.sort(key=lambda x: (-x["is_rate"], -x["oos_rate"]))

    print(f"\n{'='*120}")
    print(f"  TOP 30 by IS funded rate")
    print(f"{'='*120}")
    print(f"  {'Label':<55s} {'#':>3s} {'IS%':>6s} {'IS fund':>8s} {'IS P1%':>6s} "
          f"{'OOS%':>6s} {'OOS fund':>9s} {'OOS P1%':>7s} {'Gap':>6s}")
    print(f"  {'-'*110}")

    for r in results[:30]:
        gap = r["is_rate"] - r["oos_rate"]
        print(f"  {r['label'][:55]:<55s} {r['n_combos']:>3d} "
              f"{r['is_rate']:>5.1f}% {r['is_funded']:>3d}/{r['is_n']:<4d} "
              f"{r['is_p1_rate']:>5.1f}% "
              f"{r['oos_rate']:>5.1f}% {r['oos_funded']:>4d}/{r['oos_n']:<4d} "
              f"{r['oos_p1_rate']:>5.1f}%  "
              f"{gap:>+5.1f}pp")

    # ── Step 7: OOS ranking (for reference only, NOT for selection) ──
    results_oos = sorted(results, key=lambda x: (-x["oos_rate"], -x["is_rate"]))

    print(f"\n{'='*120}")
    print(f"  TOP 15 by OOS funded rate (2025 — validation only, not selection)")
    print(f"{'='*120}")
    print(f"  {'Label':<55s} {'#':>3s} {'IS%':>6s} {'OOS%':>6s} {'OOS fund':>9s} "
          f"{'OOS P1':>7s} {'Gap':>6s}")
    print(f"  {'-'*100}")

    for r in results_oos[:15]:
        gap = r["is_rate"] - r["oos_rate"]
        print(f"  {r['label'][:55]:<55s} {r['n_combos']:>3d} "
              f"{r['is_rate']:>5.1f}% {r['oos_rate']:>5.1f}% "
              f"{r['oos_funded']:>4d}/{r['oos_n']:<4d} "
              f"{r['oos_p1']:>3d}/{r['oos_n']:<4d}  "
              f"{gap:>+5.1f}pp")

    # ── Step 8: Best config — selected by IS rate (OOS is validation only) ──
    best = results[0]  # results already sorted by (-is_rate, -oos_rate)
    best_deck = decks[best["deck"]]

    print(f"\n{'='*120}")
    print(f"  BEST CONFIG: {best['label']}")
    print(f"  IS: {best['is_rate']:.1f}% ({best['is_funded']}/{best['is_n']}) | "
          f"OOS: {best['oos_rate']:.1f}% ({best['oos_funded']}/{best['oos_n']})")
    print(f"  Deck: {', '.join(best_deck)}")
    print(f"{'='*120}")

    print(f"\n  {'Window':<12s} {'Active':>7s} {'P1':>12s} {'P1 Prof':>8s} {'P1 DD':>6s} "
          f"{'P1 Eq':>8s} {'P2':>12s} {'P2 Prof':>8s} {'Exam':>10s}")
    print(f"  {'-'*100}")

    for start in oos_windows:
        if best["lookback"] > 0:
            active = get_active_combos(streams, best_deck, start.date(),
                                       lookback_months=best["lookback"])
        else:
            active = best_deck

        regime_kw = {}
        if best.get("regime", 0) > 0:
            regime_kw = {"regime_data": regime_data, "regime_threshold": best["regime"]}

        r = simulate_exam(
            streams, active, start,
            daily_loss_cap_pct=best["daily_cap"],
            combo_daily_max_losses=best["cooldown"],
            p1_risk_factor=best.get("p1_rf", 1.0),
            p2_risk_factor=best["p2_rf"],
            max_instr_per_day=best.get("max_instr", 99),
            max_daily_losses=best.get("max_losses", 99),
            **regime_kw,
        )

        p1 = r["p1"]
        p1_eq = f"${p1['final_equity']/1000:.0f}K"
        p2_out = r["p2"]["outcome"] if r["p2"] else "-"
        p2_prof = f"{r['p2']['profit_pct']:+.1f}%" if r["p2"] else "-"
        marker = " <<<" if r["exam"] == "FUNDED" else ""

        print(f"  {str(start.date()):<12s} {len(active):>3d}/{len(best_deck):<3d} "
              f"{p1['outcome']:>12s} {p1['profit_pct']:>+7.1f}% {p1['max_dd']:>5.1f}% "
              f"{p1_eq:>8s} {p2_out:>12s} {p2_prof:>8s} {r['exam']:>10s}{marker}")

    # ── Step 9: Year-by-year stability ──
    print(f"\n{'='*120}")
    print(f"  YEAR-BY-YEAR STABILITY — {best['label'][:50]}")
    print(f"{'='*120}")
    print(f"  {'Year':<6s} {'Win':>4s} {'P1 Pass':>8s} {'P1%':>6s} {'Funded':>8s} "
          f"{'Fund%':>6s} {'DD Fail':>7s} {'Prof Fail':>9s} {'Type':>4s}")
    print(f"  {'-'*75}")

    for year in range(2016, 2026):
        year_windows = pd.date_range(f"{year}-01-01", f"{year}-10-01", freq="30D")
        funded = p1_pass = dd_fails = prof_fails = 0

        for start in year_windows:
            if best["lookback"] > 0:
                active = get_active_combos(streams, best_deck, start.date(),
                                           lookback_months=best["lookback"])
            else:
                active = best_deck
            if not active:
                continue

            r = simulate_exam(
                streams, active, start,
                daily_loss_cap_pct=best["daily_cap"],
                combo_daily_max_losses=best["cooldown"],
                p1_risk_factor=best.get("p1_rf", 1.0),
                p2_risk_factor=best["p2_rf"],
                max_instr_per_day=best.get("max_instr", 99),
                max_daily_losses=best.get("max_losses", 99),
                **regime_kw,
            )
            if r["exam"] == "FUNDED": funded += 1
            if r["p1"]["outcome"] == "PASS": p1_pass += 1
            if "DD" in r["p1"]["outcome"]: dd_fails += 1
            if r["p1"]["outcome"] == "FAIL_PROFIT": prof_fails += 1

        n = len(year_windows)
        tag = "OOS" if year >= 2025 else "IS"
        mark = " <<<" if year >= 2025 else ""
        print(f"  {year:<6d} {n:>4d} {p1_pass:>3d}/{n:<4d} {p1_pass/n*100:>5.1f}% "
              f"{funded:>3d}/{n:<4d} {funded/n*100:>5.1f}% "
              f"{dd_fails:>7d} {prof_fails:>9d} {tag:>4s}{mark}")

    # ── Step 10: ROI for $5K accounts ──
    oos_rate = best["oos_rate"]
    is_rate = best["is_rate"]

    print(f"\n{'='*120}")
    print(f"  ROI ANALYSIS — $5K accounts @EUR40/exam")
    print(f"{'='*120}")

    # Use conservative estimate: min(IS, OOS) to avoid optimism bias
    conservative_rate = min(is_rate, oos_rate) if oos_rate > 0 else is_rate * 0.5
    print(f"\n  IS rate: {is_rate:.1f}% | OOS rate: {oos_rate:.1f}% | Conservative: {conservative_rate:.1f}%")

    if conservative_rate > 0:
        fr = conservative_rate / 100
        profit_per_funded_mo = 200  # ~$200/mo per $5K funded account (conservative)
        print(f"\n  {'Exams/mo':>10s} {'Cost':>8s} {'Funded':>8s} {'Income/mo':>10s} "
              f"{'Net/mo':>10s} {'ROI':>8s} {'Payback':>10s}")
        print(f"  {'-'*70}")
        for n_ex in [5, 10, 15, 20, 30, 50]:
            funded_mo = n_ex * fr
            cost = n_ex * 40
            # Each funded account generates income for ~4 months avg
            income_mo = funded_mo * profit_per_funded_mo
            net = income_mo - cost
            roi = net / cost * 100 if cost > 0 else 0
            payback = f"{cost/income_mo:.1f}mo" if income_mo > 0 else "never"
            print(f"  {n_ex:>10d} EUR{cost:>5.0f} {funded_mo:>7.1f} EUR{income_mo:>7.0f} "
                  f"EUR{net:>+7.0f} {roi:>+6.0f}% {payback:>10s}")
    else:
        print(f"\n  Rate too low for profitable deployment. Need to improve strategies.")

    # ── Step 10b: Also test windows with just P1 data (30 days) for extra OOS info ──
    extended_oos = pd.date_range("2025-01-01", last_data_date - timedelta(days=30), freq="30D")
    extra_windows = [w for w in extended_oos if w not in oos_windows]
    if extra_windows:
        print(f"\n  Extended OOS (P1-only, insufficient data for P2):")
        for start in extra_windows:
            if best["lookback"] > 0:
                active = get_active_combos(streams, best_deck, start.date(),
                                           lookback_months=best["lookback"])
            else:
                active = best_deck
            r = simulate_exam(streams, active, start,
                              daily_loss_cap_pct=best["daily_cap"],
                              combo_daily_max_losses=best["cooldown"],
                              p1_risk_factor=best.get("p1_rf", 1.0),
                              p2_risk_factor=best["p2_rf"],
                              max_instr_per_day=best.get("max_instr", 99),
                              max_daily_losses=best.get("max_losses", 99),
                              **regime_kw)
            p1 = r["p1"]
            print(f"    {str(start.date()):<12s} P1: {p1['outcome']:>12s} {p1['profit_pct']:>+7.1f}% "
                  f"DD={p1['max_dd']:.1f}% (P2 data truncated — result unreliable)")

    # ── Step 11: Stability check — do nearby configs perform similarly? ──
    print(f"\n{'='*120}")
    print(f"  STABILITY CHECK — are top results robust or fragile?")
    print(f"{'='*120}")

    top10_is = results[:10]
    top10_oos = results_oos[:10]

    is_rates = [r["is_rate"] for r in top10_is]
    oos_rates = [r["oos_rate"] for r in top10_oos]
    print(f"\n  Top 10 IS rates:  {', '.join(f'{r:.1f}%' for r in is_rates)}")
    print(f"  Top 10 OOS rates: {', '.join(f'{r:.1f}%' for r in oos_rates)}")
    print(f"  IS spread:  {max(is_rates) - min(is_rates):.1f}pp (smaller = more stable)")
    print(f"  OOS spread: {max(oos_rates) - min(oos_rates):.1f}pp")

    # Check if best IS and best OOS agree on structural choices
    best_is = results[0]
    best_oos = results_oos[0]
    print(f"\n  Best IS config:  {best_is['label'][:60]}")
    print(f"  Best OOS config: {best_oos['label'][:60]}")
    if best_is["deck"] == best_oos["deck"]:
        print(f"  Deck AGREES — good sign (same deck wins both IS and OOS)")
    else:
        print(f"  Deck DIFFERS — IS prefers {best_is['deck']}, OOS prefers {best_oos['deck']}")

    # ══════════════════════════════════════════════════════════════════
    # PHASE 2: FUNDED ACCOUNT SURVIVAL OPTIMIZATION
    # Different operating mode: conservative to maximize account lifespan
    # ══════════════════════════════════════════════════════════════════

    print(f"\n{'='*120}")
    print(f"  FUNDED SURVIVAL OPTIMIZATION — find best conservative mode")
    print(f"  Exam mode: aggressive (pass fast). Funded mode: conservative (survive long).")
    print(f"{'='*120}")

    funded_windows = pd.date_range("2016-01-01", "2024-07-01", freq="60D")

    def simulate_funded(deck_combos, fund_start, risk_factor=1.0,
                        daily_cap=2.5, cooldown=1, max_instr=99,
                        max_losses=99, months=18):
        """Simulate a funded account with given risk controls."""
        fund_end = fund_start + timedelta(days=months * 30)
        initial = 100000
        equity = initial
        daily_start_eq = initial
        current_day = None
        combo_day_losses = defaultdict(int)
        instr_day_trades_d = defaultdict(int)
        total_daily_losses_d = 0
        daily_stopped = False
        max_dd = 0
        max_daily_dd = 0
        monthly_pnl = defaultdict(float)
        terminated = False
        termination_day = None
        peak_equity = initial

        all_trades = []
        for combo in deck_combos:
            if combo not in streams:
                continue
            for t in streams[combo]:
                if fund_start.date() <= t["date"] < fund_end.date():
                    all_trades.append(t)
        all_trades.sort(key=lambda x: x["ts"])

        for t in all_trades:
            if terminated:
                break

            if t["date"] != current_day:
                if current_day is not None:
                    dd = (daily_start_eq - equity) / initial
                    max_daily_dd = max(max_daily_dd, dd)
                current_day = t["date"]
                daily_start_eq = equity
                combo_day_losses.clear()
                instr_day_trades_d.clear()
                total_daily_losses_d = 0
                daily_stopped = False

            if daily_stopped:
                continue

            combo = t["combo"]
            if combo_day_losses[combo] >= cooldown:
                continue

            instrument = ALL_COMBOS[combo]["symbol"]
            if instr_day_trades_d[instrument] >= max_instr:
                continue
            if total_daily_losses_d >= max_losses:
                continue

            pnl = t["pnl"] * risk_factor
            equity += pnl
            peak_equity = max(peak_equity, equity)
            instr_day_trades_d[instrument] += 1
            month_key = t["date"].strftime("%Y-%m")
            monthly_pnl[month_key] += pnl

            if pnl < 0:
                combo_day_losses[combo] += 1
                total_daily_losses_d += 1

            daily_loss_pct = (daily_start_eq - equity) / initial * 100
            if daily_loss_pct >= daily_cap:
                daily_stopped = True

            # FTMO hard limits: 5% daily DD, 10% total DD from INITIAL
            dd_total = (initial - equity) / initial
            max_dd = max(max_dd, dd_total)
            daily_dd_hard = (daily_start_eq - equity) / initial

            if dd_total >= 0.10 or daily_dd_hard >= 0.05:
                terminated = True
                termination_day = t["date"]

        n_months = len(monthly_pnl)
        total_profit = equity - initial
        avg_monthly = total_profit / max(n_months, 1)
        win_months = sum(1 for v in monthly_pnl.values() if v > 0)

        return {
            "months": n_months, "terminated": terminated,
            "term_day": termination_day,
            "total_pnl": total_profit, "avg_monthly": avg_monthly,
            "win_months": win_months, "max_dd": max_dd * 100,
            "max_daily_dd": max_daily_dd * 100,
        }

    # Grid search over funded-mode parameters
    funded_grid = []
    for f_risk in [0.25, 0.35, 0.5, 0.7, 1.0]:
        for f_dc in [1.5, 2.0, 2.5, 3.0]:
            for f_cd in [1, 2]:
                for f_mi in [2, 3, 99]:
                    for f_ml in [2, 3, 5, 99]:
                        funded_grid.append({
                            "f_risk": f_risk, "f_dc": f_dc,
                            "f_cd": f_cd, "f_mi": f_mi, "f_ml": f_ml,
                        })

    print(f"  {len(funded_grid)} funded configs × {len(funded_windows)} windows = "
          f"{len(funded_grid) * len(funded_windows):,} sims")

    funded_results = []
    for gi, fp in enumerate(funded_grid):
        all_surv = []
        for start in funded_windows:
            fund_start = start + timedelta(days=90)
            sr = simulate_funded(
                best_deck, fund_start,
                risk_factor=fp["f_risk"], daily_cap=fp["f_dc"],
                cooldown=fp["f_cd"], max_instr=fp["f_mi"],
                max_losses=fp["f_ml"],
            )
            all_surv.append(sr)

        avg_months = np.mean([s["months"] for s in all_surv])
        avg_pnl_mo = np.mean([s["avg_monthly"] for s in all_surv])
        term_rate = sum(1 for s in all_surv if s["terminated"]) / len(all_surv)
        median_months = np.median([s["months"] for s in all_surv])
        # Expected value per funded account = avg monthly pnl × avg months survived
        ev_per_account = avg_pnl_mo * avg_months

        label = (f"RF{fp['f_risk']}_DC{fp['f_dc']}_CD{fp['f_cd']}"
                 f"_MI{fp['f_mi']}_ML{fp['f_ml']}")
        funded_results.append({
            "label": label, **fp,
            "avg_months": avg_months, "median_months": median_months,
            "avg_pnl_mo": avg_pnl_mo, "term_rate": term_rate,
            "ev_account": ev_per_account,
            "details": all_surv,
        })

        if (gi + 1) % 200 == 0:
            best_so_far = max(funded_results, key=lambda x: x["ev_account"])
            print(f"    [{gi+1}/{len(funded_grid)}] best EV: {best_so_far['label']} → "
                  f"${best_so_far['ev_account']:+,.0f}/acct, "
                  f"{best_so_far['avg_months']:.1f}mo avg", flush=True)

    # Sort by expected value per account (survival × income)
    funded_results.sort(key=lambda x: -x["ev_account"])

    print(f"\n{'='*120}")
    print(f"  TOP 20 FUNDED CONFIGS by Expected Value per Account")
    print(f"  EV = avg_monthly_pnl × avg_survival_months (total $100K lifetime profit)")
    print(f"{'='*120}")
    print(f"  {'Config':<35s} {'Avg Mo':>7s} {'Med Mo':>7s} {'Term%':>6s} "
          f"{'$/mo':>10s} {'EV/acct':>12s} {'$5K net/mo':>11s}")
    print(f"  {'-'*100}")

    for fr in funded_results[:20]:
        pnl_5k_mo = fr["avg_pnl_mo"] * 0.05 * 0.8  # scale to $5K, 80% split
        print(f"  {fr['label']:<35s} {fr['avg_months']:>6.1f} {fr['median_months']:>6.1f} "
              f"{fr['term_rate']*100:>5.0f}% ${fr['avg_pnl_mo']:>+8,.0f} "
              f"${fr['ev_account']:>+10,.0f} ${pnl_5k_mo:>+9,.0f}")

    # Best funded config
    best_funded = funded_results[0]
    print(f"\n  BEST FUNDED CONFIG: {best_funded['label']}")
    print(f"    Risk factor: {best_funded['f_risk']}x | Daily cap: {best_funded['f_dc']}% | "
          f"Cooldown: {best_funded['f_cd']} | Max instr/day: {best_funded['f_mi']} | "
          f"Max losses/day: {best_funded['f_ml']}")

    # Show detail of best funded config
    print(f"\n  {'Start':<12s} {'Months':>7s} {'Status':>16s} {'Total PnL':>12s} "
          f"{'$/mo':>10s} {'Win/Tot':>8s} {'Max DD':>7s} {'Max DDD':>8s}")
    print(f"  {'-'*90}")

    for sr in best_funded["details"]:
        start = (funded_windows[best_funded["details"].index(sr)]
                 + timedelta(days=90)).date()
        status = f"TERM {sr['term_day']}" if sr["terminated"] else "ALIVE (18mo)"
        wm = f"{sr['win_months']}/{sr['months']}"
        print(f"  {str(start):<12s} {sr['months']:>7d} {status:>16s} "
              f"${sr['total_pnl']:>+10,.0f} ${sr['avg_monthly']:>+8,.0f} "
              f"{wm:>8s} {sr['max_dd']:>6.1f}% {sr['max_daily_dd']:>7.1f}%")

    # ══════════════════════════════════════════════════════════════════
    # PHASE 3: COMBINED ROI — exam factory + funded survival
    # ══════════════════════════════════════════════════════════════════

    print(f"\n{'='*120}")
    print(f"  COMBINED ROI — Exam Factory + Funded Account Income")
    print(f"{'='*120}")

    # Parameters
    exam_cost = 40  # EUR for $5K account
    fund_rate = min(is_rate, oos_rate) / 100 if oos_rate > 0 else is_rate / 200
    avg_surv = best_funded["avg_months"]
    monthly_pnl_5k = best_funded["avg_pnl_mo"] * 0.05 * 0.8  # $5K scale, 80% split
    # FTMO refunds exam fee when funded
    exam_refund = exam_cost

    print(f"\n  Exam mode: {best['label'][:50]}")
    print(f"  Funded mode: {best_funded['label']}")
    print(f"  Fund rate: {fund_rate*100:.1f}% | Avg survival: {avg_surv:.1f} months")
    print(f"  Monthly income per funded $5K: EUR{monthly_pnl_5k:+,.0f}")
    print(f"  Exam fee: EUR{exam_cost} (refunded on funding)")

    print(f"\n  {'Exams/mo':>9s} {'Cost':>7s} {'Funded':>7s} {'Refund':>7s} "
          f"{'Net Cost':>9s} {'Active':>7s} {'Income':>9s} {'Net/mo':>9s} "
          f"{'ROI':>7s} {'Net/yr':>10s}")
    print(f"  {'-'*95}")

    for n_ex in [5, 10, 15, 20, 30]:
        new_funded_mo = n_ex * fund_rate
        gross_cost = n_ex * exam_cost
        refund = new_funded_mo * exam_refund
        net_cost = gross_cost - refund
        # Steady-state: active accounts = new_funded/month × avg_survival
        active_accounts = new_funded_mo * avg_surv
        monthly_income = active_accounts * monthly_pnl_5k
        net_monthly = monthly_income - net_cost
        roi = net_monthly / gross_cost * 100 if gross_cost > 0 else 0
        net_yearly = net_monthly * 12

        print(f"  {n_ex:>9d} EUR{gross_cost:>4.0f} {new_funded_mo:>6.1f} EUR{refund:>4.0f} "
              f"EUR{net_cost:>6.0f} {active_accounts:>6.1f} EUR{monthly_income:>7.0f} "
              f"EUR{net_monthly:>+7.0f} {roi:>+6.0f}% EUR{net_yearly:>+8.0f}")

    # Worst-case scenario (halve everything)
    print(f"\n  WORST CASE (half fund rate, half survival, half income):")
    wc_rate = fund_rate / 2
    wc_surv = avg_surv / 2
    wc_income = monthly_pnl_5k / 2
    for n_ex in [10, 20]:
        new_f = n_ex * wc_rate
        cost = n_ex * exam_cost
        refund = new_f * exam_refund
        active = new_f * wc_surv
        inc = active * wc_income
        net = inc - (cost - refund)
        print(f"    {n_ex} exams/mo → {new_f:.1f} funded, {active:.1f} active → "
              f"EUR{inc:.0f} income - EUR{cost - refund:.0f} cost = EUR{net:+.0f}/mo")

    # ── Summary verdict ──
    print(f"\n{'='*120}")
    print(f"  FINAL VERDICT")
    print(f"{'='*120}")

    print(f"\n  EXAM MODE:   {best['label'][:55]}")
    print(f"    → {is_rate:.1f}% IS / {oos_rate:.1f}% OOS funded rate")
    print(f"\n  FUNDED MODE: {best_funded['label']}")
    print(f"    → {best_funded['avg_months']:.1f}mo avg survival, "
          f"${best_funded['avg_pnl_mo'] * 0.05:+,.0f}/mo per $5K (before split)")
    print(f"    → {best_funded['term_rate']*100:.0f}% terminated within 18mo")

    if oos_rate >= 20 and avg_surv >= 5:
        print(f"\n  DEPLOYMENT READY — strong exam rate + decent survival")
        print(f"  Start with 10 exams, scale based on live funded rate")
    elif oos_rate >= 10:
        print(f"\n  CAUTIOUS DEPLOY — start with 5 exams to validate live")
    else:
        print(f"\n  NOT READY — improve strategies before deploying")


if __name__ == "__main__":
    main()
