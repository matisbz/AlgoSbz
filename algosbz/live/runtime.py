from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class TradingDayConfig:
    reset_hour: int = 0
    timezone_name: str = "UTC"

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_aware_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def trading_day_key(
    ts: datetime | None = None,
    *,
    reset_hour: int = 0,
    timezone_name: str = "UTC",
) -> date:
    current = ensure_aware_utc(ts or utc_now())
    local_time = current.astimezone(ZoneInfo(timezone_name))
    if local_time.hour < reset_hour:
        local_time -= timedelta(days=1)
    return local_time.date()
