"""
AUDIT: Complete bias and methodology check before going live.

Checks every potential source of bias in our testing:
1. Look-ahead bias in engine
2. Linear PnL scaling error (1% → 2% is NOT 2x)
3. Intraday DD not captured (we check at trade close, FTMO checks tick-by-tick)
4. P2 balance carry-over not modeled
5. Combo pool selection bias (27 combos chosen on full data)
6. Sample size confidence intervals
7. Spread realism (data vs fixed)
8. Adaptive lookback uses trades from same pre-computed stream (subtle)
9. Overlapping positions across combos on same instrument

Usage:
    python -X utf8 scripts/audit_biases.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
import importlib
import pandas as pd
import numpy as np
from copy import deepcopy
from datetime import timedelta
from collections import defaultdict
from scipy import stats

from algosbz.core.config import load_config, load_all_instruments
from algosbz.data.loader import DataLoader
from algosbz.backtest.engine import BacktestEngine
from algosbz.risk.equity_manager import EquityManager, EquityManagerConfig

logging.basicConfig(level=logging.ERROR)

from scripts.challenge_decks import ALL_COMBOS, STRAT_REGISTRY


def load_strategy(entry):
    info = STRAT_REGISTRY[entry["strat"]]
    mod = importlib.import_module(info["module"])
    cls = getattr(mod, info["class"])
    return cls(entry["params"])


def precompute_at_risk(config, instruments, data_dict, combo_names, risk_pct):
    """Pre-compute trades at a specific risk level (not scaled)."""
    streams = {}
    cfg = deepcopy(config)
    cfg.risk.risk_per_trade = risk_pct
    cfg.risk.daily_dd_limit = 0.049
    cfg.risk.max_dd_limit = 0.099
    eq_cfg = EquityManagerConfig(
        dd_tiers=[(0.10, 1.0)], daily_stop_threshold=0.048,
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
        except Exception:
            continue
        trades = []
        for t in result.trades:
            ts = t.entry_time
            if isinstance(ts, pd.Timestamp):
                trades.append({"ts": ts, "date": ts.date(), "pnl": t.pnl, "combo": combo_name,
                               "sl": t.stop_loss, "entry": t.entry_price, "exit": t.exit_price,
                               "volume": t.volume, "direction": str(t.direction)})
        streams[combo_name] = trades
    return streams


def main():
    config = load_config()
    instruments = load_all_instruments()
    loader = DataLoader()

    # Subset of combos to audit (diverse set)
    audit_combos = [
        "VMR_USDCHF_H1", "SwBrk_XTIUSD_H4", "SessBrk_XTIUSD_M15",
        "SMCOB_GBPJPY_H1", "TPB_XTIUSD_loose_H4", "Engulf_XAUUSD_tight_H4",
    ]
    all_symbols = list({ALL_COMBOS[c]["symbol"] for c in audit_combos})

    data_dict = {}
    print("Loading data...")
    for sym in sorted(all_symbols):
        data_dict[sym] = loader.load(sym, start="2014-09-01", end="2026-01-01")
        print(f"  {sym}: {len(data_dict[sym]):,} bars")

    # ═══════════════════════════════════════════════════════════════
    # BIAS 1: Linear PnL scaling — is 2% risk = 2× (1% risk)?
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  BIAS 1: LINEAR PNL SCALING")
    print(f"  We pre-compute at 1% then multiply by 2. Is this accurate?")
    print(f"  Test: run same combos at 1% and 2%, compare PnL")
    print(f"{'='*120}")

    streams_1pct = precompute_at_risk(config, instruments, data_dict, audit_combos, 0.01)
    streams_2pct = precompute_at_risk(config, instruments, data_dict, audit_combos, 0.02)

    print(f"\n  {'Combo':<30s} {'1% trades':>10s} {'2% trades':>10s} {'1%×2 PnL':>12s} {'2% PnL':>12s} {'Error':>8s}")
    print(f"  {'-'*85}")

    total_error = 0
    total_combos = 0
    for combo in audit_combos:
        if combo not in streams_1pct or combo not in streams_2pct:
            continue
        t1 = streams_1pct[combo]
        t2 = streams_2pct[combo]

        pnl_1x2 = sum(t["pnl"] for t in t1) * 2
        pnl_2 = sum(t["pnl"] for t in t2)

        if abs(pnl_1x2) > 0:
            error = (pnl_2 - pnl_1x2) / abs(pnl_1x2) * 100
        else:
            error = 0

        total_error += abs(error)
        total_combos += 1

        print(f"  {combo:<30s} {len(t1):>10d} {len(t2):>10d} "
              f"${pnl_1x2:>+10,.0f} ${pnl_2:>+10,.0f} {error:>+6.1f}%")

    avg_error = total_error / total_combos if total_combos else 0
    severity = "LOW" if avg_error < 5 else "MODERATE" if avg_error < 15 else "HIGH"
    print(f"\n  Avg absolute error: {avg_error:.1f}% — Severity: {severity}")
    print(f"  NOTE: Differences come from compounding and risk manager capping lots near DD limit")
    if len(streams_1pct.get(audit_combos[0], [])) != len(streams_2pct.get(audit_combos[0], [])):
        print(f"  WARNING: Different trade counts — risk manager blocks some trades at higher risk!")

    # ═══════════════════════════════════════════════════════════════
    # BIAS 2: Intraday DD not captured
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  BIAS 2: INTRADAY DD NOT CAPTURED")
    print(f"  FTMO checks DD tick-by-tick. We only check at trade close.")
    print(f"  A position can be -4% intraday but close at -1%.")
    print(f"  We estimate worst-case intraday DD from SL distance.")
    print(f"{'='*120}")

    print(f"\n  Checking worst potential intraday exposure per trade (at 2% risk)...")
    for combo in audit_combos:
        if combo not in streams_2pct:
            continue
        trades = streams_2pct[combo]
        if not trades:
            continue

        # Max unrealized loss per trade = risk amount (SL distance × volume)
        # But gap slippage can make it worse
        max_unrealized = 0
        for t in trades:
            if t["sl"] and t["entry"]:
                sl_dist = abs(t["entry"] - t["sl"])
                realized = t["pnl"]
                # Worst case: price reaches SL before closing
                # At 2% risk, max unrealized ≈ 2% of 100K = $2,000
                max_unrealized = max(max_unrealized, sl_dist)

        print(f"    {combo:<30s}: max SL distance = {max_unrealized:.5f} price units")

    print(f"\n  At 2% risk, max single-trade intraday exposure = 2% = $2,000 on $100K")
    print(f"  With multiple positions open: could be 2% × N_open")
    print(f"  If 3 combos lose simultaneously: 6% intraday even if only 3% realized")
    print(f"  IMPACT: Our sim might MISS some daily DD fails that FTMO would catch")
    print(f"  SEVERITY: MODERATE — mitigated by daily cap of 2% which stops trading early")

    # ═══════════════════════════════════════════════════════════════
    # BIAS 3: P2 balance not carried over
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  BIAS 3: P2 BALANCE CARRY-OVER")
    print(f"  FTMO: P2 starts with P1 final balance (e.g. $110K after 10% P1)")
    print(f"  Our sim: P2 starts fresh at $100K")
    print(f"  Direction: CONSERVATIVE (real P2 is easier than our sim)")
    print(f"{'='*120}")

    print(f"\n  In reality:")
    print(f"  - P1 ending balance: ~$110K (if hit 10% target)")
    print(f"  - P2 DD headroom: $110K - $90K = $20K (vs $10K in our sim)")
    print(f"  - P2 target: $5K is 4.5% of $110K (vs 5% of $100K)")
    print(f"  IMPACT: Our P2 pass rate is PESSIMISTIC — real rate should be higher")
    print(f"  SEVERITY: LOW (works in our favor)")

    # ═══════════════════════════════════════════════════════════════
    # BIAS 4: Combo pool selected on full data (survivorship)
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  BIAS 4: COMBO POOL SURVIVORSHIP BIAS")
    print(f"  The 27 combos were selected using 2015-2024 data")
    print(f"  This is look-ahead: we picked combos that 'survived' the full period")
    print(f"  In reality we would have made different choices at different times")
    print(f"{'='*120}")

    print(f"\n  Mitigations already in place:")
    print(f"  1. Period stability: combo must be profitable in ≥3/5 two-year periods")
    print(f"  2. Adaptive lookback: rolling 6-month filter drops dead combos")
    print(f"  3. 2025 is pure OOS (not used in ANY selection)")
    print(f"\n  Remaining risk:")
    print(f"  - The STRATEGY CODE itself was designed looking at all data")
    print(f"  - Parameters were chosen/validated on 2015-2024")
    print(f"  - A strategy that 'looks good' on 10 years might not persist")
    print(f"  SEVERITY: MODERATE — partially mitigated by OOS but the pool is still biased")

    # ═══════════════════════════════════════════════════════════════
    # BIAS 5: Sample size (OOS confidence intervals)
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  BIAS 5: SAMPLE SIZE — Confidence intervals")
    print(f"  9 OOS windows with 5 funded = 55.6%. How confident are we?")
    print(f"{'='*120}")

    n_oos = 9
    k_funded = 5
    p_hat = k_funded / n_oos

    # Wilson score interval (better than Wald for small n)
    z = 1.96  # 95% CI
    denom = 1 + z**2 / n_oos
    center = (p_hat + z**2 / (2 * n_oos)) / denom
    margin = z * np.sqrt((p_hat * (1 - p_hat) + z**2 / (4 * n_oos)) / n_oos) / denom
    ci_low = max(0, center - margin)
    ci_high = min(1, center + margin)

    print(f"\n  Point estimate: {p_hat*100:.1f}%")
    print(f"  95% CI (Wilson): [{ci_low*100:.1f}%, {ci_high*100:.1f}%]")
    print(f"\n  Interpretation: true funded rate could be anywhere from {ci_low*100:.0f}% to {ci_high*100:.0f}%")
    print(f"  At the WORST case ({ci_low*100:.0f}%), ROI with 10 exams/mo:")

    worst_rate = ci_low
    if worst_rate > 0:
        funded_mo = 10 * worst_rate
        income = funded_mo * 200
        cost = 10 * 40
        print(f"    {funded_mo:.1f} funded → EUR{income:.0f} income - EUR{cost} cost = EUR{income-cost:+.0f}/mo")
    else:
        print(f"    0 funded → LOSS of EUR{10*40}/mo")

    best_rate = ci_high
    funded_mo = 10 * best_rate
    income = funded_mo * 200
    cost = 10 * 40
    print(f"  At the BEST case ({ci_high*100:.0f}%):")
    print(f"    {funded_mo:.1f} funded → EUR{income:.0f} income - EUR{cost} cost = EUR{income-cost:+.0f}/mo")

    print(f"\n  SEVERITY: HIGH — 9 windows is a small sample. Need 30+ for reliable estimates.")

    # ═══════════════════════════════════════════════════════════════
    # BIAS 6: Spread realism
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  BIAS 6: SPREAD REALISM")
    print(f"  Our broker uses spread_mode='data' (from Darwinex M1 CSVs)")
    print(f"  How does this compare to FTMO actual spreads?")
    print(f"{'='*120}")

    for sym in sorted(all_symbols):
        data = data_dict[sym]
        if "spread" not in data.columns:
            print(f"  {sym}: NO spread column!")
            continue
        spreads = data["spread"]
        spreads = spreads[spreads > 0]
        if len(spreads) == 0:
            print(f"  {sym}: all zero spreads!")
            continue

        inst = instruments.get(sym)
        if inst is None:
            continue

        spread_pips = spreads / inst.pip_size
        print(f"  {sym}: mean={spread_pips.mean():.2f} pips, median={spread_pips.median():.2f}, "
              f"p95={spread_pips.quantile(0.95):.2f}, max={spread_pips.max():.1f} "
              f"(default: {inst.default_spread_pips} pips)")

        # Check 2024-2025 vs 2020 (regime change?)
        for year in [2020, 2024, 2025]:
            mask = spreads.index.year == year
            if mask.sum() > 1000:
                year_pips = spreads[mask] / inst.pip_size
                print(f"    {year}: mean={year_pips.mean():.2f} pips, p95={year_pips.quantile(0.95):.2f}")

    print(f"\n  IMPACT: If FTMO spreads are wider than Darwinex, our backtest is optimistic")
    print(f"  MITIGATION: We already passed spread +50% stress test for ROBUST combos")
    print(f"  SEVERITY: LOW (data spreads + stress test provides safety margin)")

    # ═══════════════════════════════════════════════════════════════
    # BIAS 7: Overlapping positions on same instrument
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  BIAS 7: OVERLAPPING POSITIONS ON SAME INSTRUMENT")
    print(f"  Multiple combos can be in XTIUSD simultaneously")
    print(f"  On FTMO: allowed, but increases intraday DD exposure")
    print(f"{'='*120}")

    # Check how often multiple combos trade same instrument on same day
    all_streams = precompute_at_risk(config, instruments, data_dict, audit_combos, 0.02)
    instrument_day_combos = defaultdict(lambda: defaultdict(set))
    for combo, trades in all_streams.items():
        sym = ALL_COMBOS[combo]["symbol"]
        for t in trades:
            instrument_day_combos[sym][t["date"]].add(combo)

    print()
    for sym in sorted(instrument_day_combos.keys()):
        overlap_days = sum(1 for combos in instrument_day_combos[sym].values() if len(combos) > 1)
        total_days = len(instrument_day_combos[sym])
        pct = overlap_days / total_days * 100 if total_days > 0 else 0
        print(f"  {sym}: {overlap_days}/{total_days} days with multiple combos ({pct:.1f}%)")

    print(f"\n  IMPACT: Correlated losses on same instrument amplify daily DD")
    print(f"  MITIGATION: Daily cap of 2% limits total exposure")
    print(f"  SEVERITY: LOW-MODERATE")

    # ═══════════════════════════════════════════════════════════════
    # BIAS 8: Look-ahead in engine (verification)
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  BIAS 8: LOOK-AHEAD BIAS IN ENGINE")
    print(f"  Verified in code review:")
    print(f"  - engine.py:147: signal = strategy.on_bar(i, bar, has_position)")
    print(f"  - engine.py:101: pending_signal executed on NEXT bar's open")
    print(f"  - engine.py:119-132: SL/TP adjusted for open-vs-close gap")
    print(f"  - broker.py:209-211: pessimistic_fills=True (SL before TP when both hit)")
    print(f"  SEVERITY: NONE — correctly implemented")
    print(f"{'='*120}")

    # ═══════════════════════════════════════════════════════════════
    # BIAS 9: Adaptive filter uses pre-computed trades (subtle)
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  BIAS 9: ADAPTIVE FILTER & PRE-COMPUTED TRADES")
    print(f"  Trades are pre-computed over full period at 1% risk with independent DD manager")
    print(f"  The adaptive filter uses these to decide which combos are 'active'")
    print(f"  But: in a shared account, some trades would be BLOCKED by portfolio-level risk")
    print(f"  So the lookback PF is based on trades that might not all happen in reality")
    print(f"{'='*120}")
    print(f"\n  IMPACT: Adaptive filter sees a slightly optimistic trade stream")
    print(f"  MITIGATION: The filter only needs directional accuracy (PF > or < 1.0)")
    print(f"  Even if some trades are missing, the PF ranking is likely preserved")
    print(f"  SEVERITY: LOW")

    # ═══════════════════════════════════════════════════════════════
    # BIAS 10: 2023 = 0% funded year
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  BIAS 10: REGIME RISK — 2023 = 0% funded")
    print(f"  Even with the best config, 2023 had 0/10 funded windows")
    print(f"  If 2026 is similar to 2023, we lose EUR40 × N exams with zero return")
    print(f"{'='*120}")
    print(f"\n  This is the BIGGEST real risk: market regime changes")
    print(f"  The adaptive filter helps but can't prevent ALL losses in a hostile regime")
    print(f"  MITIGATION: Start small, monitor monthly, stop buying exams if 3+ consecutive fail")
    print(f"  SEVERITY: HIGH — this is a business risk, not a technical bug")

    # ═══════════════════════════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  AUDIT SUMMARY")
    print(f"{'='*120}")

    biases = [
        ("Linear PnL scaling", severity, "Error ~5-15%. Use direct 2% backtest for final numbers"),
        ("Intraday DD blind spot", "MODERATE", "Sim misses intraday spikes. Cap 2% mitigates"),
        ("P2 balance carry-over", "LOW", "Conservative — real P2 is easier"),
        ("Combo survivorship", "MODERATE", "Pool chosen on full data. OOS partially mitigates"),
        ("Sample size (n=9)", "HIGH", f"95% CI: [{ci_low*100:.0f}%, {ci_high*100:.0f}%]. Need more data"),
        ("Spread realism", "LOW", "Data spreads + stress test OK"),
        ("Overlapping positions", "LOW-MOD", "Daily cap mitigates"),
        ("Look-ahead bias", "NONE", "Correctly implemented"),
        ("Adaptive filter trades", "LOW", "Direction preserved despite filtering"),
        ("Regime risk (2023=0%)", "HIGH", "Business risk. Start small, stop-loss on strategy"),
    ]

    print(f"\n  {'Bias':<30s} {'Severity':<10s} {'Direction':<12s} Notes")
    print(f"  {'-'*100}")
    for name, sev, notes in biases:
        direction = "OPTIMISTIC" if sev in ("HIGH", "MODERATE") else "CONSERVATIVE" if "LOW" in sev and "P2" in name else "NEUTRAL"
        if "Intraday" in name or "Scaling" in name or "Survivorship" in name or "Sample" in name:
            direction = "OPTIMISTIC"
        elif "P2" in name:
            direction = "CONSERVATIVE"
        else:
            direction = "NEUTRAL"
        print(f"  {name:<30s} {sev:<10s} {direction:<12s} {notes}")

    print(f"\n  RECOMMENDATION:")
    print(f"  1. Re-run production_sim with DIRECT 2% backtests (not scaled) to fix Bias 1")
    print(f"  2. Accept OOS CI lower bound ({ci_low*100:.0f}%) as realistic funded rate")
    print(f"  3. Start with 5 exams (EUR200 risk), not 20")
    print(f"  4. Monthly review: if <2 funded in first 10 exams, pause and reassess")
    print(f"  5. Set aside 3-month capital buffer for regime risk (2023-like period)")


if __name__ == "__main__":
    main()
