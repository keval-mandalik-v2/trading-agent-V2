"""
The session watcher: one long-lived job that does its own 15-minute timing.

Measured on this repository, GitHub's `schedule:` fired 2 of 24 requested runs per day --
with sequential run numbers, so the other 22 events were never created at all, no error
and nothing cancelled. Scheduled events are best-effort and heavily deprioritised, and no
amount of cron tuning changes that.

So timing does not come from the scheduler any more. It comes from here: one job starts in
the morning and sleeps to each candle boundary itself, which is exact. The scheduler now
only has to be roughly right once a day, and a thirty-minute delay costs the first candle
or two instead of the entire session.

A GitHub-hosted job may run for six hours. 10:31 to 15:46 IST is 5h15m, which fits with
half an hour to spare.

Usage:
    python -m scanner.watch                 # loop until the session ends
    python -m scanner.watch --until 12:00   # stop earlier (testing)
    python -m scanner.watch --once          # a single pass, then exit
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta

from scanner import config as cfg
from scanner import scan

# A candle labelled T closes at T+15. Wait a little past that before asking the feed for
# it -- the exchange's own aggregation is not instant, and a bar read too early is a bar
# read wrong.
SETTLE_SECONDS = 75

# The 15:15 candle closes at 15:30. This is late enough to settle every position and send
# the digest, and early enough to stay inside the six-hour job ceiling.
SESSION_END = "15:46"


def now_ist() -> datetime:
    return datetime.now(cfg.IST).replace(tzinfo=None)


def next_boundary(now: datetime) -> datetime:
    """The next candle close, plus the settle delay."""
    minute = (now.hour * 60 + now.minute) // cfg.CANDLE_MINUTES * cfg.CANDLE_MINUTES
    close = now.replace(hour=minute // 60, minute=minute % 60, second=0, microsecond=0)
    target = close + timedelta(minutes=cfg.CANDLE_MINUTES, seconds=SETTLE_SECONDS)
    while target <= now:
        target += timedelta(minutes=cfg.CANDLE_MINUTES)
    return target


def git(*args: str) -> tuple[int, str]:
    p = subprocess.run(("git",) + args, capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def commit_journal() -> None:
    """
    Persist after every pass, not once at the end.

    A six-hour job that commits only on exit loses the whole session to one crash, one
    timeout, or one cancelled workflow.
    """
    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    git("add", "-A", "data/")
    code, _ = git("diff", "--cached", "--quiet")
    if code == 0:
        return                                     # nothing changed this pass
    git("commit", "-m", "journal: %s IST" % now_ist().strftime("%Y-%m-%d %H:%M"))
    for attempt in range(3):
        git("pull", "--rebase", "--autostash", "origin", "HEAD")
        code, out = git("push", "origin", "HEAD")
        if code == 0:
            return
        print("  push attempt %d failed: %s" % (attempt + 1, out[:120]))
        time.sleep(5)
    print("  WARNING: could not push the Journal; it will go up with the next pass")


def one_pass(argv: list[str]) -> None:
    """
    Run the scanner once. Never let a single bad pass end the session.

    A transient NSE timeout or a Telegram hiccup at 11:00 must not cost you the 11:15,
    11:30 and 11:45 Signals as well.
    """
    try:
        scan.main(argv)
    except Exception:
        print("  pass failed, continuing:")
        traceback.print_exc(limit=3)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Watch one trading session end to end")
    p.add_argument("--until", default=SESSION_END, help="stop after this IST time (HH:MM)")
    p.add_argument("--once", action="store_true", help="a single pass, then exit")
    p.add_argument("--no-commit", action="store_true", help="do not commit the Journal")
    p.add_argument("--no-telegram", action="store_true", help="print alerts instead of sending")
    a = p.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    passthrough = ["--no-telegram"] if a.no_telegram else []
    start = now_ist()
    end = start.replace(hour=int(a.until[:2]), minute=int(a.until[3:]),
                        second=0, microsecond=0)

    print("watcher up at %s IST, running until %s IST" % (start.strftime("%H:%M:%S"), a.until))

    if a.once:
        one_pass(passthrough)
        if not a.no_commit:
            commit_journal()
        return 0

    if start >= end:
        # A backup cron that queued behind the real run, arriving after the close.
        print("session already over; nothing to watch.")
        return 0

    passes = 0
    while True:
        now = now_ist()
        if now >= end:
            break
        # The Coil window closes at 10:30. Before then there is nothing to look at, so
        # wait rather than hammering the feed for candles that do not exist yet.
        if now.strftime("%H:%M") >= cfg.BREAKOUT_WINDOW[0]:
            passes += 1
            print("\n──── pass %d at %s IST ────" % (passes, now.strftime("%H:%M:%S")))
            one_pass(passthrough)
            if not a.no_commit:
                commit_journal()

        nxt = min(next_boundary(now_ist()), end)
        wait = (nxt - now_ist()).total_seconds()
        if wait <= 0:
            continue
        print("  sleeping %d min %02d s until %s IST" % (wait // 60, wait % 60,
                                                         nxt.strftime("%H:%M:%S")))
        time.sleep(wait)

    print("\nsession over at %s IST after %d passes." % (now_ist().strftime("%H:%M:%S"), passes))
    # One last pass: the 15:15 candle has closed, so everything still open settles and the
    # digest goes out.
    one_pass(passthrough + ["--digest"])
    if not a.no_commit:
        commit_journal()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
