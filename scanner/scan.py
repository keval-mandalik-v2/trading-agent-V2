"""
The scheduled run. Idempotent and catch-up safe by construction.

Nothing here reads the wall clock except to ask candles.latest_complete() which
candle has finished. Everything else is derived from data and from the Journal, so:

  * a cron that fires twelve minutes late sees identical candles and does identical work
  * a cron that GitHub drops entirely is caught up by the next run
  * running the same minute twice rewrites the same rows instead of duplicating them

Load control: the full 50-symbol sweep happens once per session, at the first run
after 10:15 closes, to find out who Coiled. Every later run in that session polls
only the Coiled symbols -- typically 6 or 7 of 50.

Usage:
    python -m scanner.scan                      # normal scheduled run
    python -m scanner.scan --at "2026-08-27 11:20"   # pretend it is this IST time
    python -m scanner.scan --no-telegram        # print alerts instead of sending
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

from scanner import candles as cd
from scanner import confidence
from scanner import config as cfg
from scanner import journal as jr
from scanner import money
from scanner import rules
from scanner import telegram as tg


def now_ist() -> datetime:
    return datetime.now(cfg.IST).replace(tzinfo=None)


def session_for(symbol: str, day: str, refresh: bool, days: int = 5) -> dict[str, cd.Bar]:
    """
    Cached candles for one symbol, topped up from the feed when asked.

    `days` is the lookback the feed is asked for. Five is plenty for today's session, but
    catching up a straggler from last week needs a wider window -- and the candle cache
    cannot be relied on to cover the gap, because it is gitignored and therefore empty on
    every fresh runner.
    """
    rows = cd.load(symbol)
    if refresh:
        try:
            rows = cd.merge(rows, cd.fetch(symbol, days=days))
            cd.save(symbol, rows)
        except Exception as exc:                     # one bad symbol must not kill the run
            print("  warn %s: %s: %s" % (symbol, type(exc).__name__, str(exc)[:80]))
    return cd.build_sessions(rows).get(day, {})


def find_coiled(universe: list[str], day: str, refresh: bool = True) -> list[str]:
    """The once-per-session full sweep."""
    coiled = []
    for i, sym in enumerate(universe):
        session = session_for(sym, day, refresh)
        coil, reason = rules.find_coil(session)
        if coil:
            coiled.append(sym)
        if refresh and i % 10 == 9:
            time.sleep(0.5)                      # be polite to an undocumented endpoint
    return coiled


def emit_signal(sym: str, day: str, session: dict, coil: rules.Coil,
                latest: str, when: datetime) -> tuple[dict | None, bool]:
    """Detect and persist. Alerting is the caller's job, so a Signal that is detected
    and entered in the same run produces one message rather than two."""
    sig, reason = rules.find_signal(sym, day, session, coil, through=latest)
    if sig is None:
        return None, False
    row = sig.as_row()
    row["status"] = jr.SIGNALLED
    row["detected_at"] = jr.now_utc()
    if not jr.upsert_signal(row):
        return None, False                           # already alerted on a previous run

    # ADR-0004's window is defined by candle labels, not by when this ran. If GitHub
    # fired us late or dropped runs, the window may already be shut -- so say so and
    # bank a NO RESPONSE, rather than inviting a reply that could never count.
    due = tg.deadline(day, sig.entry_candle)
    missed = when > due
    if missed:
        jr.upsert_pick({
            "day": day, "symbol": sym, "answer": "NO_RESPONSE",
            "reason_code": "WINDOW_MISSED",
            "answered_at": "", "deadline_candle": due.strftime("%H:%M"),
            "valid": "WINDOW_MISSED",
            "raw": "alerted %s IST, after the deadline" % when.strftime("%H:%M"),
        })

    return row, missed


def alert(row: dict, missed: bool, when: datetime, send: bool, session: dict) -> None:
    """One message per Signal, carrying whatever detail the data supports so far."""
    stop_pct = float(row["stop_pct"]) if row.get("stop_pct") else float(row["c1_width_pct"])
    n, mean, label = jr.base_rate(row["direction"], stop_pct)
    sizes = []
    if row.get("stop_pct"):
        # risk_1pct first: it is the recommended rule, and putting the ₹1,00,000
        # fixed_5x row underneath makes the difference in rupees-at-risk obvious.
        sizes = [(r, money.position_size(r, cfg.EQUITY, float(row["stop_pct"])))
                 for r in ("risk_1pct", "fixed_5x")]

    sc = None
    bar = session.get(row["breakout_candle"]) if session else None
    if bar and row.get("breakeven_hit_rate"):
        c1_high, c1_low = float(row["c1_high"]), float(row["c1_low"])
        box = c1_high - c1_low
        beyond = (bar.close - c1_high) if row["direction"] == rules.BUY else (c1_low - bar.close)
        sc = confidence.evaluate(
            breakeven_hit_rate=float(row["breakeven_hit_rate"]),
            breakout_close_beyond_pct=(beyond / box * 100) if box > 0 else 0.0,
            coil_use_pct=float(row["coil_use_pct"]),
            volume_ratio=float(row["breakout_vol_ratio"] or 0),
            turnover_rupees=bar.volume * bar.close,
        )

    text = tg.format_signal(row, label, sizes, session=session, when=when, score=sc)
    if missed:
        due = tg.deadline(row["day"], row["entry_candle"])
        text += ("\n\n⚠ Pick window closed at %s IST; this run happened at %s. "
                 "Recorded as NO RESPONSE." % (due.strftime("%H:%M"), when.strftime("%H:%M")))
    if send:
        tg.send(text)
    else:
        print(text + "\n")


def open_position(row: dict, session: dict) -> dict | None:
    """SIGNALLED -> OPEN once the entry candle has printed its open."""
    bar = session.get(row["entry_candle"])
    if bar is None:
        return None
    sig = rules.Signal(
        symbol=row["symbol"], day=row["day"], direction=row["direction"],
        breakout_candle=row["breakout_candle"], entry_candle=row["entry_candle"],
        stop=float(row["stop"]), stop_pct=None, target_pct=cfg.TARGET_PCT,
        breakout_vol_ratio=float(row["breakout_vol_ratio"] or 0),
        coil=rules.Coil(float(row["c1_high"]), float(row["c1_low"]),
                        float(row["c1_width_pct"]), float(row["coil_high"]),
                        float(row["coil_low"]), float(row["coil_use_pct"]),
                        float(row["coil_avg_volume"])),
    )
    lv = rules.levels(sig, bar.open)
    update = dict(row)
    update.update({k: "%.4f" % v for k, v in lv.items()})
    update["status"] = jr.OPEN
    jr.upsert_signal(update)
    return update


def settle(row: dict, session: dict, latest: str) -> str | None:
    """OPEN -> RESOLVED once Target, Stop or the square-off candle is reached."""
    sig = rules.Signal(
        symbol=row["symbol"], day=row["day"], direction=row["direction"],
        breakout_candle=row["breakout_candle"], entry_candle=row["entry_candle"],
        stop=float(row["stop"]), stop_pct=float(row["stop_pct"]),
        target_pct=cfg.TARGET_PCT, breakout_vol_ratio=0.0,
        coil=rules.Coil(0, 0, 0, 0, 0, 0, 0),
    )
    outcome = rules.resolve(session, sig, float(row["entry"]), through=latest)
    if outcome is None:
        return None
    update = dict(row)
    update.update({
        "status": jr.RESOLVED,
        "outcome": outcome.status,
        "realized_pct": "%.4f" % outcome.realized_pct,
        "resolved_candle": outcome.resolved_candle,
        "resolved_at": jr.now_utc(),
    })
    jr.upsert_signal(update)
    return outcome.status


def settle_stragglers(today: str, refresh: bool) -> int:
    """
    Resolve Signals left over from earlier sessions.

    The main loop only walks today's Coiled symbols, so a row that failed to resolve on
    its own day -- a truncated feed, a dropped run, a crash -- would otherwise sit at
    OPEN forever and disappear from every statistic the Journal produces. Those sessions
    are definitively over, so they settle with no ceiling on which candles may be read.
    """
    done = 0
    for row in jr.unresolved():
        if row["day"] >= today:
            continue
        session = session_for(row["symbol"], row["day"], refresh, days=40)
        if not session:
            continue                       # outside the feed's history window
        if row["status"] == jr.SIGNALLED:
            row = open_position(row, session) or row
        if row["status"] != jr.OPEN:
            continue
        got = settle(row, session, None)
        if got:
            print("  caught up %s %s -> %s" % (row["day"], row["symbol"], got))
            done += 1
    return done


def collect_picks(day: str) -> int:
    """
    Match Telegram replies to today's Signals and stamp them valid or late.

    Validity is judged against tg.deadline(), which is derived from candle labels --
    so a Pick cannot become valid merely because this workflow ran late.
    """
    state = jr.read_state()
    replies, next_offset = tg.get_updates(state.get("telegram_offset"))
    if next_offset is not None:
        state["telegram_offset"] = next_offset
        jr.write_state(state)

    todays = {r["symbol"]: r for r in jr.read_signals() if r["day"] == day}
    recorded = 0
    for rep in replies:
        sig = todays.get(rep["symbol"])
        if sig is None:
            continue
        due = tg.deadline(day, sig["entry_candle"])
        ok = jr.upsert_pick({
            "day": day,
            "symbol": rep["symbol"],
            "answer": rep["answer"],
            "reason_code": rep["reason_code"],
            "answered_at": rep["sent_at"].strftime("%Y-%m-%d %H:%M:%S"),
            "deadline_candle": due.strftime("%H:%M"),
            "valid": "yes" if rep["sent_at"] <= due else "LATE",
            "raw": rep["raw"],
        })
        recorded += 1 if ok else 0
    return recorded


def digest(day: str) -> str:
    rows = [r for r in jr.read_signals() if r["day"] == day]
    picks = {p["symbol"]: p for p in jr.read_picks() if p["day"] == day}
    total_resolved = [r for r in jr.read_signals() if r["status"] == jr.RESOLVED]
    lines = ["\U0001F4CA <b>Session digest %s</b>" % day, ""]
    if not rows:
        lines.append("No Coil produced a Signal today.")
    for r in rows:
        pick = picks.get(r["symbol"], {})
        lines.append("%-12s %-4s %-8s %s  pick=%s%s" % (
            r["symbol"], r["direction"], r.get("outcome") or r["status"],
            ("%+.2f%%" % float(r["realized_pct"])) if r.get("realized_pct") else "",
            pick.get("answer", "NO_RESPONSE"),
            " (LATE)" if pick.get("valid") == "LATE" else ""))
    lines += ["", "ADR-0006 progress: %d / %d resolved Signals" % (
        len(total_resolved), cfg.DECIDE_BY_TRADES)]
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="One scheduled scan of the Nifty 50 universe")
    p.add_argument("--at", help='pretend the IST time is this, e.g. "2026-08-27 11:20"')
    p.add_argument("--no-telegram", action="store_true", help="print alerts instead of sending")
    p.add_argument("--digest", action="store_true", help="force the session digest")
    p.add_argument("--offline", action="store_true",
                   help="use only the cached candles; do not call NSE")
    a = p.parse_args(argv)

    # Alerts carry emoji. Actions runners are UTF-8; a Windows console is cp1252 and
    # would abort the run on the first print rather than on anything that matters.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    when = datetime.strptime(a.at, "%Y-%m-%d %H:%M") if a.at else now_ist()
    day, latest = when.strftime("%Y-%m-%d"), cd.latest_complete(when)
    send = not a.no_telegram and tg.enabled()
    refresh = not a.offline
    print("run at %s IST -> newest complete candle %s" % (when.strftime("%Y-%m-%d %H:%M"), latest))

    if latest < cfg.COIL_TIMES[-1]:
        print("Coil window not finished yet; nothing to do.")
        return 0

    # Heartbeat. Until now a run that found nothing committed nothing, so a dropped
    # cron and a quiet cron looked identical -- which is precisely what let the feed
    # truncation go unnoticed for a week. Recording every run makes the schedule visible.
    hb = jr.read_state()
    hb["runs"] = ([when.strftime("%Y-%m-%d %H:%M")] + hb.get("runs", []))[:80]
    hb["last_run_ist"] = when.strftime("%Y-%m-%d %H:%M")
    jr.write_state(hb)

    caught = settle_stragglers(day, refresh)
    if caught:
        print("settled %d Signal(s) left over from earlier sessions" % caught)

    state = jr.read_state()
    universe = jr.load_universe()
    if refresh and state.get("universe_day") != day:
        try:
            universe = jr.refresh_universe()
            state["universe_day"] = day
        except Exception as exc:
            print("warn: constituent refresh failed, using cache: %s" % exc)

    coiled_key = "coiled_%s" % day
    if coiled_key not in state:
        print("full sweep of %d symbols to find Coils..." % len(universe))
        coiled = find_coiled(universe, day, refresh)
        if not coiled and not any(cd.build_sessions(cd.load(s)).get(day) for s in universe[:3]):
            print("no candles for %s -- market holiday. Nothing to do." % day)
            return 0
        state[coiled_key] = coiled
        jr.write_state(state)
    else:
        coiled = state[coiled_key]
    print("Coiled today: %d symbols %s" % (len(coiled), coiled))

    jr.write_state(state)

    for sym in coiled:
        session = session_for(sym, day, refresh)
        row = next((r for r in jr.read_signals()
                    if r["day"] == day and r["symbol"] == sym), None)

        # Advance as far as the data allows in this single run. One run must be able
        # to take a Signal all the way from detection to resolution, otherwise a
        # handful of dropped crons would leave positions stranded mid-state.
        fresh, missed = None, False
        if row is None:
            coil, reason = rules.find_coil(session)
            if coil is None:
                continue
            fresh, missed = emit_signal(sym, day, session, coil, latest, when)
            row = fresh
        if row is not None and row["status"] == jr.SIGNALLED:
            row = open_position(row, session) or row
        if fresh is not None:
            alert(row, missed, when, send, session)
        if row is not None and row["status"] == jr.OPEN:
            got = settle(row, session, latest)
            if got:
                print("  %s resolved: %s" % (sym, got))
        if refresh:
            time.sleep(0.3)

    picked = collect_picks(day)
    if picked:
        print("recorded %d Pick(s)" % picked)

    if a.digest or latest >= cfg.SQUARE_OFF_CANDLE:
        text = digest(day)
        if send:
            tg.send(text)
        else:
            print("\n" + text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
