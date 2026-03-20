import logging
from typing import Optional

import pandas as pd

from algosbz.core.config import AppConfig, InstrumentConfig
from algosbz.core.enums import ExitReason, SignalAction
from algosbz.core.models import Signal
from algosbz.backtest.broker import SimulatedBroker
from algosbz.backtest.results import BacktestResult
from algosbz.data.resampler import resample
from algosbz.risk.manager import RiskManager
from algosbz.risk.equity_manager import EquityManager, EquityManagerConfig

logger = logging.getLogger(__name__)


class BacktestEngine:

    def __init__(self, config: AppConfig, instrument: InstrumentConfig,
                 equity_manager: EquityManager = None):
        self.config = config
        self.instrument = instrument
        self.equity_manager = equity_manager or EquityManager()

    def run(self, strategy, data: pd.DataFrame, symbol: str) -> BacktestResult:
        # Resample data to the strategy's required timeframe
        tf = strategy.required_timeframe()
        df = resample(data, tf)

        if df.empty:
            logger.warning("No data after resampling to %s", tf)
            return BacktestResult([], pd.Series(dtype=float), self.config.account.initial_balance)

        logger.info(
            "Running %s on %s %s: %d bars (%s to %s)",
            strategy.name, symbol, tf, len(df),
            df.index[0], df.index[-1],
        )

        # Initialize components
        broker = SimulatedBroker(
            instrument=self.instrument,
            spread_mode=self.config.backtest.spread_mode,
            slippage_pips=self.config.backtest.slippage_pips,
            pessimistic_fills=self.config.backtest.pessimistic_fills,
            commission_per_lot=self.config.backtest.commission_per_lot,
        )
        risk_mgr = RiskManager(self.config.risk, self.instrument)
        risk_mgr.initialize(self.config.account.initial_balance)

        # Equity curve manager (anti-martingale)
        eq_mgr = self.equity_manager
        eq_mgr.initialize(self.config.account.initial_balance)
        base_risk = self.config.risk.risk_per_trade

        # Let strategy pre-compute indicators
        strategy.setup(df)

        # Equity tracking
        equity_points = []
        initial_balance = self.config.account.initial_balance

        # Pending signal from previous bar (executed on NEXT bar's open)
        # This eliminates look-ahead bias: signal at bar[i] close → fill at bar[i+1] open
        pending_signal = None

        # Main event loop
        for i in range(len(df)):
            bar = df.iloc[i]
            timestamp = df.index[i]

            # 1. Process bar in broker (check SL/TP on open positions)
            closed_trades = broker.process_bar(bar)
            for trade in closed_trades:
                risk_mgr.on_trade_closed(trade.pnl, trade.exit_time)
                eq_mgr.on_trade_closed(trade.pnl, risk_mgr.current_equity)

            # 2. Update unrealized PnL and equity manager
            broker.update_unrealized_pnl(bar)
            total_unrealized = sum(p.unrealized_pnl for p in broker.open_positions)
            risk_mgr.update_on_bar(timestamp, total_unrealized)
            eq_mgr.on_bar(timestamp)

            # 3. Check if risk manager halted trading
            if risk_mgr.is_halted:
                pending_signal = None
                if broker.has_position:
                    closing_trades = broker.close_all_positions(
                        bar["open"], timestamp, ExitReason.RISK_MANAGER
                    )
                    for trade in closing_trades:
                        risk_mgr.on_trade_closed(trade.pnl, trade.exit_time)
                broker.cancel_pending_orders()
                equity_points.append((timestamp, risk_mgr.current_equity))
                continue

            # 4. Execute PENDING signal from previous bar at THIS bar's open
            #    This is the key fix: strategy decides at bar[i-1] close,
            #    execution happens at bar[i] open — no look-ahead
            if pending_signal is not None:
                sig = pending_signal
                pending_signal = None

                if sig.action == SignalAction.EXIT and broker.has_position:
                    closing_trades = broker.close_all_positions(
                        bar["open"], timestamp, ExitReason.SIGNAL
                    )
                    for trade in closing_trades:
                        risk_mgr.on_trade_closed(trade.pnl, trade.exit_time)

                elif sig.action in (SignalAction.ENTER_LONG, SignalAction.ENTER_SHORT):
                    if not broker.has_position:
                        multiplier = eq_mgr.get_risk_multiplier()
                        if multiplier > 0 and not eq_mgr.should_stop_trading():
                            # Adjust SL/TP: strategy set them relative to prev bar's close,
                            # but fill happens at this bar's open. Shift by the gap to
                            # preserve the intended ATR-based distances.
                            ref_price = sig.metadata.get("ref_price")
                            fill_price = bar["open"]
                            if ref_price is not None and sig.stop_loss is not None:
                                gap = fill_price - ref_price
                                adjusted_sl = sig.stop_loss + gap
                                adjusted_tp = (sig.take_profit + gap) if sig.take_profit else None
                                sig = Signal(
                                    action=sig.action,
                                    symbol=sig.symbol,
                                    timestamp=sig.timestamp,
                                    stop_loss=adjusted_sl,
                                    take_profit=adjusted_tp,
                                    metadata=sig.metadata,
                                )

                            risk_mgr.config.risk_per_trade = base_risk * multiplier
                            order = risk_mgr.evaluate_signal(
                                sig, risk_mgr.current_equity, fill_price
                            )
                            risk_mgr.config.risk_per_trade = base_risk

                            if order is not None:
                                fill = broker.submit_order(order, bar)
                                if fill:
                                    risk_mgr.on_trade_opened()
                                    risk_mgr._trading_days.add(timestamp.date())

            # 5. Get strategy signal (will be executed on NEXT bar)
            signal = strategy.on_bar(i, bar, broker.has_position)
            if signal.action != SignalAction.NO_ACTION:
                pending_signal = signal

            # 6. Record equity
            total_unrealized = sum(p.unrealized_pnl for p in broker.open_positions)
            equity_points.append((timestamp, risk_mgr.current_equity + total_unrealized))

        # Close any remaining positions at end of data
        if broker.has_position and len(df) > 0:
            last_bar = df.iloc[-1]
            last_time = df.index[-1]
            closing_trades = broker.close_all_positions(
                last_bar["close"], last_time, ExitReason.END_OF_DATA
            )
            for trade in closing_trades:
                risk_mgr.on_trade_closed(trade.pnl, trade.exit_time)

        # Build equity curve
        if equity_points:
            times, values = zip(*equity_points)
            equity_curve = pd.Series(values, index=pd.DatetimeIndex(times), name="equity")
        else:
            equity_curve = pd.Series(dtype=float, name="equity")

        all_trades = broker.trades

        logger.info(
            "Backtest complete: %d trades, final equity: %.2f (%.2f%%)",
            len(all_trades),
            risk_mgr.current_equity,
            (risk_mgr.current_equity - initial_balance) / initial_balance * 100,
        )

        return BacktestResult(all_trades, equity_curve, initial_balance)
