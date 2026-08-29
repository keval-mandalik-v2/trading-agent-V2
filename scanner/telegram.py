"""
Telegram: sending alerts, and collecting Picks in a way that cannot be backdated.

Why text replies and not inline buttons. GitHub Actions cannot host a webhook, so
updates must be polled with getUpdates -- which does deliver callback_query updates
from inline keyboards. But a callback_query carries no press timestamp; its
`message.date` is when the alert was sent, not when the button was tapped. That
makes the ADR-0004 deadline unverifiable.

A text reply carries `date`: a Telegram-server timestamp of when the user actually
sent it, which the user cannot forge. So Picks are text replies, and the deadline is
enforced against that timestamp -- not against when this workflow happened to run.
That is what keeps a late or dropped GitHub cron from silently widening the window.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta

import requests

from scanner import config as cfg

API = "https://api.telegram.org/bot%s/%s"

REPLY_RE = re.compile(r"^\s*(take|skip)\s+([A-Za-z0-9&\-]+)\s*(.*)$", re.IGNORECASE)

REASON_CODES = ("NO_CAPITAL", "LOW_CONVICTION", "WIDE_STOP", "MISSED", "OTHER")


class TelegramError(RuntimeError):
    pass


def _creds() -> tuple[str, str]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        raise TelegramError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")
    return token, chat


def enabled() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def send(text: str) -> int | None:
    """Post a message. Returns its id, or None when Telegram is not configured."""
    if not enabled():
        print("[telegram not configured, would have sent]\n%s\n" % text)
        return None
    token, chat = _creds()
    r = requests.post(
        API % (token, "sendMessage"),
        json={"chat_id": chat, "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": True},
        timeout=30,
    )
    if r.status_code != 200:
        raise TelegramError("sendMessage -> HTTP %s: %s" % (r.status_code, r.text[:300]))
    return r.json().get("result", {}).get("message_id")


def get_updates(offset: int | None) -> tuple[list[dict], int | None]:
    """
    Fetch new messages. Returns (replies, next_offset).

    Each reply is {symbol, answer, reason_code, sent_at (datetime, IST), raw}.
    """
    if not enabled():
        return [], offset
    token, _ = _creds()
    params = {"timeout": 0, "allowed_updates": '["message"]'}
    if offset is not None:
        params["offset"] = offset
    r = requests.get(API % (token, "getUpdates"), params=params, timeout=30)
    if r.status_code != 200:
        raise TelegramError("getUpdates -> HTTP %s: %s" % (r.status_code, r.text[:300]))

    replies, next_offset = [], offset
    for upd in r.json().get("result", []):
        next_offset = upd["update_id"] + 1
        msg = upd.get("message") or {}
        text = (msg.get("text") or "").strip()
        m = REPLY_RE.match(text)
        if not m:
            continue
        answer, symbol, tail = m.group(1).upper(), m.group(2).upper(), m.group(3).strip()
        reason = next((c for c in REASON_CODES if c in tail.upper()), "OTHER" if tail else "")
        replies.append({
            "symbol": symbol,
            "answer": answer,
            "reason_code": reason if answer == "SKIP" else "",
            # Telegram's own send-time, in IST to match candle labels
            "sent_at": datetime.fromtimestamp(msg["date"], cfg.IST).replace(tzinfo=None),
            "raw": text[:120],
        })
    return replies, next_offset


def deadline(day: str, entry_candle: str) -> datetime:
    """
    A Pick is valid only if sent before the entry candle closes.

    Defined purely from candle labels, so it is identical whether the workflow ran
    on time, twelve minutes late, or was skipped and caught up by the next run.
    """
    base = datetime.strptime("%s %s" % (day, entry_candle), "%Y-%m-%d %H:%M")
    return base + timedelta(minutes=cfg.CANDLE_MINUTES)


def format_signal(sig_row: dict, base_rate_label: str, sizes: list[tuple[str, float]]) -> str:
    """
    The alert. No confidence score (ADR-0002) -- raw measurements, the Breakeven Hit
    Rate this Trade has to clear, and a Base Rate that says "insufficient data" until
    it has earned the right to say anything else.
    """
    arrow = "\U0001F7E9 BUY" if sig_row["direction"] == "BUY" else "\U0001F7E5 SELL"
    lines = [
        "<b>%s  %s</b>" % (sig_row["symbol"], arrow),
        "%s  breakout candle %s → enter at %s open" % (
            sig_row["day"], sig_row["breakout_candle"], sig_row["entry_candle"]),
        "",
        "Candle 1     %.2f / %.2f   (%.2f%% wide)" % (
            float(sig_row["c1_high"]), float(sig_row["c1_low"]), float(sig_row["c1_width_pct"])),
        "Coil used    %.0f%% of Candle 1 range" % float(sig_row["coil_use_pct"]),
        "Breakout vol %.2fx the Coil average" % float(sig_row["breakout_vol_ratio"]),
        "Stop         %.2f" % float(sig_row["stop"]),
        "",
    ]
    if sig_row.get("stop_pct"):
        lines += [
            "Stop distance %.2f%%   vs Target %.2f%%" % (
                float(sig_row["stop_pct"]), cfg.TARGET_PCT),
            "<b>Breakeven Hit Rate: %.0f%%</b>  (incl. %.3f%% costs)" % (
                float(sig_row["breakeven_hit_rate"]), float(sig_row["cost_pct"])),
            "",
        ]
    else:
        lines += ["Stop distance: known once %s opens" % sig_row["entry_candle"], ""]

    lines.append("Base Rate: %s" % base_rate_label)
    if sizes:
        lines.append("")
        lines.append("Position size:")
        for rule, notional in sizes:
            lines.append("  %-12s ₹%s" % (rule, format(round(notional), ",")))
    lines += [
        "",
        "<i>Paper only — ADR-0006 gates real money.</i>",
        "Reply <code>TAKE %s</code> or <code>SKIP %s &lt;reason&gt;</code> before %s IST." % (
            sig_row["symbol"], sig_row["symbol"],
            deadline(sig_row["day"], sig_row["entry_candle"]).strftime("%H:%M")),
    ]
    return "\n".join(lines)
