# tennis-props

Five tennis pages covering ATP and WTA, published to GitHub Pages and rebuilt
on a schedule.

| Script | Page | What it answers |
|---|---|---|
| `build.py` | `index.html` | How the air at each venue moves serve outcomes |
| `build.py` | `live.html` | Win probability for matches on court right now |
| `build.py` | `matches.html` | Win probability, total games, set scores |
| `build.py` | `props.html` | Ace and double-fault projections |
| `build.py` | `edges.html` | Fair price for every market the model supports |
| `ledger.py` | `accuracy.html` | Keeps score of what the projections did |

`fetch.py` pulls raw data, `ratings.py` fits serve/return ratings, `model.py`
propagates points to matches, `project.py` joins them for a slate.
`backtest.py` and `sweep*.py` are the measurement tools, not part of the build.
`selftest.py` is the offline check that runs in CI before a build.

## Run it

```bash
python3 selftest.py                          # offline, no network, seconds
python3 build.py --out public
python3 ledger.py --all --out public/accuracy.html
python3 backtest.py --tour atp --year 2025
```

`backtest.collect` does the expensive part -- refitting the ratings for every
week of a season -- and caches the resulting rows on disk, keyed by the source
of `ratings.py` and `model.py` as well as by the arguments. So a sweep over
anything applied *after* the point model is instant, and editing either file
invalidates the cache instead of silently comparing new code against old
numbers. `summarize` scores the match odds, the games distribution at the lines
the site publishes, and the ace and double-fault projections, split by format
and by surface. Anything that is not measured there does not get changed.

`build.py` takes `--date YYYY-MM-DD`. Building a past date is how the model
was backtested.

## Where the data comes from

**Jeff Sackmann's `tennis_atp` and `tennis_wta` repos are gone.** His account
went from many public repos to one during 2026. Every tennis model on the
internet is built on those files, so expect any tutorial you find to 404.

| Source | Gives | Covers |
|---|---|---|
| `Aneeshers/tennis-sackmann-archive` | per-match serve stats: aces, DFs, service points, 1st in/won, 2nd won, service games, BP saved/faced | ATP+WTA 1968 → **2026-05-25** |
| ESPN scoreboard | full draws, set scores with tiebreaks, court, start time | live, and any past date via `?dates=` |
| tennis-data.co.uk | results plus **closing odds** (Pinnacle, Bet365, Betfair) | weekly, both tours |
| Match Charting Project | point-level serve direction, return depth, rally length | charted subset, still updated |
| Open-Meteo | temperature, humidity, pressure, wind, elevation | anywhere, current and historical |

**The archive stops at Roland Garros 2026.** Grass season onward has results
via ESPN but no serve statistics, so ace projections lean on rates fitted
before June. Re-check whether the mirror has caught up before assuming an ace
number is current.

No API keys anywhere. There is no keyless source of live odds, which is why
`edges.html` prices markets rather than claiming edges.

## Decisions that took measurement to reach

Do not undo these without re-measuring. Several are counter-intuitive.

**Return ratings are subtracted, not added.** The fitted model is
`spw = surface_base + serve_i - return_j`, with both signed so positive is good
at your own job. An early version added the returner's rating, which made elite
returners inflate their opponent's serve. It did not look obviously broken —
the ratings themselves were perfect, Sinner and Alcaraz on top — but predictions
were pure noise: log loss 1.084, accuracy 0.468, and every calibration bucket
came out at 0.50. Fixing the sign took it to log loss 0.625 and accuracy 0.642.
A plausible-looking rating table proves nothing about the prediction path.

**Day-to-day form is integrated over, not assumed away.** Using a point
estimate of serve percentage makes every match look more evenly contested than
it will be, and even matches run long — the model over-predicted total games by
**+1.8** for exactly this reason. Integrating over `sigma=0.05` of match-level
variation in serve percentage (`model.match_dist_form`) cuts the bias to +0.6,
improves games MAE in both test years, and moves match log loss by less than
0.004 either way. The measured unexplained match-level SD is ~0.049, which is
where the constant comes from.

**Two obvious fixes for that bias were tried and rejected.** Stretching the
skill gap (`gap_mult`) does reduce it, but pays for it in match accuracy —
log loss 0.625 → 0.649 to buy one game. Shifting the level down is nearly free
in log loss but would need −0.075 to close the gap, which puts mean serve
points won at .562 and corrupts every serve-volume-derived prop. Both knobs are
still in `ratings.py`, both default to off.

**The shrinkage constants barely matter.** A 24-point grid over `K_SERVE`,
`K_RETURN` and `K_SURFACE` moved held-out log loss by 0.006 total and left the
games bias unchanged everywhere. Do not spend time tuning them; the model is
not shrinkage-limited. This was worth knowing and is worth not rediscovering.

