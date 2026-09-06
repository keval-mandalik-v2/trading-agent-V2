"""
Fetch OHLC candle data from https://charting.nseindia.com

This talks to the exact same backend the site's "Table view" renders from, so the
numbers are identical to what you see on screen -- no browser automation needed.

Endpoints (discovered inside charting_assets/static/js/main.*.js):
    GET /v1/exchanges/symbolsDynamic?symbol=<q>&segment=
        -> resolves a symbol to its scripcode ("token") and type
    GET /v1/charts/symbolHistoricalData
            ?token=&fromDate=&toDate=&symbol=&symbolType=&chartType=&timeInterval=
        -> the candles

Timestamp convention (important):
    The API returns epoch-ms whose *naive UTC* rendering is already the IST wall
    clock, sitting ~4m59s into the candle (e.g. 1787843999000 -> "15:19:59" for
    the 15:15 candle). Flooring to the interval boundary recovers the candle
    start time exactly as the Table view labels it.

Usage:
    python nse_chart_data.py                                   # NIFTY 50, 15m, last 5 sessions
    python nse_chart_data.py --symbol HDFCBANK-EQ --interval 15
    python nse_chart_data.py --symbol "NIFTY 50" --days 30 --csv nifty_15m.csv
    python nse_chart_data.py --search RELIANCE                  # look up a symbol name
    python nse_chart_data.py --interval 1D --days 200
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

import requests

BASE = "https://charting.nseindia.com"
EPOCH = datetime(1970, 1, 1)

# The API's epochs are the IST wall clock rendered as if it were UTC, and it filters
# fromDate/toDate in that same shifted space. A true-UTC "now" is therefore 5h30m
# behind the API's "now", which silently truncates every candle whose IST time is
# later than the current UTC time-of-day. During market hours that is the entire
# session: at 10:47 IST (05:17 UTC) even the 09:15 candle is filtered out.
IST_SHIFT = 5 * 3600 + 30 * 60

# resolution -> (chartType, timeInterval), mirrors the site's own mapping
RESOLUTIONS: dict[str, tuple[str, int]] = {
    "1": ("I", 1),
    "5": ("I", 5),
    "15": ("I", 15),
    "30": ("I", 30),
    "60": ("I", 60),
    "1D": ("D", 1),
    "1W": ("W", 1),
    "1M": ("M", 1),
}

INTRADAY_MINUTES = {"1": 1, "5": 5, "15": 15, "30": 30, "60": 60}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE + "/",
    "Origin": BASE,
}


class NseChartError(RuntimeError):
    pass


@dataclass
class Candle:
    """One candle, labelled with its start time in IST (naive datetime)."""

    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int

    def as_row(self) -> dict:
        row = asdict(self)
        row["time"] = self.time.strftime("%Y-%m-%d %H:%M")
        return row


class NseCharting:
    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        # warm the session so we hold whatever cookies the edge hands out
        try:
            self.s.get(BASE + "/", timeout=timeout)
        except requests.RequestException:
            pass

    def _get(self, path: str, params: dict) -> dict:
        r = self.s.get(BASE + path, params=params, timeout=self.timeout)
        if r.status_code != 200:
            raise NseChartError("%s -> HTTP %s: %s" % (path, r.status_code, r.text[:200]))
        try:
            return r.json()
        except json.JSONDecodeError:
            raise NseChartError("%s -> non-JSON response: %s" % (path, r.text[:200]))

    # ---------------------------------------------------------------- symbols

    def search_symbols(self, query: str, segment: str = "") -> list[dict]:
        payload = self._get(
            "/v1/exchanges/symbolsDynamic", {"symbol": query, "segment": segment}
        )
        if not payload.get("status"):
            raise NseChartError("symbol search failed for %r: %s" % (query, payload))
        return payload.get("data") or []

    def resolve(self, symbol: str) -> dict:
        """Exact-match a symbol, else fall back to the first search hit."""
        hits = self.search_symbols(symbol)
        if not hits:
            raise NseChartError(
                "no symbol matches %r -- use --search to find the right name" % symbol
            )
        for h in hits:
            if h["symbol"].upper() == symbol.upper():
                return h
        return hits[0]

    # ---------------------------------------------------------------- candles

    def candles(
        self,
        symbol: str,
        interval: str = "15",
        days: int | None = None,
        merge: bool = True,
    ) -> tuple[dict, list[Candle]]:
        if interval not in RESOLUTIONS:
            raise NseChartError(
                "interval %r not supported; pick one of %s"
                % (interval, ", ".join(RESOLUTIONS))
            )

        info = self.resolve(symbol)
        chart_type, time_interval = RESOLUTIONS[interval]

        # ...which is why "now" is shifted into the API's space before being sent.
        now = int(datetime.now(timezone.utc).timestamp()) + IST_SHIFT
        from_date = 0
        if days:
            # the API filters in the same shifted epoch space that it returns
            from_date = now - days * 86400

        payload = self._get(
            "/v1/charts/symbolHistoricalData",
            {
                "token": info["scripcode"],
                "fromDate": from_date,
                "toDate": now,
                "symbol": info["symbol"],
                "symbolType": info["type"],  # Equity | Index | Future and Options
                "chartType": chart_type,
                "timeInterval": time_interval,
            },
        )
        if not payload.get("status"):
            raise NseChartError("historical data request rejected: %s" % payload)

        return info, self._parse(payload.get("data"), interval, merge)

    @staticmethod
    def _parse(data, interval: str, merge: bool = True) -> list[Candle]:
        """Accept both response shapes the site's datafeed handles."""
        if not data:
            return []

        if isinstance(data, dict) and "t" in data:  # columnar form
            vols = data.get("v") or [0] * len(data["t"])
            rows = [
                {
                    "time": data["t"][i] * 1000,
                    "open": data["o"][i],
                    "high": data["h"][i],
                    "low": data["l"][i],
                    "close": data["c"][i],
                    "volume": vols[i],
                }
                for i in range(len(data["t"]))
            ]
        else:  # list-of-objects form
            rows = data

        step = INTRADAY_MINUTES.get(interval)

        # stamp every raw bar with its candle-start boundary
        staged = []
        for r in rows:
            # the naive-UTC rendering of the epoch IS the IST wall clock
            raw = EPOCH + timedelta(seconds=r["time"] / 1000)
            if step:
                floor = (raw.hour * 60 + raw.minute) // step * step
                stamp = raw.replace(
                    hour=floor // 60, minute=floor % 60, second=0, microsecond=0
                )
            else:
                stamp = raw.replace(hour=0, minute=0, second=0, microsecond=0)
            staged.append([raw, stamp, dict(r)])
        staged.sort(key=lambda x: x[0])

        if step:
            NseCharting._decumulate_closing_volume(staged)

        candles = [
            Candle(
                time=stamp,
                open=float(r["open"]),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
                volume=int(r.get("volume") or 0),
            )
            for _, stamp, r in staged
        ]
        return NseCharting._merge_boundary(candles) if merge else candles

    @staticmethod
    def _decumulate_closing_volume(staged) -> None:
        """NSE stamps each session's closing/settlement bar with the day's TOTAL
        traded quantity rather than that bar's own volume. Convert it back to an
        incremental figure so it can be merged without inflating the candle."""
        by_day: dict = {}
        for i, (raw, _, _) in enumerate(staged):
            by_day.setdefault(raw.date(), []).append(i)

        for idxs in by_day.values():
            if len(idxs) < 2:
                continue
            last = idxs[-1]
            others = sum(int(staged[i][2].get("volume") or 0) for i in idxs[:-1])
            vol = int(staged[last][2].get("volume") or 0)
            if others and vol >= others * 0.9:
                staged[last][2]["volume"] = max(vol - others, 0)

    @staticmethod
    def _merge_boundary(candles: list[Candle]) -> list[Candle]:
        """Collapse bars that share a candle boundary, exactly as the site's
        Table view does (open of the first, high/low extremes, close of the
        last). This is what folds the closing bar into the 15:15 candle."""
        out: list[Candle] = []
        for c in candles:
            if out and out[-1].time == c.time:
                prev = out[-1]
                out[-1] = Candle(
                    time=prev.time,
                    open=prev.open,
                    high=max(prev.high, c.high),
                    low=min(prev.low, c.low),
                    close=c.close,
                    volume=prev.volume + c.volume,
                )
            else:
                out.append(c)
        return out


