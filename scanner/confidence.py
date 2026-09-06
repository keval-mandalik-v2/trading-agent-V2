"""
The 0-100 confidence score.

This is a **rule-based heuristic, not a probability**. It says how well a Signal matches
criteria we believe are sensible; it does not say how likely the Trade is to win. The
Base Rate alongside it is the empirical number, and it is the one that earns trust over
time.

The original five-factor score was removed in ADR-0002 after testing showed it did not
survive contact with data. It is reinstated here at the user's request, rebuilt so that
each factor carries real information:

  * The old "breakout candle closed beyond the range" factor was true by construction --
    the scanner only ever signals on a confirmed close -- so it handed every Signal a
    constant 20 points and made the thresholds meaningless. It is replaced by breakout
    conviction, which measures *how far* beyond the range the candle closed. Same idea,
    but now it can actually vary.
  * The old "Candle 1 is a large move" factor rewarded wide opening candles. Measured
    expectancy fell monotonically as Candle 1 widened, so rewarding width was backwards.
    Candle 1 width now enters through Risk:Reward, where a wide Candle 1 means a distant
    Stop and therefore a lower score.
  * Risk:Reward showed the only clean gradient in testing and previously carried the
    smallest weight. It now carries the largest.
  * Liquidity is new. A 1% Target is not reachable in a stock whose spread eats a third
    of it, and nothing in the old score noticed.

Weights total 100. They are reasoned, not fitted -- fitting them to 88 outcomes would be
overfitting, which is the failure ADR-0005 exists to guard against.
"""

from __future__ import annotations

from dataclasses import dataclass

OK, MEH, BAD = "OK", "MEH", "BAD"

MARK = {OK: "✅", MEH: "⚠️", BAD: "❌"}


@dataclass(frozen=True)
class Factor:
    name: str
    verdict: str
    points: int
    out_of: int
    detail: str

    def line(self) -> str:
        return "%s %s" % (MARK[self.verdict], self.detail)


def _band(value: float, bands: list[tuple[float, str, int]], detail_fn) -> tuple[str, int, str]:
    """Pick the first band whose threshold the value satisfies. Bands are descending."""
    for threshold, verdict, points in bands:
        if value >= threshold:
            return verdict, points, detail_fn(value)
    verdict, points = bands[-1][1], bands[-1][2]
    return verdict, points, detail_fn(value)


def evaluate(
    breakeven_hit_rate: float,
    breakout_close_beyond_pct: float,
    coil_use_pct: float,
    volume_ratio: float,
    turnover_rupees: float,
) -> tuple[int, list[Factor], str, str]:
    """
    Returns (score out of 100, factors, verdict label, suggested action).

    `breakout_close_beyond_pct` is how far past Candle 1's edge the breakout candle
    closed, expressed as a percentage of Candle 1's own range.
    """
    factors: list[Factor] = []

    # --- 1. Risk:Reward, 30 pts. The only factor with a measured gradient. ------
    if breakeven_hit_rate <= 45:
        v, p = OK, 30
    elif breakeven_hit_rate <= 55:
        v, p = OK, 22
    elif breakeven_hit_rate <= 65:
        v, p = MEH, 11
    else:
        v, p = BAD, 0
    factors.append(Factor(
        "risk_reward", v, p, 30,
        "Risk:Reward — you must win %.0f%% of trades like this just to break even"
        % breakeven_hit_rate))

    # --- 2. Breakout conviction, 20 pts. Replaces the old tautology. ------------
    if breakout_close_beyond_pct >= 25:
        v, p = OK, 20
    elif breakout_close_beyond_pct >= 10:
        v, p = OK, 14
    elif breakout_close_beyond_pct >= 3:
        v, p = MEH, 7
    else:
        v, p = BAD, 0
    factors.append(Factor(
        "conviction", v, p, 20,
        "Breakout closed %.0f%% of the box width beyond the edge — %s"
        % (breakout_close_beyond_pct,
           "decisive" if p >= 14 else "barely cleared it" if p else "a hair past the line")))

    # --- 3. Coil tightness, 20 pts. The pattern's own premise. ------------------
    if coil_use_pct <= 50:
        v, p = OK, 20
    elif coil_use_pct <= 70:
        v, p = OK, 13
    elif coil_use_pct <= 85:
        v, p = MEH, 7
    else:
        v, p = BAD, 0
    factors.append(Factor(
        "coil", v, p, 20,
        "Coil used %.0f%% of Candle 1's range — %s"
        % (coil_use_pct, "tight" if p >= 13 else "loose" if p else "barely a coil at all")))

    # --- 4. Breakout volume, 15 pts. -------------------------------------------
    if volume_ratio >= 1.5:
        v, p = OK, 15
    elif volume_ratio >= 1.0:
        v, p = OK, 10
    elif volume_ratio >= 0.6:
        v, p = MEH, 5
    else:
        v, p = BAD, 0
    factors.append(Factor(
        "volume", v, p, 15,
        "Breakout volume %.2fx the coil average — %s"
        % (volume_ratio, "conviction behind it" if p >= 10 else "thin participation")))

    # --- 5. Liquidity, 15 pts. New: a 1% Target dies to a wide spread. ---------
    crore = turnover_rupees / 1e7
    if crore >= 2:
        v, p = OK, 15
    elif crore >= 0.5:
        v, p = OK, 10
    elif crore >= 0.1:
        v, p = MEH, 5
    else:
        v, p = BAD, 0
    factors.append(Factor(
        "liquidity", v, p, 15,
        "Traded ₹%.2f crore in the breakout candle — %s"
        % (crore, "liquid enough to get filled" if p >= 10 else "thin; expect slippage")))

    total = sum(f.points for f in factors)

    if total >= 75:
        label = "✅ STRONG SETUP"
    elif total >= 50:
        label = "⚠️ CONSIDER WITH CAUTION"
    else:
        label = "❌ WEAK — LIKELY SKIP"

    # Suggested action. Risk:Reward can veto on its own: a Trade needing a 65%+ hit rate
    # is one this pattern has never historically delivered, whatever else looks good.
    if breakeven_hit_rate > 65:
        action = ("SKIP", "WIDE_STOP",
                  "needs a %.0f%% win rate; this pattern has never sustained that"
                  % breakeven_hit_rate)
    elif total >= 75:
        action = ("TAKE", "", "all five criteria line up")
    elif total >= 50:
        action = ("YOUR CALL", "LOW_CONVICTION",
                  "mixed evidence — the score is a heuristic, not a forecast")
    else:
        action = ("SKIP", "LOW_CONVICTION", "too many criteria fail")

    return total, factors, label, action