**An ace rate belongs to the surface at least as much as to the server.** On
the ATP the clay ace rate is 0.67× the hard-court rate and grass is 1.19×; the
model used a single blended rate on all three. The cost was not subtle -- ace
projections came out **+1.2 to +1.4 aces per player-match too high on clay and
1.5 to 2.1 too low on grass**, in every season measured. Fitting the rate per
surface collapses that spread to under half an ace and cuts ace MAE by 7-8% on
the ATP.

The player's own effect is *multiplicative*, not additive: among ATP players
with a real sample on both, the spread of clay ace rates is 0.78 of the spread
on hard while the mean is 0.67 of it -- much closer to scaling with the surface
than to surviving it. So `_fit_counts` fits `(rate - base) / base` and a player
carries a percentage from one surface to the next, not a count.

**The ace opponent adjustment has to iterate, like serve and return do.** The
server term and the returner term used to be regressed on the same raw residual
in one pass, neither told about the other -- so a returner who happened to face
big servers was credited with allowing aces that were really the servers' doing,
and then `ace_rate` added both back together and counted the server twice. It
looked like the serve/return machinery and was not.

**Ace counts are ~1.37× wider than binomial (ATP), 1.26× (WTA).** Measured as
the SD of the z-score against fitted rates, n≈14,000 player-matches per tour
over 2023-2025. Double faults are 1.14 and 1.15. Pricing an ace over off a
binomial understates every over; `model.count_dist` uses a moment-matched
beta-binomial. These numbers came *down* when the rate model learned about
surfaces, which is the expected direction: overdispersion measured against a
model is partly the model's own error. Re-measure them whenever the rate model
changes -- `sweep3.py` prints them off the same walk-forward pass.

**Air density genuinely moves serve outcomes, and only shows up after
opponent adjustment.** Ace rate −0.042 per kg/m³ (SE 0.005, t = −8.4), serve
points won −0.067 (SE 0.009, t = −7.4), n=39,085 outdoor player-matches. The
*raw* slopes are indistinguishable from zero — high-altitude events draw fields
that mask the effect. But r is only −0.04: this is a nudge, not an edge. Indoor
events are excluded and act as the control group.

**Counting props are a rate times an opportunity, and the opportunity is the
hard part.** `model.serve_volume` derives expected service points from the same
match model, carrying set-to-set serve parity. A player about to lose in
straight sets does not serve enough to clear a big ace line.

**The opportunity has to be integrated over form too, or the two halves of a
projection disagree about the same afternoon.** The match distribution was
integrated over day-to-day variation in serve percentage and the serve volume
was not. Expected points in a service game peak when the two players are level,
so the fixed-form volume quietly credited every match with more serving than it
would get: **+5.3 to +6.6 service points per player per match**, about four
percent on every ace and double-fault line and all in the same direction.
Making `serve_volume_form` share the grid takes that to +0.5 to +2.1. This was
not a modelling choice anyone made; it was one call that never got the argument
the other one got.

**Best-of-five is a different serve environment, not just a longer one.**
Everybody holds less at a slam than the same pair would over three sets, and it
survives the opponent adjustment: pooled over 2024-2025 the ATP residual is
about −0.010 of serve points won against best-of-three, consistently signed on
hard, clay and grass. The single blended baseline is dominated by best-of-three,
so slam matches got more holds than they get and were predicted long -- a
**+1.6 games bias on best-of-five against +0.5 on best-of-three**, which is
essentially the whole of the reported bias. The blended number hid it, which is
why `summarize` now always prints the split. `ratings._fit_format` fits the
shift as a deviation from the overall residual, so it re-weights the formats
against each other without moving the level. It takes best-of-five to +1.15.
The rest of that gap is still open.

**All four slams decide a 6-6 final set over ten points, not seven.**
`match_dist` has taken `final_set_tb_target` all along and nothing ever passed
it. It is a small effect on games and a real one on who wins a deciding set.

**A live probability is not a second model, it is the same walk started
later.** `match_dist` propagates forward from the first point, so conditioning
on a score is `live_dist` entering the same DP at that state -- at 0-0 it
returns the pre-match number to twelve decimal places, which `selftest.py`
asserts. Anything else would put two numbers for one match on the same site
with no way to say which was wrong.

**Who is serving is worth more than the scoreline.** At one set all and 5-4 in
the decider, the same score is a **0.93 win for the server and 0.66 for the
receiver**. ESPN does not reliably say, so `fetch._espn_match` looks in several
places for it and reports `None` rather than guessing; the page then averages
the two entries, which is blunter but not wrong. If a reliable source of the
server turns up it is the single biggest improvement available to that page.

