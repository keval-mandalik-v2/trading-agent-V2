---
status: accepted
---

# Nifty 50 is the only universe, for both backtest and live scanning

The strategy places its Stop at Candle 1's opposite extreme, which means the market — not
us — chooses the risk on every Trade. Measured over 21 sessions, that distance is ~0.96%
on large caps but reached 9.75% on THERMAX, a midcap whose Candle 1 was 9.13% wide. At the
intended 5x intraday leverage a single 9.75% Stop removes half the account. Restricting to
Nifty 50 eliminates that failure mode structurally rather than by tuning a filter, and
guarantees every candidate is liquid enough for a 1% Target to survive the spread and
eligible for intraday margin.

We deliberately use the same universe for the backtest, rejecting a wider measurement
universe, so that measured behaviour and traded behaviour cannot diverge.

## Consequences

- Only ~88 Signals exist in the ~21 sessions of 15-minute history the data source will
  return, of which ~23 reach Target or Stop. That is far too few to distinguish a 40% Hit
  Rate from a 70% one. **No Hit Rate claim from this backtest is trustworthy on its own.**
- Forward-collecting ~100 decided outcomes at ~4 Signals/day takes roughly 5 months.
- Two of the three trades that motivated this project (DATAPATTNS, THERMAX) are outside
  this universe and will never be scanned. This is intended, not an oversight.
- Do not widen the universe to recover sample size without re-deciding the Stop rule
  first. The narrow universe is what currently bounds risk.

## Amendment — the stated rationale is weaker than it looked

This ADR justified the narrow universe on the grounds that the Stop distance is chosen by
the market, so a 9.13%-wide Candle 1 forces a 9.75% Stop. Risk-based position sizing
dissolves that argument: sizing each Trade so that being stopped costs a fixed fraction of
equity makes a 9.75% Stop cost exactly what a 0.5% Stop costs, by taking a position roughly
twenty times smaller. Measured over the same 88 Signals, fixed ₹1,00,000 notional ended at
₹18,520 with a 34% drawdown while risking 1% of equity per Trade ended at ₹20,294 with a
4.8% drawdown.

The universe restriction stands as a deliberate, reaffirmed simplification for a first
system, not because wide Stops are unmanageable. **If the universe is ever widened, adopt
risk-based sizing in the same change** — the two decisions are coupled, and widening the
universe under fixed-notional sizing is the specific combination that produced the 34%
drawdown above.
