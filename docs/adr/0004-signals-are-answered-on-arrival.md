---
status: accepted
---

# A Signal is answered on arrival, within one candle, or recorded as unanswered

Signals arrive between 10:45 and 13:15, with a median of 11:15 and only 11% actionable at
10:45. Deciding from the full day's list would mean choosing after the early Signals had
visibly played out — the human equivalent of lookahead bias, and enough to make the
Journal's pick column worthless. So each Signal is answered TAKE or SKIP before the next
15-minute candle closes; after that the row locks as NO RESPONSE and cannot be edited. The
decision is therefore always made on the same information a live trader would have had.

## Consequences

- Requires the user to respond within 15 minutes, several times a session. This is the most
  likely part of the system to be abandoned, and abandonment is visible in the Journal as a
  run of NO RESPONSE rather than as silence.
- Median hold time is 12 candles (~3 hours) and only one position can be held on ₹1,00,000
  of buying power, so most Signals will be answered SKIP for capacity reasons alone. SKIP
  therefore records a reason code, or the pick column measures capital limits rather than
  judgment.
- The 10:45 cohort, which the strategy is named after, had the worst measured expectancy of
  any arrival time (net -0.212%). Nothing in the protocol privileges early Signals.
