from datetime import datetime


SESSIONS = {
    "asia": (0, 8),       # 00:00 - 08:00 UTC
    "london": (8, 16),    # 08:00 - 16:00 UTC
    "new_york": (13, 21), # 13:00 - 21:00 UTC
    "overlap": (13, 16),  # 13:00 - 16:00 UTC (London + NY)
}


def is_in_session(timestamp: datetime, session: str) -> bool:
    if session not in SESSIONS:
        raise ValueError(f"Unknown session '{session}'. Valid: {list(SESSIONS.keys())}")
    start_hour, end_hour = SESSIONS[session]
    hour = timestamp.hour
    return start_hour <= hour < end_hour


def is_trading_allowed(timestamp: datetime, allowed_sessions: list[str]) -> bool:
    if not allowed_sessions:
        return True
    return any(is_in_session(timestamp, s) for s in allowed_sessions)