**The live page ships the answers, not the model.** `model.live_table`
precomputes P(A wins) for every state a match can reach -- 39 game scores by
at most 9 set scores by 2 servers, so 234 states for a best-of-three and 702
for a best-of-five -- and `encode_table` packs each into two base-36
characters. About a kilobyte a match. The browser learns the score and does a
lookup; it never propagates anything. A second implementation of the
propagation in JavaScript would be a second thing to keep correct, and
`selftest.py` runs the shipped script under `node` against the shipped table
to prove the two halves still agree on the index arithmetic.

**Building a live table is the expensive part of the build, so most matches do
not get one.** With form integration a best-of-five table costs about 1.4
seconds. `project.wants_live` builds one only for matches on court or starting
within three hours, which is what keeps a slam day from paying for a hundred
tables it will never read.

**Serve alternates across the set boundary.** Whoever received the last game of
a set serves the first game of the next. Getting this wrong is invisible in win
probability and visible in set scores — it is why 6-3 is more likely than 6-4
for a set's first server (6-3 ends on their own serve; 6-4 needs a break).

**The site is themed after whatever is actually being played.** `themes.py`
maps the slate's busiest tournament to a palette — the four slams have their
own, everything else falls back to its surface, and roofed events get the
indoor palette. Colours come from the courts, not from tournament branding,
which keeps this well clear of anyone's trade marks. Every palette defines both
a light and a dark set and all sixteen combinations were checked to meet
WCAG AA (body text ≥14.8:1, accents ≥4.5:1); a palette that only works in one
mode is a bug. Text colour is deliberately not themed — contrast against a
tinted background is the one thing that must not vary by event.

`build.py` writes the chosen theme to `data/theme.json` so `ledger.py` can
match it. Without that the accuracy page would look like a different site.

**Players below 300 tour-level serve points are refused, not guessed.** About
20% of a slam's first round is qualifiers and wildcards the model has never
seen. Pricing them anyway is worse than skipping them.

## Gotchas that have bitten before

**ESPN 403s any user agent that claims to be a browser.** Its bot check is
inverted: `curl/8.7.1` and no-user-agent both work, a Chrome string does not.
`fetch.UA` is deliberately a plain honest agent. Do not "fix" a 403 by pasting
in a browser string — that is what causes it.

**The ledger must never record a match that has started.** `ledger.record`
refuses any match whose start time has passed and never rewrites an existing
row. This used to be a manual check; it is now the first thing `selftest.py`
asserts, with a stubbed slate, so CI fails before a build rather than after.

**A retirement has a winner but not a games total.** ESPN reports the partial
sets, and scoring those against a full-match projection is a straight
subtraction from the model -- a match abandoned in the second set did not play
a short match. `ledger._clean` decides it from the set scores rather than from
ESPN's status wording: a completed match ends the moment one side holds the
sets it needs, so anything short of that was abandoned however the string
happens to be phrased. Retirements still count towards the win columns, which
do have an answer.

**ESPN orders the two competitors independently of how a pick was stored.** The
ledger reads the orientation off the names -- and now confirms both of them. A
single unmatched name used to fall through to "then it must be the other one",
which inverts the result rather than skipping the row.

**The market benchmark can go missing without anything looking broken.** If
tennis-data.co.uk is unreachable, `market_index` returns nothing, every match
fails to join, and the backtest prints a perfectly reasonable-looking set of
model-only numbers with a quiet `0 matched to closing odds`. It now says so in
capitals instead. Beating nothing is not a result.

**ESPN keys the scoreboard by tournament, not by match.** One call returns the
whole draw, played and unplayed. `?dates=` takes any date and returns that
week's events, which is how the post-May 2026 gap gets filled. Scoring walks
three weeks back because a slam runs a fortnight.

**The live page must never be able to write to the ledger.** It is the one
feature whose whole job is to look at matches that have already started, which
is exactly what `ledger.record` refuses to touch. `P.build` therefore takes
`states` and defaults to `("pre",)`; only `build.py` asks for `"in"`, and the
ledger never does. Two guards, because one would be enough right up until
somebody changed the default.

**Whether a browser can reach ESPN at all is unverified.** The live page
refreshes by fetching the scoreboard from the reader's browser, which needs
ESPN to send a permissive `Access-Control-Allow-Origin`, and this repo's other
note says ESPN 403s browser user agents -- a browser cannot spoof that header,
so if the bot check is not IP-conditional the fetch will fail. It degrades
honestly: the page renders the scores it was built with, and the note under
the table says the refresh is unavailable rather than letting a stale number
look live. Confirm it in a real browser before trusting the word "live".

