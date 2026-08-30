# Triggering the build

GitHub's own scheduler is unreliable on the sibling repos — measured at roughly
15% of requested runs on `mlb-wind`, then 47 hours with none while the workflow
was active and Actions healthy. Assume the same here. The `schedule:` block in
`build.yml` is left in only as a free backstop.

`workflow_dispatch` has never failed. Trigger the build from outside GitHub.

## The endpoint

```
POST https://api.github.com/repos/dbabsy/tennis-props/actions/workflows/build.yml/dispatches
```

| Header | Value |
|---|---|
| `Accept` | `application/vnd.github+json` |
| `Authorization` | `Bearer YOUR_TOKEN` |
| `X-GitHub-Api-Version` | `2022-11-28` |
| `Content-Type` | `application/json` |

Body: `{"ref": "main"}`

Success is **HTTP 204 No Content** with an empty body. 401 is a bad or expired
token; 404 usually means the token lacks Actions permission on this repo rather
than a wrong URL.

## The token

A **fine-grained personal access token**, scoped as narrowly as the job needs:

- Repository access: **only** `dbabsy/tennis-props`
- Permission: **Actions → Read and write**, nothing else

This is a second token from the `mlb-wind` one — a fine-grained token is bound
to the repositories you pick, so the existing one will 404 here until
`tennis-props` is added to its repository list. Adding this repo to the
existing token is fine and avoids managing two.

## When to run it

**Every two hours, timezone `America/Chicago`, at minute 20.**

Tennis has no daily slate boundary the way baseball does. Tournaments run in
overlapping timezones and matches start at essentially every hour of the day,
so there is no clever set of three times that covers the tour — the honest
answer is to run often and let the ledger sort it out.

The reason the cadence matters is one-directional. The ledger refuses to record
a match that has already started, so any match scheduled *and* started inside a
gap between runs never gets a frozen prediction, and it cannot be recovered
afterwards. A two-hour cadence caps that loss at two hours. Running more often
only ever adds coverage; running less often silently discards picks.

Central rather than UTC is a deliberate, harmless choice. cron-job.org handles
the DST transition for `America/Chicago` itself, and at a two-hour cadence the
one-hour seasonal shift is not worth reasoning about. The `schedule:` block in
`build.yml` is UTC-only and cannot follow it, which is fine for a backstop.

Costs, measured rather than assumed, at a two-hour cadence (12 runs/day):

| | per run | per day | limit |
|---|---|---|---|
| Actions runtime | ~30 s | ~6 min | unlimited on a public repo |
| Downloads | 14.8 MB | ~178 MB | no hard cap; 10 requests/hour |
| Open-Meteo calls | 2–4 | ~40 | 10,000/day free |
| Pages deploys | 1 | 12 | 10/hour soft limit |

The ledger file grows about 510 bytes per pick, or ~2.9 MB a year across both
tours. Expect several ledger commits a day during a slam and almost none in
December — the build only commits when `picks.json` actually changes.
