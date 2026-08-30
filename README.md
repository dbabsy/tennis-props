# tennis-props

Point-model projections and prop pricing for ATP and WTA singles.

Each player's serve and return rates are opponent- and surface-adjusted, then
propagated point → game → tiebreak → set → match. One engine produces match
odds, total games, set scores, games handicaps and straight-sets from the same
distribution, and serve volume for the ace and double-fault props.

**Pages:** [conditions](index.html) · [matches](matches.html) ·
[props](props.html) · [fair prices](edges.html) · [accuracy](accuracy.html)

Measured by walk-forward backtest against Pinnacle/Bet365 closing lines,
ratings refit weekly on data strictly before each match:

| | matches | model | market | gap | games MAE | games bias |
|---|---|---|---|---|---|---|
| ATP 2024 | 1,930 | 0.626 | 0.593 | +0.033 | 5.56 | +0.75 |
| ATP 2025 | 1,781 | 0.633 | 0.600 | +0.033 | 5.58 | +0.64 |
| WTA 2024 | 1,702 | 0.631 | 0.599 | +0.032 | 5.09 | +0.84 |
| WTA 2025 | 1,697 | 0.639 | 0.612 | +0.028 | 4.99 | +0.82 |

Log loss; lower is better. The model sits behind the closing line, as it
should — a public model that beat the close would be suspicious. That ~0.03 gap
is the hurdle a price has to clear before it is an edge. On WTA the model's
raw accuracy (65.5%) is within a point of the market's (66.2%), consistent
with a thinner market.

Python 3, standard library only. No API keys. See `CLAUDE.md` for the data
sources and the decisions that took measurement to reach.

Projections are estimates, not advice.
