---
status: accepted
---

# The Journal records the full Signal population, the user's pre-committed pick, and a mechanical benchmark

Base Rates require the full population of Signals, not only the ones acted on, so every
Signal and its realised outcome is recorded unconditionally. Two further columns exist to
answer questions the population alone cannot: the Signal the user would have taken,
recorded *before* the outcome is known, and the Signal one fixed mechanical rule would have
taken. Without the pre-committed pick, discretionary skill can never be separated from
hindsight; without the benchmark, "I picked well" has nothing to be well against.

## Consequences

- The pick must be captured before the outcome exists. A pick entered later is worthless
  and actively misleading, so the Journal timestamps it and refuses backdated entries.
- The benchmark rule is fixed once and never tuned. Tuning it turns the benchmark into
  another overfitted strategy and destroys its only purpose.
- Measured on 23 sessions, a plausible selection rule ("narrowest Candle 1") returned
  +0.179%/trade with a 95% interval of -0.120% to +0.478%, and 10.8% of randomly-picking
  simulations matched it. Seven rules were tried; the chance one would look that good by
  luck alone was 55%. Treat every future Journal-derived selection rule with the same
  suspicion, and record how many were tried.
