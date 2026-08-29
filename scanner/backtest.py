"""
The historical harness. Same rule engine as the live scanner, pointed at cached
candles instead of at today.

It reports expectancy rather than Hit Rate, because a Hit Rate without its Target
and Stop attached means nothing (CONTEXT.md), and it always reports the number of
Signals so that a flattering number cannot be quoted without its sample size.

The feed only serves about 24 sessions of 15-minute history, so this can rule out a
badly broken strategy and cannot certify a good one. That limit is why the Journal
exists.

Usage:
    python -m scanner.backtest --fetch          # top up the candle cache first
    python -m scanner.backtest                  # report on what is cached
    python -m scanner.backtest --factors        # also test each scoring factor
"""

from __future__ import annotations

import argparse
import math
import statistics as st
import time

from scanner import candles as cd
from scanner import config as cfg
from scanner import journal as jr
from scanner import money
from scanner import rules


def collect(universe: list[str]) -> list[dict]:
    """Every Signal in the cache, resolved, with the fields needed to slice it."""
    out = []
    for sym in universe:
        sessions = cd.build_sessions(cd.load(sym))
        for day, session in sorted(sessions.items()):
            coil, _ = rules.find_coil(session)
            if coil is None:
                continue
            sig, _ = rules.find_signal(sym, day, session, coil)
            if sig is None:
                continue
            entry = rules.entry_price(session, sig)
            if entry is None:
                continue
            lv = rules.levels(sig, entry)
            outcome = rules.resolve(session, sig, entry)
            if outcome is None:
                continue
            out.append({
                "symbol": sym, "day": day, "direction": sig.direction,
                "breakout_candle": sig.breakout_candle, "entry": entry,
                "stop_pct": lv["stop_pct"], "cost_pct": lv["cost_pct"],
                "c1_width_pct": coil.c1_width_pct, "coil_use_pct": coil.coil_use_pct,
                "vol_ratio": sig.breakout_vol_ratio,
                "outcome": outcome.status, "gross_pct": outcome.realized_pct,
            })
    return out


def summarise(sigs: list[dict]) -> None:
    if not sigs:
        print("no Signals in the cache -- run with --fetch first")
        return
    gross = [s["gross_pct"] for s in sigs]
    net = [s["gross_pct"] - s["cost_pct"] for s in sigs]
    n = len(sigs)
    counts = {k: sum(1 for s in sigs if s["outcome"] == k) for k in ("TARGET", "STOP", "FIZZLE")}
    decided = counts["TARGET"] + counts["STOP"]
    stops = sorted(s["stop_pct"] for s in sigs)
    sd = st.stdev(net) if n > 1 else 0.0
    se = sd / math.sqrt(n) if n else 0.0

    days = len({s["day"] for s in sigs})
    print("Signals %d over %d sessions, %d symbols" % (n, days, len({s["symbol"] for s in sigs})))
    print("  TARGET %d   STOP %d   FIZZLE %d" % (counts["TARGET"], counts["STOP"], counts["FIZZLE"]))
    print("  Hit Rate of Signals      %5.1f%%" % (100 * counts["TARGET"] / n))
    print("  Hit Rate excl. Fizzles   %5.1f%%   (%d decided)"
          % (100 * counts["TARGET"] / decided if decided else 0, decided))
    print("  Stop distance %%          min %.2f  median %.2f  max %.2f"
          % (stops[0], stops[len(stops) // 2], stops[-1]))
    print("  Breakeven Hit Rate at median Stop  %.1f%%"
          % money.breakeven_hit_rate(stops[len(stops) // 2], cfg.TARGET_PCT))
    print()
    print("  gross expectancy  %+.4f%% per Signal" % st.mean(gross))
    print("  net expectancy    %+.4f%% per Signal  (real cost model)" % st.mean(net))
    if n > 1:
        print("  95%% CI on net     %+.4f%% to %+.4f%%   t=%.2f"
              % (st.mean(net) - 1.96 * se, st.mean(net) + 1.96 * se,
                 st.mean(net) / se if se else 0))
        print("  --> zero %s inside the interval"
              % ("IS" if st.mean(net) - 1.96 * se < 0 < st.mean(net) + 1.96 * se else "is NOT"))
        need = (1.96 * sd / abs(st.mean(net))) ** 2 if st.mean(net) else float("inf")
        print("  Signals needed to separate this from zero: %d (~%.1f years at 1/day)"
              % (min(need, 1e9), need / 250))


def equity_curves(sigs: list[dict]) -> None:
    print("\nSame Signals, taken in order, under each sizing rule (equity Rs %s):"
          % format(round(cfg.EQUITY), ","))
    print("  %-14s %12s %9s %13s" % ("rule", "end equity", "max DD", "worst trade"))
    ordered = sorted(sigs, key=lambda s: (s["day"], s["breakout_candle"], s["symbol"]))
    for rule in money.SIZING_RULES:
        eq = peak = cfg.EQUITY
        dd = worst = 0.0
        for s in ordered:
            notional = money.position_size(rule, eq, s["stop_pct"])
            pnl = notional * s["gross_pct"] / 100 - money.round_trip_cost(notional)
            eq += pnl
            worst = min(worst, pnl)
            peak = max(peak, eq)
            dd = max(dd, (peak - eq) / peak * 100 if peak else 0)
        print("  %-14s %12s %8.1f%% %13s"
              % (rule, format(round(eq), ","), dd, format(round(worst), ",")))


def factors(sigs: list[dict]) -> None:
    print("\nDoes each factor predict net expectancy? (terciles, ascending)")
    print("  Every row below is a hypothesis. ADR-0005 requires the count.")
    for label, key in [("Coil use %% (tight->loose)", "coil_use_pct"),
                       ("Candle 1 width (small->big)", "c1_width_pct"),
                       ("Breakout volume ratio", "vol_ratio"),
                       ("Stop distance (near->far)", "stop_pct")]:
        v = sorted(sigs, key=lambda s: s[key])
        a, b = len(v) // 3, 2 * len(v) // 3
        parts = [v[:a], v[a:b], v[b:]]
        cells = " | ".join(
            "n=%2d %+0.3f%%" % (len(p), st.mean([x["gross_pct"] - x["cost_pct"] for x in p]))
            for p in parts if p)
        print("  %-30s %s" % (label, cells))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Backtest the rule engine over cached candles")
    p.add_argument("--fetch", action="store_true", help="top up the candle cache first")
    p.add_argument("--days", type=int, default=45, help="lookback when fetching")
    p.add_argument("--factors", action="store_true", help="also test each scoring factor")
    p.add_argument("--log", help="append the headline result to the research log under this title")
    a = p.parse_args(argv)

    universe = jr.load_universe()
    if a.fetch:
        print("fetching %d symbols..." % len(universe))
        for i, sym in enumerate(universe):
            try:
                cd.save(sym, cd.merge(cd.load(sym), cd.fetch(sym, a.days)))
            except Exception as exc:
                print("  warn %s: %s" % (sym, str(exc)[:70]))
            if i % 10 == 9:
                print("  %d/%d" % (i + 1, len(universe)))
                time.sleep(0.5)

    sigs = collect(universe)
    summarise(sigs)
    if sigs:
        equity_curves(sigs)
    if a.factors and sigs:
        factors(sigs)
    if a.log and sigs:
        net = [s["gross_pct"] - s["cost_pct"] for s in sigs]
        jr.log_hypothesis(a.log, "net %+.4f%%/Signal over n=%d" % (st.mean(net), len(sigs)),
                          variants_tried=1)
        print("\nappended to %s" % cfg.RESEARCH_LOG)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
