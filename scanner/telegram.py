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


def esc(s) -> str:
    """HTML-escape. Nifty 50 contains M&M and BAJAJ-AUTO; an unescaped & breaks the send."""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


HOW_TO_READ = [
    "Meaning: price CLOSED beyond Candle 1's range — a wick poking out would not have counted.",
    "Score: a rule-based heuristic, not a probability. 75+ is strong, 50–74 mixed, under 50 weak.",
    "Base Rate is the honest one: what actually happened to past Signals like this. It outranks the score.",
    "Entry: do not chase. If price has already run past the entry, the Stop is further away than shown.",
    "Risk first: decide the rupees you are willing to lose before you look at the target.",
    "Breakeven: the win rate this trade needs just to make zero. Compare it to reality, not to hope.",
    "Size from the Stop, never from the Target — the risk_1pct row already does that arithmetic.",
    "Check the live chart: support and resistance, spread, sector and index direction.",
]


def format_signal(sig_row: dict, base_rate_label: str, sizes: list[tuple[str, float]],
                  session: dict | None = None, when: datetime | None = None,
                  score: tuple | None = None) -> str:
    """
    The alert.

    Ordered so the reader meets the evidence before the conclusion: the candles that
    formed the pattern, then the levels, then the heuristic score, then the Base Rate --
    which is the measured number and outranks the score whenever the two disagree.
    """
    sym = esc(sig_row["symbol"])
    buy = sig_row["direction"] == "BUY"
    dot = "\U0001F7E2" if buy else "\U0001F534"
    day = sig_row["day"]
    pretty_day = datetime.strptime(day, "%Y-%m-%d").strftime("%d-%b-%Y")

    L = ["%s <b>15-MIN SETUP SIGNAL</b>" % dot, "",
         "\U0001F4CA <b>%s</b>" % sym,
         "Direction: %s <b>%s</b>" % (dot, "BUY" if buy else "SELL")]
    L.append("Date: %s%s" % (pretty_day,
                             "   Time: %s IST" % when.strftime("%I:%M %p").lstrip("0") if when else ""))

    # ---- the candles that built the pattern -------------------------------------
    if session:
        rows = []
        for label, key in ([("C1", cfg.CANDLE_1)]
                           + [("C%d" % (i + 2), t) for i, t in enumerate(cfg.COIL_TIMES)]
                           + [("Break", sig_row["breakout_candle"])]):
            b = session.get(key)
            if b:
                rows.append("%-5s %s  O:%-9.2f H:%-9.2f L:%-9.2f C:%.2f"
                            % (label, key, b.open, b.high, b.low, b.close))
        if rows:
            L += ["", "── <b>Candles</b> ──", "<pre>" + esc("\n".join(rows)) + "</pre>"]

    # ---- levels: price, rupee distance and percentage, so nothing needs computing --
    entry = float(sig_row["entry"]) if sig_row.get("entry") else None
    stop = float(sig_row["stop"])
    stop_name = "Candle 1 %s" % ("Low" if buy else "High")
    L += ["", "── <b>Trade Levels</b> ──"]
    if entry:
        target = float(sig_row["target"])
        L += [
            "Entry: <b>₹%.2f</b>  (open of %s)" % (entry, sig_row["entry_candle"]),
            "Target 1%%: <b>₹%.2f</b>   ₹%.2f away" % (target, abs(target - entry)),
            "Stop (%s): <b>₹%.2f</b>   ₹%.2f away" % (stop_name, stop, abs(stop - entry)),
            "Stop distance %.2f%%  vs  Target %.2f%%   costs %.3f%%" % (
                float(sig_row["stop_pct"]), cfg.TARGET_PCT, float(sig_row["cost_pct"])),
        ]
    else:
        L += ["Stop (%s): <b>₹%.2f</b>" % (stop_name, stop),
              "Entry and Target: known once %s opens" % sig_row["entry_candle"]]

    # ---- the heuristic score ------------------------------------------------------
    if score:
        total, factors, label, action = score
        L += ["", "── <b>Confidence Score: %d/100</b> ──" % total]
        L += [esc(f.line()) for f in factors]
        L += ["", "Verdict: <b>%s</b>" % esc(label)]

    # ---- the measured number, which outranks the score ----------------------------
    L += ["", "── <b>Base Rate</b> (measured, not guessed) ──", esc(base_rate_label)]

    if sizes and entry:
        risk_ps = abs(stop - entry)
        rows = ["%-11s ₹%-9s %4d sh   risk ₹%s"
                % (r, format(round(n), ","), int(n // entry),
                   format(round(int(n // entry) * risk_ps), ","))
                for r, n in sizes]
        L += ["", "── <b>Position Size</b> ──", "<pre>" + esc("\n".join(rows)) + "</pre>"]

    if score:
        verb, reason, why = score[3]
        L += ["", "── <b>Suggested: %s</b> ──" % esc(verb), esc(why)]

    L += ["", "── <b>How to Read This</b> ──"]
    L += ["%d. %s" % (i + 1, esc(t)) for i, t in enumerate(HOW_TO_READ)]

    L += ["", "── <b>Your Pick</b> ──",
          "Reply <code>TAKE %s</code> or <code>SKIP %s &lt;reason&gt;</code> "
          "before <b>%s IST</b>." % (sym, sym,
                                     deadline(day, sig_row["entry_candle"]).strftime("%H:%M")),
          "Reasons: <code>%s</code>" % " ".join(REASON_CODES),
          "",
          "<i>Paper only — no order is placed. ADR-0006 gates real money. "
          "The score is a rule-based heuristic, not a probability.</i>"]
    return "\n".join(L)
