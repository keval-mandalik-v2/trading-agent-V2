"""
Probe whether the NSE endpoints answer from wherever this is running.

Run this FIRST on a GitHub Actions runner. Everything else in this repo assumes
charting.nseindia.com will talk to us; datacenter IPs are the most likely thing
to break that assumption, and it fails as a timeout or a 403 rather than as
anything obvious.

Usage:
    python tools/probe_reachability.py
Exit code 0 = both endpoints usable, 1 = something is blocked.
"""

from __future__ import annotations

import sys
import time

import requests

sys.path.insert(0, ".")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)


def probe_universe() -> bool:
    url = "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv"
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Referer": "https://www.nseindia.com/"}, timeout=30)
    except requests.RequestException as e:
        print("FAIL  constituent list -> %s: %s" % (type(e).__name__, e))
        return False
    ok = r.status_code == 200 and "Symbol" in r.text
    print("%s  constituent list -> HTTP %s, %d bytes, %d rows"
          % ("OK  " if ok else "FAIL", r.status_code, len(r.content), r.text.count("\n")))
    return ok


def probe_candles() -> bool:
    from nse_chart_data import NseCharting, NseChartError

    t0 = time.time()
    try:
        info, candles = NseCharting().candles("HDFCBANK-EQ", "15", 5)
    except (NseChartError, requests.RequestException) as e:
        print("FAIL  candle feed -> %s: %s" % (type(e).__name__, e))
        return False
    ok = len(candles) > 0
    print("%s  candle feed -> %s, %d candles in %.1fs, latest %s"
          % ("OK  " if ok else "FAIL", info["symbol"], len(candles), time.time() - t0,
             candles[-1].time if candles else "-"))
    return ok


def main() -> int:
    print("probing NSE reachability from this host\n")
    a = probe_universe()
    b = probe_candles()
    print()
    if a and b:
        print("VERDICT: both endpoints reachable. The scanner can run here.")
        return 0
    print("VERDICT: blocked. Do not build on GitHub-hosted runners --")
    print("         use a self-hosted runner on a residential connection instead.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
