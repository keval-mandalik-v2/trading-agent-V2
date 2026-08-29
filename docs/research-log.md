# Research log

Append-only (ADR-0005). Every hypothesis tested against the Journal, failures included,
with the number of variants tried to reach it. A result quoted without its trial count is
not a result.

## 2026-08-28 — Design phase: is the pattern tradeable on Nifty 50?

Baseline, entry at the next candle's open, Target 1%, Stop at Candle 1's extreme, real
Indian intraday cost model, 15:15 square-off. 50 symbols, 23 sessions, 1,150 stock-days.

- **Result:** 88 Signals. TARGET 17, STOP 10, FIZZLE 61. Hit Rate of Signals 19%;
  excluding Fizzles 63% against a Breakeven Hit Rate of 50%. Gross +0.112%/Signal,
  **net +0.006%/Signal**, 95% CI -0.127% to +0.138%, t=0.08. Zero is inside the interval.
  Separating this from zero would need tens of thousands of Signals.
- **Variants tried to reach this:** 43

### The 43

| group | variants | outcome |
|---|---|---|
| Coil definition | 4 | wicks-inclusive best; loosening to bodies tripled Signals and pushed the Hit Rate below breakeven |
| Exit rule | 10 | all ten net-negative under a flat 0.10% cost; the spec's 1% Target was the least bad |
| Fill assumption | 5 | lookahead was *not* flattering; the entire gross edge is the size of the bid-ask spread |
| Selection rule | 7 | "narrowest Candle 1" net +0.079%, n=23, but 10.8% of random pickers matched it |
| Sizing rule | 6 | risk-based sizing turned -7.4% at 34% drawdown into +1.5% at 4.8%. Derived from a principle, not fitted |
| Decision protocol | 5 | "first-come, 1 position" net +0.065%, n=27 |
| Scoring factor | 6 | under the real cost model, no factor showed a clean monotonic gradient |

### Interpretation

A permutation test (20,000 random 1-per-session pickers) put the chance of any single
variant looking profitable by luck at 10.8%. Across 43 tests, `1 - (1 - 0.108)^43` makes
at least one false winner essentially certain and four the expected count. Four appeared.
**None of them is validated.** They are hypotheses awaiting out-of-sample evidence from
the Journal, and re-testing them on this same 88 does not count as evidence.

Two claims from the original brief were checked and did not survive:

- **"80-85% accuracy."** Achievable — at a 0.5% Target, 77% of decided Trades hit. That
  version loses money twice as fast as the 1% Target version. A Hit Rate without its
  Target and Stop attached is not information.
- **"+8% and +13% intraday moves."** Zero of 88 Signals ever moved 3% in their favour.
  Median max favourable excursion was +0.51%. Those moves belong to midcaps, which
  ADR-0001 deliberately excludes.
