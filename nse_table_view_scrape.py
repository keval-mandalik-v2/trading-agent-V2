"""
Scrape 15-minute OHLC candles out of the *Table view* on charting.nseindia.com
by driving the real UI, exactly as a human would:

    load ?symbol=<SYM>  ->  check the interval button  ->  set it to 15m if it
    isn't already  ->  right-click the chart  ->  "Table view"  ->  read rows

This is the slow, literal route. `nse_chart_data.py` hits the same data through
the site's own JSON API and is far faster -- use this one to eyeball/verify the
UI, or if the API ever changes shape.

Requirements:
    pip install playwright
    python -m playwright install chromium

NOTE ON HEADLESS: charting.nseindia.com sits behind Akamai Bot Manager, which
kills headless Chromium at the TLS/HTTP2 layer (ERR_HTTP2_PROTOCOL_ERROR before
any HTML arrives). A visible browser window is required. --headless is offered
only so you can confirm that for yourself.

NOTE ON ROW COUNT: the Table view lists the candles currently in the chart's
visible range. Pass --zoom-out N to press the chart's zoom-out key N times
before switching to the table, which widens that range.

Usage:
    python nse_table_view_scrape.py
    python nse_table_view_scrape.py --symbol "NIFTY 50" --interval 15
    python nse_table_view_scrape.py --symbol HDFCBANK-EQ --zoom-out 4 --csv out.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

BASE = "https://charting.nseindia.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)

# interval button label -> the data-value used by the dropdown rows
INTERVALS = {
    "1": ("1m", "1"),
    "5": ("5m", "5"),
    "15": ("15m", "15"),
    "30": ("30m", "30"),
    "60": ("1h", "60"),
    "1D": ("1D", "1D"),
    "1W": ("1W", "1W"),
    "1M": ("1M", "1M"),
}

# "Thu 27 Aug '26 15:15"  /  "Thu 27 Aug '26"
DATE_RE = re.compile(r"^[A-Za-z]{3}\s+\d{1,2}\s+[A-Za-z]{3}\s+'\d{2}")


def _num(text: str) -> float | None:
    """Parse a table cell: strips thousands separators and the U+2212 minus."""
    t = (text or "").replace(",", "").replace("−", "-").replace("–", "-").strip()
    if not t or t in {"-", "--", "n/a"}:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _parse_stamp(text: str) -> datetime | None:
    t = " ".join(text.split())
    for fmt in ("%a %d %b '%y %H:%M", "%a %d %b '%y"):
        try:
            return datetime.strptime(t, fmt)
        except ValueError:
            continue
    return None


class TableViewScraper:
    def __init__(self, headless: bool = False, slow_mo: int = 0, verbose: bool = True):
        self.headless = headless
        self.slow_mo = slow_mo
        self.verbose = verbose

    def log(self, msg: str) -> None:
        if self.verbose:
            print(f"  [ui] {msg}", flush=True)

    def scrape(self, symbol: str, interval: str = "15", zoom_out: int = 0,
               shot_prefix: str | None = None) -> list[dict]:
        label, data_value = INTERVALS[interval]

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless, slow_mo=self.slow_mo)
            ctx = browser.new_context(
                user_agent=UA,
                viewport={"width": 1600, "height": 950},
                locale="en-IN",
                timezone_id="Asia/Kolkata",
            )
            page = ctx.new_page()
            try:
                self.log(f"opening {BASE}/?symbol={symbol}")
                page.goto(f"{BASE}/?symbol={symbol}", wait_until="load", timeout=90_000)

                frame = self._chart_frame(page)
                self._set_interval(page, frame, label, data_value)

                for i in range(zoom_out):
                    frame.locator("body").press("Minus")
                    page.wait_for_timeout(600)
                if zoom_out:
                    self.log(f"zoomed out {zoom_out}x to widen the visible range")

                if shot_prefix:
                    page.screenshot(path=f"{shot_prefix}_chart.png")

                self._open_table_view(page, frame)

                if shot_prefix:
                    page.screenshot(path=f"{shot_prefix}_table.png")

                rows = self._read_table(page, frame)
                self.log(f"parsed {len(rows)} candles from the Table view")
                return rows
            finally:
                browser.close()

    # ------------------------------------------------------------------ steps

    def _chart_frame(self, page):
        """The TradingView library lives in a blob: iframe named tradingview_*."""
        for _ in range(45):
            for f in page.frames:
                if f.name.startswith("tradingview_"):
                    # wait for the toolbar to actually exist inside it
                    try:
                        f.wait_for_selector("#header-toolbar-intervals", timeout=2_000)
                        self.log(f"chart iframe ready ({f.name})")
                        return f
                    except PWTimeout:
                        pass
            page.wait_for_timeout(1_000)
        raise RuntimeError("chart iframe never became ready -- site layout may have changed")

    def _interval_button(self, frame):
        return frame.locator(
            "#header-toolbar-intervals button, "
            "#header-toolbar-intervals div[class*='menu-']"
        ).first

    def _set_interval(self, page, frame, label: str, data_value: str) -> None:
        btn = self._interval_button(frame)
        current = (btn.inner_text() or "").strip()
        self.log(f"interval currently {current!r}, want {label!r}")
        if current.lower() == label.lower():
            self.log("already on the right interval, nothing to change")
            return

        btn.click()
        page.wait_for_timeout(1_200)
        frame.locator(f"[data-value='{data_value}']").first.click(timeout=8_000)
        page.wait_for_timeout(5_000)  # let the new resolution's bars load

        now = (self._interval_button(frame).inner_text() or "").strip()
        if now.lower() != label.lower():
            raise RuntimeError(f"failed to switch interval: button still says {now!r}")
        self.log(f"interval switched to {now!r}")

    def _open_table_view(self, page, frame) -> None:
        box = None
        for sel in (
            "div.chart-markup-table.pane",
            "div[class*='chart-gui-wrapper'] canvas",
            "div[class*='chart-container'] canvas",
        ):
            try:
                box = frame.locator(sel).first.bounding_box(timeout=8_000)
                if box and box["width"] > 200 and box["height"] > 200:
                    break
                box = None
            except PWTimeout:
                continue
        if not box:
            raise RuntimeError("could not locate the chart pane to right-click")

        # right-click dead centre of the chart, away from axes and drawings
        page.mouse.click(
            box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5, button="right"
        )
        page.wait_for_timeout(1_500)
        self.log("right-clicked the chart, looking for 'Table view'")

        clicked = False
        for scope in (frame, page.main_frame):
            try:
                scope.get_by_text("Table view", exact=False).first.click(timeout=4_000)
                clicked = True
                break
            except PWTimeout:
                continue
        if not clicked:
            raise RuntimeError("'Table view' was not in the context menu")

        frame.wait_for_selector("table", timeout=20_000)
        page.wait_for_timeout(2_500)
        self.log("Table view is open")

    def _read_table(self, page, frame) -> list[dict]:
        # the list is scroll-loaded: keep scrolling until the row count settles
        seen = -1
        for _ in range(30):
            count = frame.evaluate(
                "() => { const t = document.querySelector('table');"
                " return t ? t.rows.length : 0; }"
            )
            if count == seen:
                break
            seen = count
            frame.evaluate(
                """() => {
                    const c = document.querySelector("[class*='tableContainer']")
                          || document.scrollingElement;
                    c.scrollTop = c.scrollHeight;
                }"""
            )
            page.wait_for_timeout(700)

        raw = frame.evaluate(
            """() => {
                const t = document.querySelector('table');
                if (!t) return [];
                return [...t.rows].map(r => [...r.cells].map(c => c.innerText.trim()));
            }"""
        )

        out = []
        for cells in raw:
            if not cells or not DATE_RE.match(cells[0] or ""):
                continue  # header / column-group rows
            stamp = _parse_stamp(cells[0])
            vals = [_num(c) for c in cells[1:5]]
            if stamp is None or any(v is None for v in vals):
                continue
            o, h, l, c = vals
            out.append(
                {
                    "time": stamp.strftime("%Y-%m-%d %H:%M"),
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                }
            )
        out.sort(key=lambda r: r["time"])
        return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Scrape OHLC candles from the charting.nseindia.com Table view",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--symbol", default="HDFCBANK-EQ", help='e.g. HDFCBANK-EQ, "NIFTY 50"')
    p.add_argument("--interval", default="15", choices=list(INTERVALS))
    p.add_argument("--zoom-out", type=int, default=0, help="widen the visible candle range")
    p.add_argument("--csv", help="write the scraped candles to this CSV path")
    p.add_argument("--shots", help="path prefix for before/after screenshots")
    p.add_argument("--headless", action="store_true", help="expected to fail; see module docstring")
    p.add_argument("--slow-mo", type=int, default=0, help="ms delay between UI actions")
    p.add_argument("--quiet", action="store_true")
    a = p.parse_args(argv)

    scraper = TableViewScraper(headless=a.headless, slow_mo=a.slow_mo, verbose=not a.quiet)
    try:
        rows = scraper.scrape(a.symbol, a.interval, a.zoom_out, a.shots)
    except Exception as e:
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    if not rows:
        print("no rows scraped", file=sys.stderr)
        return 1

    head = f"{'Date - ' + a.interval:<20} {'Open':>11} {'High':>11} {'Low':>11} {'Close':>11}"
    print(f"\n{a.symbol} - Table view - {len(rows)} candles ({a.interval}), IST candle START\n")
    print(head)
    print("-" * len(head))
    for r in reversed(rows):  # newest first, like the site
        print(
            f"{r['time']:<20} {r['open']:>11,.2f} {r['high']:>11,.2f} "
            f"{r['low']:>11,.2f} {r['close']:>11,.2f}"
        )

    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["time", "open", "high", "low", "close"])
            w.writeheader()
            w.writerows(rows)
        print(f"\nwrote {len(rows)} rows -> {a.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
