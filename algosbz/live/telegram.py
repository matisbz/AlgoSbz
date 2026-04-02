"""
Telegram notifier for AlgoSbz live trading.

Sends notifications on trade open/close and periodic heartbeats.
All calls are non-blocking (fire-and-forget in a background thread)
so they never delay the trading loop.
"""
import logging
import threading
import urllib.request
import urllib.parse
import json
from datetime import datetime

logger = logging.getLogger(__name__)

BOT_TOKEN = "8796677794:AAEISFqMxskpnk4f5_h1SN-cjb9yt9-R7KI"
CHAT_ID = "646733371"
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"


def _send(text: str):
    """Send a Telegram message (blocking). Called from background thread."""
    try:
        payload = urllib.parse.urlencode({
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
        }).encode()
        req = urllib.request.Request(API_URL, data=payload)
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        logger.warning("Telegram send failed: %s", e)


def send(text: str):
    """Fire-and-forget Telegram message (non-blocking)."""
    threading.Thread(target=_send, args=(text,), daemon=True).start()


def notify_trade_opened(account: str, direction: str, combo: str,
                        volume: float, fill_price: float,
                        sl: float, tp: float | None, state: str):
    arrow = "\U0001f7e2" if direction == "BUY" else "\U0001f534"
    tp_str = f"{tp:.5f}" if tp else "None"
    text = (
        f"{arrow} <b>TRADE OPENED</b>\n"
        f"Account: {account} ({state})\n"
        f"{direction} <b>{combo}</b>\n"
        f"Volume: {volume} lots @ {fill_price:.5f}\n"
        f"SL: {sl:.5f} | TP: {tp_str}"
    )
    send(text)


def notify_trade_closed(account: str, combo: str, pnl: float,
                        ticket: int, equity: float):
    icon = "\u2705" if pnl >= 0 else "\u274c"
    text = (
        f"{icon} <b>TRADE CLOSED</b>\n"
        f"Account: {account}\n"
        f"Combo: <b>{combo}</b>\n"
        f"PnL: ${pnl:+,.2f}\n"
        f"Equity: ${equity:,.2f}"
    )
    send(text)


def notify_heartbeat(accounts: list[dict]):
    """Periodic status update."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"\U0001f493 <b>AlgoSbz Heartbeat</b> — {now}\n"]
    for acct in accounts:
        profit_pct = (acct["equity"] - acct["initial"]) / acct["initial"] * 100
        lines.append(
            f"<b>{acct['name']}</b> ({acct['state']})\n"
            f"  Equity: ${acct['equity']:,.2f} ({profit_pct:+.1f}%)\n"
            f"  Trades: {acct['trades']} | Open: {acct['open']}"
        )
    send("\n".join(lines))


def notify_dd_breach(account: str, dd_type: str, dd_pct: float):
    text = (
        f"\U0001f6a8 <b>DD BREACH — {dd_type.upper()}</b>\n"
        f"Account: {account}\n"
        f"DD: {dd_pct:.2f}%\n"
        f"Closing all positions!"
    )
    send(text)


def notify_startup(num_accounts: int, num_combos: int, mode: str):
    text = (
        f"\U0001f680 <b>AlgoSbz Started</b>\n"
        f"Mode: {mode}\n"
        f"Accounts: {num_accounts}\n"
        f"Combos: {num_combos}"
    )
    send(text)
