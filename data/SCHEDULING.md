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

Tennis has no daily slate boundary the way baseball does — tournaments run in
overlapping timezones and matches start from roughly 01:00 to 23:00 UTC. Three
runs a day keeps the slate fresh without much waste:

- **05:20 UTC** — catches the Australian and Asian swing before play
- **11:20 UTC** — European day session
- **17:20 UTC** — American evening session, and the US Open night matches

The ledger only ever freezes matches that have not started, so a run that
happens mid-session simply records fewer picks. Missing a run costs those
picks permanently; they cannot be re-recorded once play begins.
