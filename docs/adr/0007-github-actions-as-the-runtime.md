---
status: accepted
---

# GitHub Actions is the runtime, and the repository is the database

The lab must run unattended every trading day for months. GitHub Actions was chosen over
a VPS or a always-on home machine because it needs no host to maintain, its secrets store
already holds the Telegram credentials, and on a public repository the minutes are free.
Three of its properties are hostile to this workload, and the design answers each rather
than hoping.

**Scheduled runs are not punctual.** `schedule:` events are routinely delayed 5-15 minutes
and are dropped entirely under load. So nothing in `scanner/` consults the wall clock except
`candles.latest_complete()`, which asks the *data* which candle has finished. A run twelve
minutes late sees identical candles and does identical work; a dropped run is caught up by
the next one, which advances each Signal as far as the data allows in a single pass. Every
write is keyed on `(day, symbol)`, so repeated runs rewrite rows instead of duplicating them.

**Runners are ephemeral.** The Journal is CSV committed back to the repository by the
workflow. Git supplies an append-only history with server-side timestamps, which is exactly
what ADR-0003 and ADR-0005 require — a losing row cannot be quietly edited without the
change appearing in `git log`. The candle cache is *not* committed: it is rebuildable, and
committing 1.5 MB of churn twenty-four times a day would bloat the repository within weeks.

**No webhook can be hosted.** Telegram updates must therefore be polled with `getUpdates`,
which is why Picks are text replies rather than inline-keyboard buttons. A `callback_query`
carries no press timestamp — its `message.date` is when the alert was sent — so a button tap
cannot be proven to have happened inside the ADR-0004 window. A text reply carries Telegram's
own `date` for when the user sent it, which the user cannot forge, and the deadline is checked
against that rather than against when the workflow ran.

## Considered options

- **Self-hosted runner on the user's machine.** Punctual, residential IP, no NSE blocking
  risk. Rejected because it requires the machine to be awake 10:30-16:15 IST daily, which is
  precisely the reliability problem being outsourced.
- **A small VPS with cron.** Punctual and persistent. Rejected as an ongoing cost and another
  thing to patch, for a lab whose expected outcome is retirement of the strategy.

## Consequences

- **The NSE feed may refuse datacenter IPs.** `charting.nseindia.com` answers from a
  residential connection; it may not answer from Azure. `.github/workflows/probe.yml` must be
  run and pass before anything here is trusted. If it fails, the fallback is a self-hosted
  runner — the code is unchanged, only the `runs-on` line.
- A Signal alerted after its window has already shut is banked as `NO_RESPONSE` with reason
  `WINDOW_MISSED`, so GitHub's unreliability shows up as data in the Journal rather than as
  silently widened Pick windows.
- Twenty-four runs per session at roughly 1-2 minutes each is ~800 minutes/month: free on a
  public repository, and inside the 2,000-minute free allowance on a private one.
- Queued runs can race on push, so the scan workflow retries `pull --rebase` three times, and
  `concurrency.cancel-in-progress` is false — a cancelled run would lose a Pick window.

## Amendment — `schedule:` cannot drive 15-minute alerting, so it no longer tries

This ADR assumed a delayed cron was survivable because nothing keys off wall-clock time.
That held, but the failure turned out to be quantity rather than lateness. Measured over
five trading days on this repository, GitHub fired **2 of the 24 requested scheduled runs
per day**. Run numbers were sequential (#4 … #12), so the missing 22 events were never
created: nothing errored, nothing was cancelled, they were silently discarded. Scheduled
events are best-effort, are deprioritised on private and low-activity repositories, and
`:00/:15/:30/:45` are the platform's most congested minutes.

Timing therefore no longer comes from the scheduler. `scanner/watch.py` is one long-lived
job that starts in the morning and sleeps to each candle boundary itself, which is exact.
The scheduler is used once a day purely to start it, with two later backup crons in case
that one event is among those discarded. A late start now costs the first candle or two
rather than the whole session.

## Consequences

- **The repository must stay public.** The watcher is alive ~5h15m per trading day and
  GitHub bills wall-clock time including sleep — roughly 6,600 minutes/month against a
  2,000-minute free allowance for private repositories. Public repositories have no
  limit. Making this repository private again costs about $39/month, so if that is ever
  wanted, revert to short runs triggered externally via `repository_dispatch` first.
- Actions secrets remain private on a public repository, and the workflows trigger only on
  `schedule` and `workflow_dispatch`. Neither can be initiated by an outside contributor,
  so no fork-supplied code ever runs with the Telegram credentials in scope.
- The Journal is committed after **every** pass, not at the end. A six-hour job that
  commits once on exit loses an entire session to a single crash or cancellation.
- A backup cron that queues behind the live watcher starts after the close, sees the
  session is over and exits in seconds. That is intended, and costs about a minute.
