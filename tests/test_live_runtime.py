from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from algosbz.core.config import load_all_instruments, load_config
from algosbz.core.enums import SignalAction
from algosbz.core.models import Signal
from algosbz.live.runtime import trading_day_key
from scripts import live_trader, validate_live


def make_account():
    app_config = load_config()
    app_config.risk.max_positions = 3
    app_config.risk.daily_reset_hour = 5
    app_config.risk.daily_reset_timezone = "UTC"

    mode_configs = {
        "exam_mode": {
            "risk_per_trade": 0.02,
            "daily_cap_pct": 2.0,
            "cooldown": 1,
            "max_instr_per_day": 99,
            "max_daily_losses": 3,
            "p2_risk_factor": 0.7,
        },
        "funded_mode": {
            "risk_per_trade": 0.014,
            "daily_cap_pct": 1.5,
            "cooldown": 1,
            "max_instr_per_day": 99,
            "max_daily_losses": 3,
        },
        "symbol_map": {},
        "runtime": {
            "daily_reset_hour": app_config.risk.daily_reset_hour,
            "daily_reset_timezone": app_config.risk.daily_reset_timezone,
        },
    }
    acct_cfg = {
        "name": "TEST",
        "enabled": True,
        "login": 1,
        "password": "x",
        "server": "demo",
        "start_date": "2026-01-01",
        "state": "phase1",
        "initial_balance": 100000,
    }
    return live_trader.LiveAccount(
        acct_cfg["name"],
        acct_cfg,
        mode_configs,
        app_config,
        load_all_instruments(),
    )


def test_account_level_max_positions_blocks_fourth_trade():
    acct = make_account()
    acct.open_positions = {
        "MACross_XAUUSD_trend_H4": 1,
        "TPB_XTIUSD_trend_H4": 2,
        "MACross_GBPJPY_megaT_H4": 3,
    }

    signal = Signal(
        action=SignalAction.ENTER_LONG,
        symbol="EURUSD",
        timestamp=datetime(2026, 1, 2, 12, 0, 0),
        stop_loss=1.0950,
        take_profit=1.1150,
        metadata={"ref_price": 1.1000},
    )

    order = acct.evaluate_signal("Engulf_EURUSD_wideR_H4", signal, fill_price=1.1000)
    assert order is None


