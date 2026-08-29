# Intraday Inside-Bar Scanner

A system that detects a specific 15-minute opening-range candlestick pattern across a
universe of NSE equities, measures whether acting on it is profitable, and — only if it
is — alerts on it live. The measuring half and the alerting half share one rule engine so
that what is backtested is exactly what is traded.

## Language

### The pattern

**Candle 1**:
The 09:15–09:30 IST candle of a session. Its high and low define the reference range that
every later rule in the day is measured against.
_Avoid_: opening candle, first bar, ORB candle

**Coil**:
A session in which candles 2 through 5 (09:30–10:30) each have their **full range** — wicks
included — within Candle 1's high and low, touching those bounds being permitted. The
pattern's precondition; a Coil is an observation, not yet a reason to act.
_Avoid_: inside bar, consolidation, box, range, setup

**Poke**:
A candle in the 09:30–10:30 window whose wick exceeds Candle 1's range. A single Poke
disqualifies the session — there is no partial Coil.
_Avoid_: breach, violation, false break

**Signal**:
A Coil in which a later candle closes outside Candle 1's range, together with the
direction that close implies. Carries an entry, a target and a stop.
_Avoid_: alert, setup, trigger, entry, trade

**Trade**:
A Signal that was acted on with real money. Most Signals never become Trades — capital
limits how many can be held at once.
_Avoid_: position, order, execution

### Outcomes

**Target**:
The price 1% beyond entry in the Signal's direction. Reaching it is the only outcome
counted as a win.
_Avoid_: profit level, TP, exit

**Stop**:
Candle 1's opposite extreme — its low for a long Signal, its high for a short one. Note
that its distance is chosen by the market, not by us.
_Avoid_: SL, stoploss, risk level

**Fizzle**:
A Signal that reaches neither Target nor Stop before the session ends and is closed out at
whatever price the market offers. Empirically the most common outcome, so it needs a name.
_Avoid_: timeout, no-result, breakeven, scratch

**Hit Rate**:
The proportion of Signals that reach Target. Always stated against Signals — never against
Coils, and never against Signals-excluding-Fizzles, because those three numbers differ by
several times.
_Avoid_: accuracy, win rate, success rate, strike rate

**Journal**:
The durable record of every Signal the system ever emitted together with what subsequently
happened to it. The sole source of Base Rates, and the reason the system is worth running
before it is worth trading.
_Avoid_: log, history, database, trade log

**Base Rate**:
The realised expectancy of past Signals resembling a given Signal, drawn from the Journal
and always stated with its sample size. Replaces any invented confidence score; reads
"insufficient data" until the sample supports a claim.
_Avoid_: confidence score, probability, conviction, signal quality

**Pick**:
The user's TAKE or SKIP answer to a Signal, recorded before the next candle closes and
never editable afterwards. An answer given later is not a Pick.
_Avoid_: decision, selection, entry, vote

**Research Log**:
The append-only record of every hypothesis ever tested against the Journal, its result and
its date, failures included. Any claimed edge is meaningless without the count of variants
tried to reach it.
_Avoid_: experiments, notes, findings

**Breakeven Hit Rate**:
The Hit Rate at which a set of Signals makes exactly zero rupees, derived from the ratio of
Stop distance to Target distance plus costs. The number any claimed Hit Rate must be
compared against before it means anything.
_Avoid_: edge, expectancy threshold
