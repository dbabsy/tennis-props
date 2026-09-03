"""Price a parlay against the model, with the correlation and the vig in.

This does not pick legs. It answers one question about legs you have already
found on a book: given the model's view, what is this ticket actually worth?

Three things it insists on, because leaving any of them out flatters a ticket:

  correlation   Legs from the same match are not independent. "Wins in
                straight sets" and "over 22.5 games" are close to mutually
                exclusive, and multiplying their marginals -- which is what a
                parlay calculator does -- can overstate a ticket by a lot.
                model.match_dist returns the joint of winner by total games,
                so within one match the answer is exact rather than assumed.

  the vig       A four-leg ticket of fairly priced legs returns about 83
                cents on the dollar; six legs about 76. That is the number a
                model has to beat before anything else is worth discussing.

  the gap       backtest.py measures this model against the closing line and
                it is BEHIND, by 0.028-0.033 of log loss. A leg has to beat
                its offered price by more than that gap before the edge is
                real, and on props the gap has never been measured at all.

So the honest output of this file is usually "no". That is the point of it.
"""

import argparse
import itertools
import json
import math
from collections import defaultdict

import model


def to_decimal(american):
    a = float(american)
    return 1 + a / 100 if a > 0 else 1 + 100 / (-a)


def to_american(dec):
    if dec <= 1:
        return "—"
    return f"+{round((dec - 1) * 100)}" if dec >= 2 else f"{round(-100 / (dec - 1))}"


def devig_two_way(odds_a, odds_b):
    """The book's own fair probability, with its margin divided out. Comparing
    the model to the raw price credits it with beating the vig, which it did
    not do."""
    ia, ib = 1 / to_decimal(odds_a), 1 / to_decimal(odds_b)
    return ia / (ia + ib), ib / (ia + ib)


# ---------------------------------------------------------------------------
# One match: every leg on it, priced together
# ---------------------------------------------------------------------------

def _outcome_holds(leg, sa, sb, games, need):
    """Does this leg hold, for a match that ended (sa, sb) after `games`?"""
    m = leg["market"]
    a_won = sa == need
    if m == "ml":
        return a_won if leg["side"] == "a" else not a_won
    if m == "total":
        return games > leg["line"] if leg["side"] == "over" else games < leg["line"]
    if m == "sets":
        # "wins in straight sets": their side takes it without dropping one.
        if leg["side"] == "a":
            return a_won and sb == 0
        return (not a_won) and sa == 0
    return True          # counts are handled conditionally, not here


MARKETS = {"ml", "total", "sets", "ace", "df"}


def validate(legs):
    """Refuse a leg the joint cannot price, rather than pricing it wrongly.

    A games handicap is the notable absence: the margin of games is not
    recoverable from (sets won, total games), and model.spread_cover_form
    computes it in a separate pass that does not carry the rest of the ticket
    with it. Priced on its own it is fine; inside a parlay it would have to be
    assumed independent of the moneyline, which it emphatically is not.
    """
    for l in legs:
        m = l.get("market")
        if m == "spread":
            raise ValueError(
                "a games-handicap leg cannot be combined here: its margin is "
                "not in the joint. Price it alone with model.spread_cover_form.")
        if m not in MARKETS:
            raise ValueError(f"unknown market {m!r}; expected one of "
                             + ", ".join(sorted(MARKETS)))
        if m in ("total", "ace", "df") and "line" not in l:
            raise ValueError(f"{m} leg needs a line")
        if m in ("ml", "sets", "ace", "df") and l.get("side") not in ("a", "b"):
            raise ValueError(f"{m} leg needs side 'a' or 'b'")
        if m == "total" and l.get("side") not in ("over", "under"):
            raise ValueError("total leg needs side 'over' or 'under'")


def _count_prob(leg, pr, games):
    """P(an ace or double-fault leg holds) given the match went `games` long.

    The length is the whole reason to condition: a player in a straight-sets
    win does not serve enough to clear a big number, and a parlay that pairs
    "wins in straight sets" with "over 8.5 aces" is quietly betting against
    itself. Each player serves about half the games played.
    """
    tag = leg["side"]
    p = pr["pa"] if tag == "a" else pr["pb"]
    rate = pr["props"][tag]["ace_rate" if leg["market"] == "ace" else "df_rate"]
    svpt = (games / 2.0) * model.game_points(round(p, 3))
    disp = pr["dispersion"][leg["market"]]
    d = model.count_dist(rate, svpt, disp)
    over = model.over(d, leg["line"])
    return over if leg.get("side_over", True) else 1 - over


def match_prob(pr, legs):
    """P(every leg on this match holds), from the one joint distribution."""
    need = pr["best_of"] // 2 + 1
    counts = [l for l in legs if l["market"] in ("ace", "df")]
    outcomes = [l for l in legs if l["market"] not in ("ace", "df")]
    total = 0.0
    for (sa, sb, games), p in pr["dist"]["joint"].items():
        if not all(_outcome_holds(l, sa, sb, games, need) for l in outcomes):
            continue
        q = p
        # Given the length, the two serve counts are treated as independent of
        # each other. They are not exactly -- a streaky day lifts both -- but
        # the length is the correlation that actually moves a ticket.
        for l in counts:
            q *= _count_prob(l, pr, games)
        total += q
    return total