# --------------------------------------------------------------------- output


def print_table(info, candles, interval, newest_first=True):
    """Render like the site's Table view: newest candle at the top."""
    print("\n%s - %s   (%s)" % (info["symbol"], info["exchange"], info["description"]))
    print(
        "%d candles - %s interval - times are IST candle START\n"
        % (len(candles), interval)
    )

    head = "%-20s %11s %11s %11s %11s %19s %14s" % (
        "Date - " + interval, "Open", "High", "Low", "Close", "Change", "Volume",
    )
    print(head)
    print("-" * len(head))

    changes = {}
    for i, c in enumerate(candles):
        if i == 0:
            changes[c.time] = None
        else:
            prev = candles[i - 1].close
            changes[c.time] = (
                c.close - prev,
                (c.close - prev) / prev * 100 if prev else 0.0,
            )

    ordered = list(reversed(candles)) if newest_first else candles
    for c in ordered:
        ch = changes[c.time]
        chs = (
            "-"
            if ch is None
            else "%s (%+.2f%%)" % (format(ch[0], "+,.2f"), ch[1])
        )
        print(
            "%-20s %11s %11s %11s %11s %19s %14s"
            % (
                c.time.strftime("%a %d %b %y %H:%M"),
                format(c.open, ",.2f"),
                format(c.high, ",.2f"),
                format(c.low, ",.2f"),
                format(c.close, ",.2f"),
                chs,
                format(c.volume, ","),
            )
        )


