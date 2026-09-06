---
status: superseded by ADR-0008
---

# Signals carry measured Base Rates, not an invented confidence score

The original spec attached a rule-based 0-100 confidence score built from five weighted
factors. Tested against 88 Signals over 23 Nifty 50 sessions, it did not survive: one
factor ("breakout candle closed beyond the range") is true by construction and awarded a
constant 20 points to every Signal, inflating every score and making the 75/50 thresholds
meaningless; the 20-point "Candle 1 is a large move" factor pointed the wrong way, since
expectancy fell monotonically as Candle 1 widened; the 20-point breakout-volume factor was
non-monotonic with its highest third the worst performer; and the 25-point coil-tightness
factor showed no gradient. The only factor with a clean gradient, Stop distance, carried
the smallest weight at 15 points — and is merely Candle 1 width measured a second time,
since the Stop *is* Candle 1's extreme.

We therefore emit no composite score. A Signal carries its raw measurements and a Base Rate
looked up from our own Journal: the realised expectancy of past Signals resembling it,
stated with its sample size, and stated as "insufficient data" until the sample is large
enough to mean anything.

## Consequences

- The Journal becomes load-bearing rather than a log. Base Rates are only as good as it is.
- Alerts will be honestly uninformative for the first months. This is correct, not a defect.
- Per-third expectancies came from n=29 samples with wide variance. The finding is not
  "the score is inverted" but "the score was never checked and fails on checking". Do not
  reintroduce any factor above on the strength of these numbers either.
