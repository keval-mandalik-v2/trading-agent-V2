"""
The Journal (ADR-0003) and the Base Rates drawn from it (ADR-0002).

Storage is CSV committed to the repository. That is not laziness: git gives an
append-only history with server-side timestamps, so a losing row cannot be quietly
edited without the change appearing in `git log`. ADR-0003 requires exactly that,
and ADR-0005 requires the research log to be append-only too.

A Signal moves through three states:

    SIGNALLED  alert sent; the entry candle has not opened yet, so no entry price
    OPEN       entry price recorded from the entry candle's open
    RESOLVED   Target, Stop or Fizzle recorded

Every write is keyed on (day, symbol), so re-running a workflow -- or catching up
after GitHub dropped a scheduled run -- rewrites the same row rather than adding one.
"""

from __future__ import annotations

import csv
import io
import json
import os
from datetime import datetime, timezone

import requests

from scanner import config as cfg

SIGNAL_FIELDS = [
    "day", "symbol", "direction", "breakout_candle", "entry_candle", "detected_at",
    "c1_high", "c1_low", "c1_width_pct", "coil_high", "coil_low", "coil_use_pct",
    "coil_avg_volume", "breakout_vol_ratio",
    "entry", "target", "stop", "stop_pct", "cost_pct", "breakeven_hit_rate",
    "status", "outcome", "realized_pct", "resolved_candle", "resolved_at",
]

PICK_FIELDS = [
    "day", "symbol", "answer", "reason_code", "answered_at",
    "deadline_candle", "valid", "raw",
]

SIGNALLED, OPEN, RESOLVED = "SIGNALLED", "OPEN", "RESOLVED"


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------ generic csv

def _read(path: str, fields: list[str]) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [dict(r) for r in csv.DictReader(fh)]


def _write(path: str, fields: list[str], rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


# ------------------------------------------------------------------ signals

def read_signals() -> list[dict]:
    return _read(cfg.SIGNALS_CSV, SIGNAL_FIELDS)


def upsert_signal(row: dict) -> bool:
    """Insert or update by (day, symbol). Returns True if this row is new."""
    rows = read_signals()
    key = (row["day"], row["symbol"])
    for i, existing in enumerate(rows):
        if (existing["day"], existing["symbol"]) == key:
            merged = dict(existing)
            merged.update({k: v for k, v in row.items() if v not in (None, "")})
            rows[i] = merged
            _write(cfg.SIGNALS_CSV, SIGNAL_FIELDS, rows)
            return False
    rows.append(row)
    rows.sort(key=lambda r: (r["day"], r["breakout_candle"], r["symbol"]))
    _write(cfg.SIGNALS_CSV, SIGNAL_FIELDS, rows)
    return True


def unresolved(day: str | None = None) -> list[dict]:
    return [
        r for r in read_signals()
        if r["status"] != RESOLVED and (day is None or r["day"] == day)
    ]


# ------------------------------------------------------------------ picks

def read_picks() -> list[dict]:
    return _read(cfg.PICKS_CSV, PICK_FIELDS)


def upsert_pick(row: dict) -> bool:
    """
    First answer wins. ADR-0004 makes a Pick immutable once recorded, so a second
    reply about the same Signal is ignored rather than overwriting the first.
    """
    rows = read_picks()
    key = (row["day"], row["symbol"])
    for existing in rows:
        if (existing["day"], existing["symbol"]) == key:
            return False
    rows.append(row)
    _write(cfg.PICKS_CSV, PICK_FIELDS, rows)
    return True


# ------------------------------------------------------------------ base rates

def stop_band(stop_pct: float) -> str:
    """
    The only Signal attribute that showed a clean monotonic gradient during design
    (ADR-0002). Bucketed rather than modelled, because 88 outcomes cannot support
    a model.
    """
    if stop_pct < 0.75:
        return "NARROW"
    if stop_pct <= 1.5:
        return "MID"
    return "WIDE"


def base_rate(direction: str, stop_pct: float) -> tuple[int, float | None, str]:
    """
    Realised expectancy of past Signals resembling this one, with its sample size.

    Deliberately refuses to answer below cfg.BASE_RATE_MIN_N. An honest
    "insufficient data" is the correct output for a system a few weeks old.
    """
    band = stop_band(stop_pct)
    hits = []
    for r in read_signals():
        if r["status"] != RESOLVED or not r.get("realized_pct"):
            continue
        if r["direction"] != direction:
            continue
        try:
            if stop_band(float(r["stop_pct"])) != band:
                continue
            hits.append(float(r["realized_pct"]) - float(r.get("cost_pct") or 0))
        except (TypeError, ValueError):
            continue
    n = len(hits)
    if n < cfg.BASE_RATE_MIN_N:
        return n, None, "insufficient data (n=%d, need %d)" % (n, cfg.BASE_RATE_MIN_N)
    mean = sum(hits) / n
    return n, mean, "%+.3f%% net over n=%d %s %s Signals" % (mean, n, band.lower(), direction)


# ------------------------------------------------------------------ universe

def refresh_universe() -> list[str]:
    """
    Pull the constituent list rather than hardcoding it. Index membership changes,
    and symbols get renamed or demerged -- TATAMOTORS-EQ stopped resolving during
    design, which is exactly the failure a hardcoded list hides until it matters.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.nseindia.com/",
    }
    r = requests.get(cfg.UNIVERSE_URL, headers=headers, timeout=30)
    r.raise_for_status()
    syms = [row["Symbol"].strip() for row in csv.DictReader(io.StringIO(r.text))]
    if len(syms) < 40:
        raise RuntimeError("constituent list looks wrong: %d symbols" % len(syms))
    _write(cfg.UNIVERSE_CSV, ["symbol"], [{"symbol": s} for s in syms])
    return syms


def load_universe() -> list[str]:
    """Cached list, refreshing if absent. Never fails the run over a stale list."""
    rows = _read(cfg.UNIVERSE_CSV, ["symbol"])
    if rows:
        return [r["symbol"] for r in rows]
    return refresh_universe()


# ------------------------------------------------------------------ state

def read_state() -> dict:
    if not os.path.exists(cfg.STATE_JSON):
        return {}
    with open(cfg.STATE_JSON, encoding="utf-8") as fh:
        return json.load(fh)


def write_state(state: dict) -> None:
    os.makedirs(cfg.DATA_DIR, exist_ok=True)
    with open(cfg.STATE_JSON, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)


# ------------------------------------------------------------------ research log

def log_hypothesis(title: str, result: str, variants_tried: int) -> None:
    """
    ADR-0005: append-only, failures included. A result without its trial count is
    not a result.
    """
    os.makedirs(os.path.dirname(cfg.RESEARCH_LOG), exist_ok=True)
    new = not os.path.exists(cfg.RESEARCH_LOG)
    with open(cfg.RESEARCH_LOG, "a", encoding="utf-8") as fh:
        if new:
            fh.write("# Research log\n\n")
            fh.write("Append-only (ADR-0005). Every hypothesis tested against the "
                     "Journal, failures included, with the number of variants tried "
                     "to reach it.\n\n")
        fh.write("## %s — %s\n\n" % (datetime.now(timezone.utc).date(), title))
        fh.write("- **Result:** %s\n" % result)
        fh.write("- **Variants tried to reach this:** %d\n\n" % variants_tried)
