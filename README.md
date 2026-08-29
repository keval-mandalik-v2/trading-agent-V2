# 15-Minute Inside-Bar Lab

A scanner and backtester for a 15-minute opening-range breakout pattern on the Nifty 50,
running on GitHub Actions and alerting to Telegram.

**It is a lab, not a trading system.** Measured over 23 sessions and 88 Signals with a real
Indian intraday cost model, the pattern's net expectancy is **+0.006% per Signal** — a
95% confidence interval of −0.13% to +0.14%, which is indistinguishable from zero. It is not
funded, and [ADR-0006](docs/adr/0006-pre-registered-stopping-rule.md) sets out in advance what
evidence would change that.

Read [CONTEXT.md](CONTEXT.md) for the vocabulary and [docs/adr/](docs/adr/) for why each
decision was made. The ADRs are the reason the code looks the way it does.

## What it does

Each trading day, for every Nifty 50 stock:

1. **Candle 1** (09:15–09:30) sets a reference high and low.
2. If candles 2–5 (09:30–10:30) all stay fully inside that range — wicks included — the
   session is a **Coil**. A single **Poke** disqualifies it.
3. From 10:30 to 13:00, the first candle to *close* beyond Candle 1's range produces a
   **Signal**. Entry is the **open of the next candle** — never the breakout candle's own
   close, which is a price nobody could have got.
4. Target is 1%; Stop is Candle 1's opposite extreme; anything unresolved by the 15:15
   square-off is a **Fizzle** (about two thirds of all Signals).
5. The Signal is alerted with its **Breakeven Hit Rate** and a **Base Rate** from the
   Journal. There is no confidence score — see
   [ADR-0002](docs/adr/0002-base-rates-not-a-confidence-score.md).
6. You reply `TAKE <SYMBOL>` or `SKIP <SYMBOL> <reason>` **before the next candle closes**.
   A later reply is recorded as `LATE` and does not count.

## One-time setup

```bash
# 1. Make it a repo and push it
git init -b main
git add .
git commit -m "15-minute inside-bar lab"
git remote add origin git@github.com:<you>/<repo>.git
git push -u origin main
```

**2. Create a Telegram bot.** Message [@BotFather](https://t.me/BotFather), send
`/newbot`, and keep the token. Then message your new bot once, and open
`https://api.telegram.org/bot<TOKEN>/getUpdates` to read your numeric chat id.

**3. Add two repository secrets** under *Settings → Secrets and variables → Actions*:

| secret | value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | the token from BotFather |
| `TELEGRAM_CHAT_ID` | your numeric chat id |

**4. Run `probe-nse-reachability` manually and check it passes.** This is not optional.
NSE may refuse GitHub's datacenter IPs, in which case nothing else here can work on a
hosted runner — see
[ADR-0007](docs/adr/0007-github-actions-as-the-runtime.md) for the fallback.

**5. Run `backtest` manually** with *fetch* ticked, to populate the universe and see the
current numbers.

The `scan` workflow then runs itself every 15 minutes, 05:00–10:45 UTC, Monday to Friday
(10:30–16:15 IST).

## Running it locally

```bash
pip install -r requirements.txt

python -m scanner.backtest --fetch --factors      # measure
python -m scanner.scan --no-telegram              # a real run, printing instead of sending
python -m scanner.scan --at "2026-08-27 11:20" --offline --no-telegram   # replay a session
```

`--offline` uses only cached candles and never calls NSE, which makes replaying a past
session fast.

## Layout

```
scanner/rules.py      the rule engine -- ONE implementation, shared by backtest and live
scanner/candles.py    fetching, caching, and the two real defects in this feed
scanner/money.py      the actual Indian intraday cost stack, and position sizing
scanner/journal.py    the Journal, Base Rates, universe, research log
scanner/telegram.py   alerts out, Picks in (text replies, for a forgery-proof timestamp)
scanner/scan.py       the scheduled run -- idempotent, catch-up safe
scanner/backtest.py   the historical harness
tools/probe_reachability.py   run this on Actions first
nse_chart_data.py     the charting.nseindia.com client this is all built on
data/signals.csv      the Journal (committed)
data/picks.csv        your Picks, with validity stamps (committed)
data/candles/         rebuildable cache (gitignored)
docs/research-log.md  every hypothesis ever tested, failures included (ADR-0005)
```

## Three things to know before you trust any number in here

**Position sizing matters more than the strategy.** The identical 88 Signals ended at
₹21,975 with a 21% drawdown at fixed 5x leverage, and ₹20,641 with a 3.6% drawdown risking
1% of equity per Trade. Fixed size asks "how much can I buy?"; risk-based sizing asks
"how much can I lose?" and works backwards. Run `python -m scanner.backtest` to see the
whole table.

**A Hit Rate without its Target and Stop is meaningless.** At a 0.5% Target this pattern
hits 77% of decided Trades and loses money *faster* than at 1%, where it hits 58%. Any win
rate quoted without both levels attached is not information.

**Count the variants.** Forty-three were tested against these same 88 Signals during
design, and a permutation test put the odds of any one looking profitable by luck at about
1 in 9 — so four false winners were expected, and four appeared. That is why
[ADR-0005](docs/adr/0005-every-hypothesis-is-recorded.md) makes the research log
append-only. The right first question about any backtest, including this one, is never
"what were the returns?" but "how many things did you try?"
