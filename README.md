# tennis-props

Point-model projections and prop pricing for ATP and WTA singles.

Each player's serve and return rates are opponent- and surface-adjusted, then
propagated point → game → tiebreak → set → match. One engine produces match
odds, total games, set scores, games handicaps and straight-sets from the same
distribution, and serve volume for the ace and double-fault props.

**Pages:** [conditions](index.html) · [live](live.html) ·
[matches](matches.html) · [props](props.html) · [fair prices](edges.html) ·
[accuracy](accuracy.html)

The live page prices matches that are on court, by entering the same
propagation at the current score instead of at the first point — at 0-0 it
returns exactly the pre-match number. Every state a match can reach is
precomputed at build time and packed into about a kilobyte, so the browser
does a lookup rather than a second implementation of the model.

Measured by walk-forward backtest, ratings refit weekly on data strictly
before each match. Log loss and MAE; lower is better.

| | matches | log loss | games MAE | games bias | ace MAE | ace bias |
|---|---|---|---|---|---|---|
| ATP 2024 | 2,487 | 0.6193 | 5.53 | +0.61 | 2.79 | −0.27 |
| ATP 2025 | 2,201 | 0.6230 | 5.54 | +0.48 | 2.83 | −0.17 |
| WTA 2024 | 2,131 | 0.6228 | 5.06 | +0.75 | 1.63 | +0.01 |
| WTA 2025 | 2,027 | 0.6324 | 4.96 | +0.72 | 1.78 | +0.01 |

Ace numbers are per player-match. The same walk-forward pass, before the
current round of fixes, gave 0.6217 / 0.6239 / 0.6259 / 0.6337 on log loss and
3.04 / 3.06 / 1.73 / 1.84 on ace MAE — the props moved much further than the
match odds did, because the rate model had no idea what surface it was on.

**Against the closing line:** the last measurement with odds access put the
model 0.028–0.033 of log loss behind Pinnacle/Bet365 across both tours in 2024
and 2025 (n=1,700–1,930 per cell), which is the hurdle a price has to clear
before it is an edge. That comparison has *not* been re-run since the current
changes, because tennis-data.co.uk was unreachable when they were made. The
model sits behind the closing line, as it should — a public model that beat the
close would be suspicious. On WTA the model's raw accuracy (65.5%) is within a
point of the market's (66.2%), consistent with a thinner market.

Python 3, standard library only. No API keys. `python3 selftest.py` runs the
offline checks; `python3 backtest.py --tour atp --year 2025` runs the
walk-forward evaluation. See `CLAUDE.md` for the data sources and the decisions
that took measurement to reach.

Projections are estimates, not advice.
