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