def ticket_prob(legs, projections):
    """P(the whole ticket lands). Same match -> joint; different matches ->
    independent, which they are."""
    validate(legs)
    by_match = defaultdict(list)
    for l in legs:
        by_match[l["match"]].append(l)
    p = 1.0
    for key, group in by_match.items():
        pr = projections[key]
        p *= match_prob(pr, group)
    return p


def from_slate(rows, dispersion):
    """Index today's projections by a name both you and the book would use."""
    out = {}
    for r in rows:
        m = r["match"]
        key = f'{m["p1"]["name"]} v {m["p2"]["name"]}'
        out[key] = {
            "pa": r["pa"], "pb": r["pb"], "best_of": r["best_of"],
            "dist": r["dist"], "props": r["props"],
            "dispersion": dispersion,
        }
    return out


def resolve(legs, projections):
    """Let a leg name a match loosely -- a surname is enough when it is not
    ambiguous, and refused when it is."""
    keys = list(projections)
    for l in legs:
        if l["match"] in projections:
            continue
        hits = [k for k in keys if l["match"].lower() in k.lower()]
        if len(hits) == 1:
            l["match"] = hits[0]
        else:
            raise KeyError(f'{l["match"]!r} matches {len(hits)} of today\'s '
                           f'matches; name it more precisely')
    return legs


# ---------------------------------------------------------------------------

def price(legs, projections, gap=0.0):
    """Everything worth knowing about one ticket."""
    validate(legs)
    offered = 1.0
    rows = []
    for l in legs:
        pr = projections[l["match"]]
        marg = match_prob(pr, [l])
        d = to_decimal(l["odds"])
        offered *= d
        rows.append({
            "leg": l, "p": marg, "fair": to_american(1 / marg) if marg else "—",
            "odds": l["odds"], "edge": marg * d - 1,
        })
    p = ticket_prob(legs, projections)
    naive = 1.0
    for r in rows:
        naive *= r["p"]
    return {
        "rows": rows, "p": p, "p_naive": naive,
        "offered": offered, "fair": 1 / p if p else float("inf"),
        "ev": p * offered - 1,
        "ev_after_gap": p * (1 - gap) * offered - 1,
    }


def search(pool, projections, lo, hi, min_legs=4, max_legs=6, top=5, gap=0.0):
    """Best-EV combinations whose offered price lands in [lo, hi].

    Note what this is not: it does not make a bad ticket good. If every
    candidate has negative expectation, the best of them is the least bad one,
    and the honest move is not to bet it.
    """
    lo_d, hi_d = to_decimal(lo), to_decimal(hi)
    out = []
    for n in range(min_legs, max_legs + 1):
        for combo in itertools.combinations(pool, n):
            # One leg per market per match; two sides of the same thing is not
            # a parlay, it is a contradiction.
            keys = {(l["match"], l["market"], l.get("side")) for l in combo}
            if len(keys) != len(combo):
                continue
            d = 1.0
            for l in combo:
                d *= to_decimal(l["odds"])
            if not lo_d <= d <= hi_d:
                continue
            res = price(list(combo), projections, gap=gap)
            out.append(res)
    out.sort(key=lambda r: -r["ev"])
    return out[:top]


def report(res, gap=0.0):
    lines = []
    for r in res["rows"]:
        l = r["leg"]
        what = f'{l["match"]} · {l["market"]}'
        if "line" in l:
            what += f' {l["line"]}'
        if l.get("side"):
            what += f' {l["side"]}'
        lines.append(f'  {what:<46} model {100*r["p"]:5.1f}%  '
                     f'fair {r["fair"]:>6}  offered {l["odds"]:>6}  '
                     f'edge {100*r["edge"]:+6.1f}%')
    lines.append("")
    lines.append(f'  ticket   model {100*res["p"]:.2f}%   '
                 f'fair {to_american(res["fair"])}   '
                 f'offered {to_american(res["offered"])}')
    if abs(res["p"] - res["p_naive"]) > 1e-9:
        d = res["p"] / res["p_naive"] - 1
        lines.append(f'  correlation moves the ticket {100*d:+.1f}% against '
                     f'what multiplying the legs would give '
                     f'({100*res["p_naive"]:.2f}%)')
    lines.append(f'  expected value {100*res["ev"]:+.1f}% of stake')
    if gap:
        lines.append(f'  after the measured gap to the close '
                     f'({gap:.3f}): {100*res["ev_after_gap"]:+.1f}%')
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--legs", help="JSON file of legs with your book's prices")
    ap.add_argument("--pool", help="JSON file of candidate legs to search")
    ap.add_argument("--target", nargs=2, type=int, metavar=("LO", "HI"),
                    default=(250, 400))
    ap.add_argument("--date")
    ap.add_argument("--gap", type=float, default=0.0,
                    help="haircut for the model's measured deficit to the "
                         "closing line; 0 assumes the model is as sharp as "
                         "the book, which backtest.py says it is not")
    a = ap.parse_args()
    print("parlay.py needs a slate to price against; run it where ESPN is "
          "reachable.\nSee --help and the worked example in selftest.py.")
