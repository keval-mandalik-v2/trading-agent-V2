"""
Send a sample alert for a past session, without touching the Journal.

Two things make a live replay useless as a test. Journal writes are keyed on
(day, symbol), so re-running a session that has already been recorded produces no
alert at all -- and if it did fire, it would write fictional rows into a notebook
whose whole value is that every row is real.

This builds the message from real candles through the real rule engine and sends it,
then stops. Nothing is recorded, no Pick window opens, and the message carries a TEST
banner so it can never be mistaken for a live Signal.

Usage:
    python tools/preview_alert.py                        # newest session in the feed
    python tools/preview_alert.py --day 2026-09-04
    python tools/preview_alert.py --day 2026-09-04 --symbol LT
    python tools/preview_alert.py --limit 3 --print      # render locally, send nothing
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanner import candles as cd
from scanner import config as cfg
from scanner import confidence
from scanner import journal as jr
from scanner import money
from scanner import rules
from scanner import telegram as tg

BANNER = ("\U0001F9EA <b>TEST MESSAGE — NOT A LIVE SIGNAL</b>\n"
          "Replay of a past session to check formatting and delivery. "
          "Nothing was recorded and no Pick is expected.\n" + "─" * 24 + "\n")


def build(sym: str, day: str, session: dict) -> str | None:
    coil, _ = rules.find_coil(session)
    if coil is None:
        return None
    sig, _ = rules.find_signal(sym, day, session, coil)
    if sig is None:
        return None
    entry = rules.entry_price(session, sig)
    if entry is None:
        return None

    lv = rules.levels(sig, entry)
    row = sig.as_row()
    row.update({k: "%.4f" % v for k, v in lv.items()})
    row["status"] = "OPEN"

    bar = session[sig.breakout_candle]
    box = coil.c1_high - coil.c1_low
    beyond = ((bar.close - coil.c1_high) if sig.direction == rules.BUY
              else (coil.c1_low - bar.close))
    score = confidence.evaluate(
        breakeven_hit_rate=lv["breakeven_hit_rate"],
        breakout_close_beyond_pct=(beyond / box * 100) if box > 0 else 0.0,
        coil_use_pct=coil.coil_use_pct,
        volume_ratio=sig.breakout_vol_ratio,
        turnover_rupees=bar.volume * bar.close,
    )
    sizes = [(r, money.position_size(r, cfg.EQUITY, lv["stop_pct"]))
             for r in ("risk_1pct", "fixed_5x")]

    # The alert normally goes out one candle after the breakout; show that time, not now.
    sent = datetime.strptime("%s %s" % (day, sig.entry_candle), "%Y-%m-%d %H:%M")
    _, _, label = jr.base_rate(sig.direction, lv["stop_pct"])
    return BANNER + tg.format_signal(row, label, sizes, session=session,
                                     when=sent, score=score)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Send a sample alert from a past session")
    p.add_argument("--day", help="IST session date, YYYY-MM-DD (default: newest available)")
    p.add_argument("--symbol", help="only this symbol")
    p.add_argument("--limit", type=int, default=2, help="how many alerts to send")
    p.add_argument("--print", dest="show", action="store_true", help="render locally, send nothing")
    a = p.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    universe = [a.symbol] if a.symbol else jr.load_universe()
    print("scanning %d symbol(s) for a session to preview..." % len(universe))

    sessions: dict[str, dict] = {}
    for sym in universe:
        try:
            sessions[sym] = cd.build_sessions(cd.merge(cd.load(sym), cd.fetch(sym, 15)))
        except Exception as exc:
            print("  warn %s: %s" % (sym, str(exc)[:70]))

    day = a.day
    if not day:
        days = sorted({d for s in sessions.values() for d in s})
        if not days:
            print("no sessions available from the feed.")
            return 1
        day = days[-1]
    print("session: %s" % day)

    sent = 0
    for sym, by_day in sessions.items():
        if sent >= a.limit:
            break
        session = by_day.get(day)
        if not session:
            continue
        msg = build(sym, day, session)
        if msg is None:
            continue
        sent += 1
        if a.show or not tg.enabled():
            print("\n" + msg + "\n")
        else:
            tg.send(msg)
            print("  sent: %s" % sym)

    if sent == 0:
        print("no Signal formed on %s in this universe -- try another date." % day)
        return 1
    print("\ndone: %d sample alert(s). The Journal was not touched." % sent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