def write_csv(path, candles):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(
            fh, fieldnames=["time", "open", "high", "low", "close", "volume"]
        )
        w.writeheader()
        for c in candles:
            w.writerow(c.as_row())
    print("\nwrote %d rows -> %s" % (len(candles), path))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Fetch OHLC candles from charting.nseindia.com",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--symbol", default="NIFTY 50", help='e.g. "NIFTY 50", HDFCBANK-EQ')
    p.add_argument("--interval", default="15", choices=list(RESOLUTIONS))
    p.add_argument(
        "--days", type=int, default=5, help="lookback in calendar days (0 = all available)"
    )
    p.add_argument("--limit", type=int, help="keep only the N most recent candles")
    p.add_argument("--csv", help="also write candles to this CSV path")
    p.add_argument("--json", action="store_true", help="print raw JSON instead of a table")
    p.add_argument("--asc", action="store_true", help="oldest candle first")
    p.add_argument(
        "--raw-bars",
        action="store_true",
        help="do not merge bars sharing a candle boundary (shows the API's raw bars)",
    )
    p.add_argument("--search", help="look up symbol names and exit")
    a = p.parse_args(argv)

    client = NseCharting()

    try:
        if a.search:
            for h in client.search_symbols(a.search):
                print(
                    "%-22s %-20s scripcode=%-8s %s"
                    % (h["symbol"], h["type"], h["scripcode"], h["description"])
                )
            return 0

        info, candles = client.candles(
            a.symbol, a.interval, a.days or None, merge=not a.raw_bars
        )
    except NseChartError as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    except requests.RequestException as e:
        print("network error: %s" % e, file=sys.stderr)
        return 1

    if not candles:
        print(
            "no candles returned (market holiday, or no data for that interval)",
            file=sys.stderr,
        )
        return 1

    if a.limit:
        candles = candles[-a.limit :]

    if a.json:
        print(json.dumps([c.as_row() for c in candles], indent=2))
    else:
        print_table(info, candles, a.interval, newest_first=not a.asc)

    if a.csv:
        write_csv(a.csv, candles)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
