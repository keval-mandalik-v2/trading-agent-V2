"""
Rupees: what a round trip actually costs, and how big a position may be.

The cost model is the real Indian intraday equity stack rather than a flat
percentage, because against a 1% Target the gap between 0.05% and 0.10% is the
gap between an edge and no edge.

Sizing exists because of the ADR-0001 amendment: the Stop distance is chosen by
the market, so a fixed position size lets the market choose your risk too.
"""

from __future__ import annotations

from scanner import config as cfg

# discount-broker intraday equity rates as of writing
BROKERAGE_PCT = 0.03
BROKERAGE_CAP = 20.0
STT_SELL_PCT = 0.025
EXCHANGE_PCT = 0.00297
SEBI_PCT = 0.0001
STAMP_BUY_PCT = 0.003
GST_PCT = 18.0


def round_trip_cost(notional: float) -> float:
    """Total rupees to enter and exit a position of this size."""
    if notional <= 0:
        return 0.0
    turnover = notional * 2
    brokerage = min(BROKERAGE_CAP, notional * BROKERAGE_PCT / 100) * 2
    exchange = turnover * EXCHANGE_PCT / 100
    sebi = turnover * SEBI_PCT / 100
    stt = notional * STT_SELL_PCT / 100
    stamp = notional * STAMP_BUY_PCT / 100
    gst = (brokerage + exchange + sebi) * GST_PCT / 100
    return brokerage + exchange + sebi + stt + stamp + gst


def round_trip_pct(notional: float) -> float:
    """The same cost against the position, for comparison with the Target."""
    return round_trip_cost(notional) / notional * 100 if notional > 0 else 0.0


def breakeven_hit_rate(stop_pct: float, target_pct: float, cost_pct: float = 0.0) -> float:
    """
    The Hit Rate at which a set of Signals makes exactly zero (CONTEXT.md).

    Winning target_pct and losing stop_pct, this is the fraction of wins required.
    """
    win, loss = target_pct - cost_pct, stop_pct + cost_pct
    return 100 * loss / (win + loss) if win + loss > 0 else 100.0


SIZING_RULES = ("fixed_5x", "fixed_2_5x", "fixed_1x", "risk_0_5pct", "risk_1pct", "risk_2pct")


def position_size(rule: str, equity: float, stop_pct: float) -> float:
    """Notional rupees to deploy, capped by available buying power."""
    cap = min(cfg.MAX_BUYING_POWER, equity * 5)
    if rule == "fixed_5x":
        return cap
    if rule == "fixed_2_5x":
        return min(equity * 2.5, cap)
    if rule == "fixed_1x":
        return min(equity, cap)
    pct = {"risk_0_5pct": 0.5, "risk_1pct": 1.0, "risk_2pct": 2.0}.get(rule)
    if pct is None:
        raise ValueError("unknown sizing rule %r" % rule)
    if stop_pct <= 0:
        return 0.0
    return min(equity * pct / 100 / (stop_pct / 100), cap)
