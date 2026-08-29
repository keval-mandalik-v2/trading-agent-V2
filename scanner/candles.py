"""
Candle fetching, caching and session assembly -- plus the two data defects this
feed actually exhibits.

Defect 1: the same query minutes apart can return a different number of candles.
Defect 2: NSE sometimes appends a closing-session aggregate bar whose timestamp
          floors into the 15:15 slot and whose volume is the WHOLE DAY volume
          (observed: HDFCBANK 15:15 carrying 46,184,640 against a normal ~2,000,000).
          Left in place it corrupts any volume comparison.

Both are handled in build_sessions(): duplicate timestamps keep the FIRST bar seen,
and any bar whose volume exceeds the sum of every other bar in its session is
discarded as an aggregate.
"""

from __future__ import annotations

import csv
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanner import config as cfg


@dataclass(frozen=True)
class Bar:
    open: float
    high: float
    low: float
    close: float
    volume: int


def fetch(symbol: str, days: int = 45) -> list[tuple[datetime, Bar]]:
    """Pull 15m candles from charting.nseindia.com via the existing client."""
    from nse_chart_data import NseCharting

    _, candles = NseCharting().candles(symbol + "-EQ", "15", days)
    return [(c.time, Bar(c.open, c.high, c.low, c.close, c.volume)) for c in candles]


def cache_path(symbol: str) -> str:
    return os.path.join(cfg.CANDLE_DIR, symbol + ".csv")


def save(symbol: str, rows: list[tuple[datetime, Bar]]) -> None:
    os.makedirs(cfg.CANDLE_DIR, exist_ok=True)
    with open(cache_path(symbol), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["time", "open", "high", "low", "close", "volume"])
        for t, b in rows:
            w.writerow([t.strftime("%Y-%m-%d %H:%M"), b.open, b.high, b.low, b.close, b.volume])


def load(symbol: str) -> list[tuple[datetime, Bar]]:
    path = cache_path(symbol)
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out.append((
                datetime.strptime(r["time"], "%Y-%m-%d %H:%M"),
                Bar(float(r["open"]), float(r["high"]), float(r["low"]),
                    float(r["close"]), int(float(r["volume"]))),
            ))
    return out


def merge(existing: list, fresh: list) -> list:
    """Union by timestamp, preferring the bar already on disk (defect 1)."""
    seen = {t: b for t, b in fresh}
    seen.update({t: b for t, b in existing})
    return sorted(seen.items(), key=lambda kv: kv[0])


def build_sessions(rows: list[tuple[datetime, Bar]]) -> dict[str, dict[str, Bar]]:
    """Group bars into sessions, dropping duplicates and aggregate bars."""
    raw: dict[str, dict[str, Bar]] = defaultdict(dict)
    for when, bar in rows:
        day, hhmm = when.strftime("%Y-%m-%d"), when.strftime("%H:%M")
        if hhmm in raw[day]:
            continue                    # defects 1 and 2: keep the first, discard this
        raw[day][hhmm] = bar

    clean: dict[str, dict[str, Bar]] = {}
    for day, session in raw.items():
        total = sum(b.volume for b in session.values())
        clean[day] = {
            k: b for k, b in session.items()
            if b.volume == 0 or b.volume <= total - b.volume
        }
    return clean


def floor_candle(when: datetime) -> str:
    minute = (when.hour * 60 + when.minute) // cfg.CANDLE_MINUTES * cfg.CANDLE_MINUTES
    return "%02d:%02d" % (minute // 60, minute % 60)


def latest_complete(now_ist: datetime) -> str:
    """
    The newest candle that has finished.

    Candle T covers [T, T+15) and is complete only at T+15, so at 11:03 the newest
    complete candle is 10:45. This is the ONLY clock the scanner consults, which is
    why a GitHub Actions cron firing twelve minutes late is harmless.
    """
    return floor_candle(now_ist - timedelta(minutes=cfg.CANDLE_MINUTES))


def next_candle(hhmm: str) -> str | None:
    """The candle label after this one within the session, or None past the close."""
    order = (cfg.CANDLE_1,) + cfg.COIL_TIMES + cfg.SESSION_CANDLES
    try:
        i = order.index(hhmm)
    except ValueError:
        return None
    return order[i + 1] if i + 1 < len(order) else None
