from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel


CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


class AccountConfig(BaseModel):
    initial_balance: float = 100_000
    currency: str = "USD"


class RiskConfig(BaseModel):
    risk_per_trade: float = 0.01
    daily_dd_limit: float = 0.045
    max_dd_limit: float = 0.09
    max_positions: int = 3
    daily_reset_hour: int = 0
    min_risk_reward: float = 1.0


class BacktestConfig(BaseModel):
    spread_mode: str = "data"
    slippage_pips: float = 0.5
    pessimistic_fills: bool = True
    commission_per_lot: float = 7.0


class AppConfig(BaseModel):
    account: AccountConfig = AccountConfig()
    risk: RiskConfig = RiskConfig()
    backtest: BacktestConfig = BacktestConfig()


class InstrumentConfig(BaseModel):
    pip_size: float
    point_size: float
    pip_value_per_lot: float
    default_spread_pips: float
    min_lot: float = 0.01
    max_lot: float = 100.0


def load_config(path: Optional[str] = None) -> AppConfig:
    if path is None:
        path = str(CONFIG_DIR / "default.yaml")
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return AppConfig(**raw)


def load_instrument_config(symbol: str, path: Optional[str] = None) -> InstrumentConfig:
    if path is None:
        path = str(CONFIG_DIR / "instruments.yaml")
    with open(path, "r", encoding="utf-8") as f:
        instruments = yaml.safe_load(f)
    if symbol not in instruments:
        raise KeyError(f"Instrument '{symbol}' not found in {path}")
    return InstrumentConfig(**instruments[symbol])


def load_all_instruments(path: Optional[str] = None) -> dict[str, InstrumentConfig]:
    if path is None:
        path = str(CONFIG_DIR / "instruments.yaml")
    with open(path, "r", encoding="utf-8") as f:
        instruments = yaml.safe_load(f)
    return {sym: InstrumentConfig(**cfg) for sym, cfg in instruments.items()}
