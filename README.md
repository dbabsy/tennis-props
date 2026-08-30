# tennis-props

Point-model projections and prop pricing for ATP and WTA singles.

Each player's serve and return rates are opponent- and surface-adjusted, then
propagated point → game → tiebreak → set → match. One engine produces match
odds, total games, set scores, games handicaps and straight-sets from the same
distribution, and serve volume for the ace and double-fault props.

**Pages:** [conditions](index.html) · [matches](matches.html) ·
[props](props.html) · [fair prices](edges.html) · [accuracy](accuracy.html)

Measured against the closing line on ATP 2025 (n=1,781 matches with odds):
model log loss 0.636 vs market 0.600. The model is behind the market, as it
should be — that 0.036 gap is the hurdle a price must clear before it is an
edge, and the accuracy page tracks whether live picks hold up.

Python 3, standard library only. No API keys. See `CLAUDE.md` for the data
sources and the decisions that took measurement to reach.

Projections are estimates, not advice.