def test_reconcile_account_positions_recovers_untracked_mt5_positions(tmp_path, monkeypatch):
    monkeypatch.setattr(live_trader, "LOG_PATH", tmp_path / "live_trades.log")
    acct = make_account()
    acct.state.sync_runtime_day(100000, trading_day=datetime(2026, 1, 2).date())

    class FakeConn:
        def get_open_positions(self):
            return [{
                "ticket": 12345,
                "symbol": "EURUSD",
                "direction": "BUY",
                "volume": 0.5,
                "open_price": 1.1000,
                "sl": 1.0950,
                "tp": 1.1150,
                "comment": "AS_Engulf_EURUSD_wideR_H4_phase1",
                "time": datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc),
            }]

    result = live_trader.reconcile_account_positions(acct, FakeConn())

    assert result == {"recovered": 1, "removed": 0}
    assert acct.open_positions["Engulf_EURUSD_wideR_H4"] == 12345
    assert acct.state.total_trades == 1
    assert acct.state._instr_day_trades["EURUSD"] == 1

    history_path = tmp_path / "trade_history.jsonl"
    rows = [json.loads(line) for line in history_path.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["event"] == "OPEN_RECOVERED"
    assert rows[-1]["account"] == "TEST"


def test_state_persistence_restores_runtime_counters(tmp_path, monkeypatch):
    state_path = tmp_path / "live_state.json"
    monkeypatch.setattr(live_trader, "STATE_PATH", state_path)

    acct = make_account()
    acct.state.sync_runtime_day(100000, trading_day=datetime(2026, 1, 2).date())
    acct.state._day_start_equity = 101000
    acct.state._combo_day_losses["Engulf_EURUSD_wideR_H4"] = 1
    acct.state._instr_day_trades["EURUSD"] = 2
    acct.state._total_daily_losses = 1
    acct.state._daily_stopped = True
    acct.open_positions["Engulf_EURUSD_wideR_H4"] = 321

    live_trader.save_state([acct], "2026-01-02")

    restored = make_account()
    pending = live_trader.load_state([restored])

    assert pending == {}
    assert restored.open_positions["Engulf_EURUSD_wideR_H4"] == 321
    assert restored.state._current_day.isoformat() == "2026-01-02"
    assert restored.state._day_start_equity == 101000
    assert restored.state._combo_day_losses["Engulf_EURUSD_wideR_H4"] == 1
    assert restored.state._instr_day_trades["EURUSD"] == 2
    assert restored.state._total_daily_losses == 1
    assert restored.state._daily_stopped is True

    restored.state.sync_runtime_day(99500, trading_day=restored.state._current_day)
    assert restored.state._day_start_equity == 101000
    assert restored.state.current_equity == 99500


def test_validate_live_loader_keeps_only_entry_events(tmp_path, monkeypatch):
    trade_log = tmp_path / "trade_history.jsonl"
    rows = [
        {
            "event": "OPEN",
            "combo": "Engulf_EURUSD_wideR_H4",
            "direction": "BUY",
            "fill_price": 1.1,
            "ts": "2026-04-11T10:00:00+00:00",
        },
        {
            "event": "CLOSE",
            "combo": "Engulf_EURUSD_wideR_H4",
            "direction": "CLOSE",
            "pnl": 12.0,
            "ts": "2026-04-11T11:00:00+00:00",
        },
        {
            "direction": "BUY",
            "combo": "LegacyBad",
            "fill_price": 0.0,
            "ts": "2026-04-11T12:00:00",
        },
        {
            "event": "OPEN_RECOVERED",
            "combo": "MACross_XAUUSD_trend_H4",
            "direction": "SELL",
            "fill_price": 2500.0,
            "ts": "2026-04-11T13:00:00+00:00",
        },
    ]
    trade_log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    monkeypatch.setattr(validate_live, "TRADE_LOG", trade_log)
    trades = validate_live.load_live_trades()

    assert [t["combo"] for t in trades] == [
        "Engulf_EURUSD_wideR_H4",
        "MACross_XAUUSD_trend_H4",
    ]
    assert all(t["_ts"].tzinfo is None for t in trades)


def test_trading_day_key_respects_reset_hour():
    before_reset = datetime(2026, 4, 11, 4, 59, tzinfo=timezone.utc)
    after_reset = datetime(2026, 4, 11, 5, 0, tzinfo=timezone.utc)

    assert trading_day_key(before_reset, reset_hour=5, timezone_name="UTC").isoformat() == "2026-04-10"
    assert trading_day_key(after_reset, reset_hour=5, timezone_name="UTC").isoformat() == "2026-04-11"


def test_target_reached_blocks_trading_until_min_days():
    acct = make_account()
    acct.state.current_equity = 110000
    acct.state.trading_days = 3

    can_trade, reason = acct.state.can_trade("Engulf_EURUSD_wideR_H4", "EURUSD")

    assert can_trade is False
    assert "target reached" in reason


def test_phase1_to_phase2_transition_resets_state():
    acct = make_account()
    acct.state.current_equity = 110000
    acct.state.trading_days = 4
    acct.state.total_pnl = 10000

    transition = acct.state.check_phase_transition()

    assert transition == "phase2"
    assert acct.state.state == "phase2"
    assert acct.state.current_equity == acct.state.initial_balance
    assert acct.state.trading_days == 0
    assert acct.state.total_pnl == 0.0
    assert abs(acct.state.risk_per_trade - 0.014) < 1e-12


def test_phase2_to_funded_transition_switches_mode_and_resets_state():
    acct = make_account()
    acct.state.state = "phase2"
    acct.state.current_equity = 105000
    acct.state.trading_days = 4
    acct.state.total_pnl = 5000

    transition = acct.state.check_phase_transition()

    assert transition == "funded"
    assert acct.state.state == "funded"
    assert acct.state.current_equity == acct.state.initial_balance
    assert acct.state.trading_days == 0
    assert acct.state.total_pnl == 0.0
    assert acct.state.daily_cap_pct == 1.5