**A name that differs only in spacing is the same player, and used to be a
different one.** The archive writes "Xin Yu Wang", ESPN writes "Xinyu Wang".
The surname-plus-initial fallback assumes the first token is the given name
and the rest is the surname, so the archive spelling filed her under
`("yuwang", "x")` and she never appeared in the `("wang", "x")` bucket at all
-- which held only Xiyu Wang, who was therefore returned as an unambiguous
hit. `Resolver` now indexes the full name with the spaces removed and tries
that before anything that guesses. A wrong id is worse than a missing row:
the match is still priced, just for somebody else, and nothing downstream can
tell.

**An ambiguous surname is refused unless one candidate dwarfs the rest.** The
usual collision is a tour regular against somebody with a handful of
qualifying matches, which is safe to call; two established players who
collide are not, so they are skipped instead.

**Every dropped match now says why it was dropped.** `project.build` returns
the matches it could not price alongside the ones it could, and `build.py`
prints them grouped by reason. A silently skipped match looks exactly like a
match that was never on the scoreboard, which makes "are we showing
everything?" unanswerable.

**Half a slam's draw is `TBD`.** Of 127 unplayed men's singles matches at a
slam, 63 have an undetermined side. That is not a resolver bug, and the skip
report counts it separately for that reason: on a US Open day 32 of the 33
"unresolved" ATP matches were placeholders, and the one real name miss was
invisible among them. Groups print smallest first, because the short ones are
the ones somebody can act on.

**`hash()` is salted per process.** `backtest.py` orients matches with
`zlib.crc32` so runs are reproducible. Using `hash()` makes every backtest
disagree with the last one.

**Backtest orientation must be randomised.** Always putting the winner first
makes every label a 1, which leaves log loss valid but calibration meaningless.

## Scheduling

The build is triggered every two hours from cron-job.org on
`America/Chicago`; `data/SCHEDULING.md` has the endpoint, the token scope and
the measured costs. The `schedule:` block in `build.yml` is a UTC-only
backstop and deliberately does not track Central.

Cadence is chosen for one reason: the ledger refuses to record a match that has
already started, so anything scheduled and started between two runs loses its
frozen prediction permanently. Shortening the gap only adds coverage. This is
the number that decides how fast the accuracy page becomes readable.

## Open questions

- Whether the archive mirror will keep updating. If it stops, serve statistics
  end permanently and the ace model decays. The Match Charting Project is the
  fallback but covers far fewer matches.
- **The gap to the closing line has not been re-measured since these changes.**
  tennis-data.co.uk was unreachable when they were made, so every number above
  is model-only. The changes improve held-out log loss, so the gap should have
  narrowed, but "should have" is not a measurement. Re-run
  `backtest.py --tour atp --year 2025` somewhere with access before quoting a
  gap.
- Best-of-five still carries a +1.15 games bias against +0.4 for
  best-of-three, down from +1.65 but not gone. The format shift and the gap
  stretch each took a piece of it. What is left looks like the model producing
  too few straight-set slam matches: 42.8% of completed ATP best-of-fives in
  2024-2025 ended in three sets. A wider `FORM_SIGMA` for best-of-five is the
  obvious next thing to sweep -- four hours is more room to drift than ninety
  minutes -- but it has not been tried.
- Totals still lean over: the model prices 52.7% of best-of-three overs where
  46.9% land (ATP 2024), and the same 4-5 point lean shows on the WTA. That is
  the residual games bias, and it is the one number on the site a reader could
  act on directly.
- The gap to the closing line is strikingly stable: +0.033, +0.033, +0.032,
  +0.028 across ATP and WTA in 2024 and 2025 (n=1,700-1,930 each). That
  consistency is reassuring but it also means no configuration tried so far
  moves it. Closing it needs information the point model does not have --
  injuries, retirements, travel, motivation in dead rubbers.
- WTA is the softer market: the model's raw accuracy is within a point of the
  market's there (65.5% vs 66.2%) versus three points behind on ATP. If there
  is an edge anywhere it is likelier on WTA, but the log-loss gap says the
  market is still better calibrated.
- The WTA may want a bigger gap stretch than the ATP -- its 2023 and 2024
  residual optima both land at 1.21 -- but 2025 says 1.04, so a per-tour
  constant is not supported by three seasons. Worth revisiting with a fourth.
- Live in-match probability is game-level, because ESPN's linescores are. A
  point-level version would need a point-by-point feed nobody offers without a
  key, and would mostly buy resolution inside a game the model already prices
  from its endpoints.
- The live page has never been watched against a real in-progress match from a
  real browser. Everything about it is verified offline -- the model against
  `match_dist`, the JavaScript against the model under `node` -- but whether
  ESPN answers a browser is untested, and so is whether it reports the server.
