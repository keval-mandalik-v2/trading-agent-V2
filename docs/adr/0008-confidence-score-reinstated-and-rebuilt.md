---
status: accepted
supersedes: ADR-0002
---

# The confidence score returns, rebuilt so each factor carries information

ADR-0002 deleted the 0-100 confidence score after testing showed it did not survive contact
with data. The user has since required it back: a single number expressing how well a Signal
matches our criteria is what makes an alert actionable for them in the fifteen minutes
ADR-0004 allows. That is a legitimate requirement and the score is reinstated — but rebuilt,
because reinstating the original would reintroduce every defect ADR-0002 documented.

Four changes from the original five factors:

- **"Breakout closed beyond the range" (20 pts) was true by construction** — the scanner only
  ever signals on a confirmed close — so it granted every Signal a constant 20 points and made
  the 75/50 thresholds meaningless. Replaced by **breakout conviction**, which measures *how
  far* past the edge the candle closed as a share of Candle 1's width. Same intuition, but a
  quantity that can actually vary.
- **"Candle 1 is a large move" (20 pts) pointed the wrong way.** Measured expectancy fell
  monotonically as Candle 1 widened. Removed; Candle 1 width now enters through Risk:Reward,
  where a wide Candle 1 means a distant Stop and therefore a *lower* score.
- **Risk:Reward was weighted lowest at 15 pts** despite being one of only two factors showing
  a clean gradient. It now carries the largest weight at 30, and can veto on its own: a Trade
  requiring better than a 65% Hit Rate is suggested as a SKIP regardless of the total.
- **Liquidity (15 pts) is new.** A 1% Target is not collectable in a stock whose spread eats a
  third of it, and nothing in the original score noticed.

Weights are reasoned from the measured evidence, not fitted to it. Fitting five weights to 88
outcomes is exactly the overfitting ADR-0005 exists to prevent.

## Consequences

- **The score is a heuristic, not a probability.** The alert says so, and the Base Rate from
  ADR-0002 is retained alongside it and labelled as the measured number that outranks the
  score wherever the two disagree.
- The score is displayed only; it filters nothing. Every Signal is still journalled and
  alerted, so the Journal continues to record the full population and Base Rates stay unbiased.
- Whether the score predicts anything is now itself a testable question. It must not be tuned
  against the Journal without recording the attempt in the research log.
