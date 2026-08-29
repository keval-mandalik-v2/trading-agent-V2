"""
The rule engine. The single implementation of Coil, Poke, Signal, Target and Stop,
used unchanged by the backtester and by the live scanner -- so what is measured is
exactly what is alerted.

Two things here are deliberate and easy to get wrong:

Entry is the OPEN of the candle AFTER the breakout candle, never the breakout
candle's own close. The breakout candle only becomes known when it closes, so
filling at its close is a price nobody could have got. The spec's "signal at
10:30" was this same off-by-one candle: the 10:30 candle finishes at 10:45.

Rejections return a reason code rather than None, because a lab wants to know why
1,062 of 1,150 stock-days produced nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

from scanner import config as cfg
from scanner import money
from scanner.candles import Bar, next_candle

BUY = "BUY"
SELL = "SELL"


@dataclass(frozen=True)
class Coil:
    c1_high: float
    c1_low: float
    c1_width_pct: float
    coil_high: float
    coil_low: float
    coil_use_pct: float        # how much of Candle 1's range the Coil consumed
    coil_avg_volume: float


@dataclass(frozen=True)
class Signal:
    symbol: str
    day: str
    direction: str
    breakout_candle: str       # the candle whose close cleared Candle 1
    entry_candle: str          # the candle we actually enter on, at its open
    stop: float
    stop_pct: float | None     # unknown until the entry price is known
    target_pct: float
    breakout_vol_ratio: float
    coil: Coil

    def as_row(self) -> dict:
        row = {k: v for k, v in asdict(self).items() if k != "coil"}
        row.update(asdict(self.coil))
        return row


@dataclass(frozen=True)
class Outcome:
    status: str                # TARGET | STOP | FIZZLE
    realized_pct: float
    resolved_candle: str


def find_coil(session: dict[str, Bar]) -> tuple[Coil | None, str]:
    """Is this session a Coil? Returns (Coil, "OK") or (None, reason)."""
    c1 = session.get(cfg.CANDLE_1)
    if c1 is None:
        return None, "NO_CANDLE_1"
    if c1.high <= c1.low:
        return None, "CANDLE_1_ZERO_RANGE"

    missing = [t for t in cfg.COIL_TIMES if t not in session]
    if missing:
        return None, "MISSING_CANDLES:" + ",".join(missing)

    for t in cfg.COIL_TIMES:
        bar = session[t]
        if bar.high > c1.high or bar.low < c1.low:
            return None, "POKE:" + t

    highs = [session[t].high for t in cfg.COIL_TIMES]
    lows = [session[t].low for t in cfg.COIL_TIMES]
    vols = [session[t].volume for t in cfg.COIL_TIMES]
    c1_range = c1.high - c1.low
    return Coil(
        c1_high=c1.high,
        c1_low=c1.low,
        c1_width_pct=c1_range / c1.low * 100,
        coil_high=max(highs),
        coil_low=min(lows),
        coil_use_pct=(max(highs) - min(lows)) / c1_range * 100,
        coil_avg_volume=sum(vols) / len(vols),
    ), "OK"


def find_signal(
    symbol: str,
    day: str,
    session: dict[str, Bar],
    coil: Coil,
    through: str | None = None,
) -> tuple[Signal | None, str]:
    """
    The first candle in the breakout window whose CLOSE clears Candle 1.

    `through` caps how far we may look, so the live scanner cannot see candles that
    have not finished yet. One Signal per stock per session, first break only --
    reversals are not re-entered (measured at 10% frequency, n=6, no evidence for
    the complexity).
    """
    for t in cfg.BREAKOUT_WINDOW:
        if through is not None and t > through:
            return None, "PENDING"
        bar = session.get(t)
        if bar is None:
            continue
        if bar.close > coil.c1_high:
            direction, stop = BUY, coil.c1_low
        elif bar.close < coil.c1_low:
            direction, stop = SELL, coil.c1_high
        else:
            continue

        entry_candle = next_candle(t)
        if entry_candle is None:
            return None, "NO_ENTRY_CANDLE"
        ratio = bar.volume / coil.coil_avg_volume if coil.coil_avg_volume else 0.0
        return Signal(
            symbol=symbol,
            day=day,
            direction=direction,
            breakout_candle=t,
            entry_candle=entry_candle,
            stop=stop,
            stop_pct=None,
            target_pct=cfg.TARGET_PCT,
            breakout_vol_ratio=ratio,
            coil=coil,
        ), "OK"
    return None, "NO_BREAKOUT"


def entry_price(session: dict[str, Bar], signal: Signal) -> float | None:
    """The open of the entry candle, once that candle exists in the feed."""
    bar = session.get(signal.entry_candle)
    return bar.open if bar else None


def levels(signal: Signal, entry: float) -> dict:
    """Target, Stop distance and the Breakeven Hit Rate this Trade must beat."""
    sign = 1 if signal.direction == BUY else -1
    target = entry * (1 + sign * signal.target_pct / 100)
    stop_pct = abs(entry - signal.stop) / entry * 100
    notional = money.position_size("risk_1pct", cfg.EQUITY, stop_pct)
    cost_pct = money.round_trip_pct(notional)
    return {
        "entry": entry,
        "target": target,
        "stop": signal.stop,
        "stop_pct": stop_pct,
        "cost_pct": cost_pct,
        "breakeven_hit_rate": money.breakeven_hit_rate(stop_pct, signal.target_pct, cost_pct),
    }


def resolve(session: dict[str, Bar], signal: Signal, entry: float,
            through: str | None = None) -> Outcome | None:
    """
    Walk forward from the entry candle to the first Target or Stop touch.

    A candle that touches both is scored as a STOP: 15-minute bars cannot say which
    came first, and assuming the good one is how backtests lie. If neither is hit by
    the square-off candle, that is a Fizzle exited at the square-off open.
    """
    sign = 1 if signal.direction == BUY else -1
    target = entry * (1 + sign * signal.target_pct / 100)
    stop = signal.stop
    order = list(cfg.SESSION_CANDLES)
    start = order.index(signal.entry_candle) if signal.entry_candle in order else 0

    last: tuple[str, Bar] | None = None
    for t in order[start:]:
        if through is not None and t > through:
            return None
        bar = session.get(t)
        if bar is None:
            continue
        if t == cfg.SQUARE_OFF_CANDLE:
            return Outcome("FIZZLE", (bar.open - entry) / entry * 100 * sign, t)
        hit_stop = bar.low <= stop if signal.direction == BUY else bar.high >= stop
        hit_target = bar.high >= target if signal.direction == BUY else bar.low <= target
        if hit_stop:
            return Outcome("STOP", -abs(entry - stop) / entry * 100, t)
        if hit_target:
            return Outcome("TARGET", signal.target_pct, t)
        last = (t, bar)

    # The square-off candle is missing from the feed. Fall back to the last bar we
    # did see rather than returning None -- silently dropping unresolvable Signals
    # would bias the sample, and only a live Trade may legitimately be unresolved.
    if through is None and last is not None:
        return Outcome("FIZZLE", (last[1].close - entry) / entry * 100 * sign, last[0])
    return None
