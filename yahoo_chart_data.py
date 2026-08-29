"""
Fetch OHLC candles for NSE symbols from the Yahoo Finance chart API.

    GET https://query1.finance.yahoo.com/v8/finance/chart/<symbol>?interval=15m&range=1mo

No API key, no cookie/crumb, no bot wall -- just send a browser User-Agent.

    NSE equity   HDFCBANK.NS, RELIANCE.NS      (.BO for BSE)
    NSE indices  ^NSEI (NIFTY 50), ^NSEBANK (BANK NIFTY), ^INDIAVIX

>>> READ THIS BEFORE USING 15m DATA <<<

Yahoo's *daily* candles for NSE names are exact -- they match charting.nseindia.com
to the paisa. Yahoo's *intraday* candles do NOT. Measured on HDFCBANK 2026-08-27:

  * 0 of 25 15m candles matched NSE exactly, at any candle offset
  * mean |close| difference 0.68 (HDFCBANK) / 6.35 pts (NIFTY 50)
  * Yahoo's 15m bars do not even reconcile with Yahoo's OWN daily candle:
        Yahoo 1d (official)   O 728.15  H 728.15  L 710.00  C 711.00
        Yahoo 15m aggregated  O 726.20  H 726.75  L 710.00  C 711.00   <- misses the open
        NSE   15m aggregated  O 728.15  H 728.15  L 710.00  C 711.00   <- reconciles
    Yahoo's first 15m bar opens at 726.20, so it never sees the opening print
    that set both the day's open and its high.

  * 15m history is capped at ~24 sessions (range=1mo). range=3mo and beyond are
    rejected with "15m data not available" -- so no depth advantage over NSE either.

Conclusion: use this for daily bars or for cross-checking. For 15m candles that
agree with NSE, use nse_chart_data.py. Run --compare-nse to re-verify any day.

Usage:
    python yahoo_chart_data.py --symbol HDFCBANK.NS --interval 15m --range 5d
    python yahoo_chart_data.py --symbol ^NSEI --interval 1d --range 6mo --csv nifty_daily.csv
    python yahoo_chart_data.py --symbol HDFCBANK.NS --compare-nse HDFCBANK-EQ
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{}"
IST = ZoneInfo("Asia/Kolkata")
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)

INTERVALS = ["1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "1d", "5d", "1wk", "1mo"]
RANGES = ["1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"]

# intraday interval -> minutes, used to snap the live partial bar onto the grid
STEP = {"1m": 1, "2m": 2, "5m": 5, "15m": 15, "30m": 30, "60m": 60, "90m": 90, "1h": 60}


class YahooError(RuntimeError):
    pass


def fetch(symbol: str, interval: str = "15m", rng: str = "5d", timeout: int = 30):
    """Return (meta, [candle dicts]) with IST-localised candle START times."""
    r = requests.get(
        CHART.format(symbol),
        params={"interval": interval, "range": rng},
        headers={"User-Agent": UA, "Accept": "application/json"},
        timeout=timeout,
    )
    payload = r.json()
    chart = payload.get("chart") or {}
    if chart.get("error"):
        err = chart["error"]
        raise YahooError(f"{err.get('code')}: {err.get('description')}")
    if not chart.get("result"):
        raise YahooError(f"empty response (HTTP {r.status_code}): {str(payload)[:200]}")

    res = chart["result"][0]
    meta = res["meta"]
    stamps = res.get("timestamp") or []
    q = (res.get("indicators", {}).get("quote") or [{}])[0]
    if not stamps:
        return meta, []

    step = STEP.get(interval)
    rows = []
    for i, t in enumerate(stamps):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c):
            continue  # Yahoo pads gaps with nulls
        dt = datetime.fromtimestamp(t, IST).replace(tzinfo=None)
        if step:  # snap the live partial bar (e.g. 11:42) back onto the grid
            floor = (dt.hour * 60 + dt.minute) // step * step
            dt = dt.replace(hour=floor // 60, minute=floor % 60, second=0, microsecond=0)
        else:
            dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        rows.append(
            {
                "time": dt,
                "open": round(float(o), 4),
                "high": round(float(h), 4),
                "low": round(float(l), 4),
                "close": round(float(c), 4),
                "volume": int((q.get("volume") or [0] * len(stamps))[i] or 0),
            }
        )

    rows.sort(key=lambda r: r["time"])
    return meta, _merge(rows)


def _merge(rows):
    """Fold bars sharing a boundary (the snapped live bar) into one candle."""
    out = []
    for r in rows:
        if out and out[-1]["time"] == r["time"]:
            p = out[-1]
            p.update(
                high=max(p["high"], r["high"]),
                low=min(p["low"], r["low"]),
                close=r["close"],
                volume=p["volume"] + r["volume"],
            )
        else:
            out.append(dict(r))
    return out


def compare_with_nse(y_symbol: str, nse_symbol: str, interval: str, rng: str) -> int:
    """Re-run the accuracy check that produced the warning in this module's docstring."""
    try:
        from nse_chart_data import NseCharting
    except ImportError:
        print("error: nse_chart_data.py must sit next to this file", file=sys.stderr)
        return 1

    nse_interval = {"15m": "15", "5m": "5", "1m": "1", "30m": "30",
                    "60m": "60", "1h": "60", "1d": "1D"}.get(interval)
    if not nse_interval:
        print(f"error: no NSE equivalent for interval {interval!r}", file=sys.stderr)
        return 1

    _, yrows = fetch(y_symbol, interval, rng)
    _, ncandles = NseCharting().candles(nse_symbol, nse_interval, days=30)

    y = {r["time"]: (round(r["open"], 2), round(r["high"], 2),
                     round(r["low"], 2), round(r["close"], 2)) for r in yrows}
    n = {c.time: (round(c.open, 2), round(c.high, 2),
                  round(c.low, 2), round(c.close, 2)) for c in ncandles}
    common = sorted(set(y) & set(n))
    if not common:
        print("no overlapping candles to compare", file=sys.stderr)
        return 1

    exact = sum(y[t] == n[t] for t in common)
    close_err = [abs(y[t][3] - n[t][3]) for t in common]
    print(f"\n{y_symbol} (Yahoo) vs {nse_symbol} (NSE) @ {interval}")
    print(f"  overlapping candles : {len(common)}")
    print(f"  identical OHLC      : {exact}  ({exact / len(common) * 100:.1f}%)")
    print(f"  mean |close diff|   : {sum(close_err) / len(close_err):.4f}")
    print(f"  max  |close diff|   : {max(close_err):.4f}")

    print("\n  worst 5 candles:")
    for t in sorted(common, key=lambda t: -abs(y[t][3] - n[t][3]))[:5]:
        print(f"    {t:%Y-%m-%d %H:%M}  yahoo C {y[t][3]:>10,.2f}   nse C {n[t][3]:>10,.2f}"
              f"   diff {y[t][3] - n[t][3]:+.2f}")

    verdict = "interchangeable" if exact == len(common) else "NOT interchangeable"
    print(f"\n  verdict: {verdict}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Yahoo Finance candles for NSE symbols",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="NOTE: Yahoo daily data matches NSE exactly; Yahoo 15m data does NOT. "
               "See the module docstring, or run --compare-nse.",
    )
    p.add_argument("--symbol", default="^NSEI", help="HDFCBANK.NS, RELIANCE.NS, ^NSEI ...")
    p.add_argument("--interval", default="15m", choices=INTERVALS)
    p.add_argument("--range", dest="rng", default="5d", choices=RANGES)
    p.add_argument("--limit", type=int, help="keep only the N most recent candles")
    p.add_argument("--csv", help="write candles to this CSV path")
    p.add_argument("--asc", action="store_true", help="oldest first")
    p.add_argument("--compare-nse", metavar="NSE_SYMBOL",
                   help="diff against nse_chart_data.py, e.g. HDFCBANK-EQ")
    a = p.parse_args(argv)

    try:
        if a.compare_nse:
            return compare_with_nse(a.symbol, a.compare_nse, a.interval, a.rng)
        meta, rows = fetch(a.symbol, a.interval, a.rng)
    except YahooError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except requests.RequestException as e:
        print(f"network error: {e}", file=sys.stderr)
        return 1

    if not rows:
        print("no candles returned", file=sys.stderr)
        return 1
    if a.limit:
        rows = rows[-a.limit:]

    print(f"\n{meta.get('symbol')} - {meta.get('fullExchangeName', '?')} "
          f"({meta.get('exchangeTimezoneName')})")
    print(f"{len(rows)} candles - {a.interval} over {a.rng} - times are IST candle START")
    if a.interval in STEP:
        print("WARNING: Yahoo intraday bars for NSE disagree with NSE's own data "
              "-- see the module docstring")

    head = f"\n{'Date - ' + a.interval:<20} {'Open':>11} {'High':>11} {'Low':>11} {'Close':>11} {'Volume':>14}"
    print(head)
    print("-" * (len(head) - 1))
    for r in (rows if a.asc else reversed(rows)):
        print(f"{r['time']:%a %d %b %y %H:%M}    {r['open']:>11,.2f} {r['high']:>11,.2f} "
              f"{r['low']:>11,.2f} {r['close']:>11,.2f} {r['volume']:>14,}")

    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["time", "open", "high", "low", "close", "volume"])
            w.writeheader()
            for r in rows:
                w.writerow({**r, "time": r["time"].strftime("%Y-%m-%d %H:%M")})
        print(f"\nwrote {len(rows)} rows -> {a.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
