---
status: accepted
---

# The stopping rule is pre-registered: 100 Trades for the strategy, a learning milestone for the project

A project with no stopping rule does not end; it becomes hope. Both criteria below are
recorded before any capital is at stake, specifically so that they cannot be renegotiated
later by someone emotionally invested in the answer.

**The strategy question.** At 100 recorded Trades, compute net expectancy and its 95%
confidence interval. Lower bound above zero: the evidence justifies revisiting the decision
not to fund this, at small size. Upper bound below zero: retire the strategy and keep the
engine. Interval straddling zero: continue to 250 Trades, then retire regardless of the
number. At ~1 Trade/day capacity this puts the first decision roughly five months out.

**The project question.** The project has succeeded when its owner can explain expectancy,
transaction costs, risk-based position sizing and multiple-comparisons bias without
reference material, and has caught at least one false winner in the research log. This is
what was actually purchased — education — and unlike a P&L target it cannot be faked by a
lucky month.

## Consequences

- These thresholds are not to be moved. A revision requires a new ADR that states what
  changed in the evidence, not in the appetite.
- 100 Trades can only distinguish a large edge from zero. An edge of +0.079%/Trade — the
  size actually measured during design — needs ~328 Trades to separate from zero, so a
  straddling interval at 100 is the expected outcome, not a disappointment.
- The two criteria can resolve in opposite directions: the strategy may be retired while
  the project is judged a success. That is the intended and most likely result.
