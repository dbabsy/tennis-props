# tennis-props

Five tennis pages covering ATP and WTA, published to GitHub Pages and rebuilt
on a schedule.

| Script | Page | What it answers |
|---|---|---|
| `build.py` | `index.html` | How the air at each venue moves serve outcomes |
| `build.py` | `matches.html` | Win probability, total games, set scores |
| `build.py` | `props.html` | Ace and double-fault projections |
| `build.py` | `edges.html` | Fair price for every market the model supports |
| `ledger.py` | `accuracy.html` | Keeps score of what the projections did |

`fetch.py` pulls raw data, `ratings.py` fits serve/return ratings, `model.py`
propagates points to matches, `project.py` joins them for a slate.
`backtest.py` and `sweep*.py` are the measurement tools, not part of the build.

## Run it

```bash
python3 build.py --out public
python3 ledger.py --all --out public/accuracy.html
python3 backtest.py --tour atp --year 2025
```

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

**Ace counts are ~1.43× wider than binomial (ATP), 1.31× (WTA).** Measured as
the SD of the z-score against fitted rates, n≈4,900 player-matches per tour.
Double faults are 1.19 and 1.16. Pricing an ace over off a binomial understates
every over. `model.count_dist` uses a moment-matched beta-binomial.

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
row. There is a manual check: copy `ledger/picks.json`, wipe it, call
`record(now=<after every start time>)`, and confirm it writes nothing.

**ESPN keys the scoreboard by tournament, not by match.** One call returns the
whole draw, played and unplayed. `?dates=` takes any date and returns that
week's events, which is how the post-May 2026 gap gets filled. Scoring walks
three weeks back because a slam runs a fortnight.

**Half a slam's draw is `TBD`.** Of 127 unplayed men's singles matches at a
slam, 63 have an undetermined side. That is not a resolver bug.

**`hash()` is salted per process.** `backtest.py` orients matches with
`zlib.crc32` so runs are reproducible. Using `hash()` makes every backtest
disagree with the last one.

**Backtest orientation must be randomised.** Always putting the winner first
makes every label a 1, which leaves log loss valid but calibration meaningless.

## Open questions

- Whether the archive mirror will keep updating. If it stops, serve statistics
  end permanently and the ace model decays. The Match Charting Project is the
  fallback but covers far fewer matches.
- The gap to the closing line is strikingly stable: +0.033, +0.033, +0.032,
  +0.028 across ATP and WTA in 2024 and 2025 (n=1,700-1,930 each). That
  consistency is reassuring but it also means no configuration tried so far
  moves it. Closing it needs information the point model does not have --
  injuries, retirements, travel, motivation in dead rubbers.
- WTA is the softer market: the model's raw accuracy is within a point of the
  market's there (65.5% vs 66.2%) versus three points behind on ATP. If there
  is an edge anywhere it is likelier on WTA, but the log-loss gap says the
  market is still better calibrated.
- Live in-match probability is not built. ESPN linescores update during play
  but not per point, so it would be game-level, not point-level.
