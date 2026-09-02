"""The point model: everything downstream is derived from two numbers per
player per surface -- serve points won and return points won.

Tennis is unusually well suited to this. A match is a nested sequence of
independent-ish points, so a single pair of rates propagates all the way up to
match odds, total games and set scores without a separate model per market.
That is why there is no match-result model here: fitting one would throw away
the structure that makes the game-total and handicap markets tractable.

Propagation is done by dynamic programming rather than the closed forms, for
two reasons: the DP returns the full distribution over game scores (needed for
totals and spreads, not just the win probability), and it makes the tiebreak
serve rotation and the set-to-set serve parity explicit instead of assumed.
"""

import math
from collections import defaultdict
from datetime import date
from functools import lru_cache

# ---------------------------------------------------------------------------
# Point -> game
# ---------------------------------------------------------------------------

@lru_cache(maxsize=None)
def game_prob(p):
    """Probability the server holds, given p = P(server wins a point)."""
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    q = 1.0 - p
    # Reach deuce from 40-40 onward: p^2 / (p^2 + q^2)
    deuce = (p * p) / (p * p + q * q)
    # Win outright at 4-0, 4-1, 4-2; reach 3-3 then deuce.
    return (p ** 4
            + 4 * p ** 4 * q
            + 10 * p ** 4 * q ** 2
            + 20 * p ** 3 * q ** 3 * deuce)


@lru_cache(maxsize=None)
def game_points(p):
    """Expected points played in a service game. Needed to turn per-point ace
    and double-fault rates into per-match counts."""
    if not 0.0 < p < 1.0:
        return 4.0
    q = 1.0 - p
    # Points to reach 3-3 (deuce) is 6; expected extra points from deuce is
    # 2 / (p^2 + q^2) since each two-point exchange either resolves or resets.
    p_deuce = 20 * (p ** 3) * (q ** 3)
    base = (4 * (p ** 4 + q ** 4)
            + 5 * (4 * p ** 4 * q + 4 * q ** 4 * p)
            + 6 * (10 * p ** 4 * q ** 2 + 10 * q ** 4 * p ** 2))
    return base + p_deuce * (6 + 2.0 / (p * p + q * q))


# ---------------------------------------------------------------------------
# Point -> tiebreak
# ---------------------------------------------------------------------------

def _tb_server(points_played):
    """Who serves point n of a tiebreak. A serves point 0, then the serve
    changes every two points: A BB AA BB AA ..."""
    return 0 if ((points_played + 1) // 2) % 2 == 0 else 1


@lru_cache(maxsize=None)
def tiebreak_prob(pa, pb, target=7):
    """P(player A wins the tiebreak), A serving first.
    pa/pb are each player's P(win point on own serve)."""
    memo = {}

    def rec(a, b):
        if a >= target and a - b >= 2:
            return 1.0
        if b >= target and b - a >= 2:
            return 0.0
        key = (a, b)
        if key in memo:
            return memo[key]
        # At 6-6 the state is symmetric and repeats every two points; solve it
        # rather than recursing forever.
        if a >= target - 1 and b >= target - 1:
            s = _tb_server(a + b)
            p1 = pa if s == 0 else 1 - pb
            s2 = _tb_server(a + b + 1)
            p2 = pa if s2 == 0 else 1 - pb
            win2 = p1 * p2                      # A takes both points
            lose2 = (1 - p1) * (1 - p2)         # A drops both
            hold = 1 - win2 - lose2             # back to deuce
            v = win2 / (1 - hold) if hold < 1 else 0.5
            memo[key] = v
            return v
        s = _tb_server(a + b)
        p = pa if s == 0 else 1 - pb
        v = p * rec(a + 1, b) + (1 - p) * rec(a, b + 1)
        memo[key] = v
        return v

    return rec(0, 0)


# ---------------------------------------------------------------------------
# Point -> set, as a full distribution over game scores
# ---------------------------------------------------------------------------

@lru_cache(maxsize=None)
def set_dist(pa, pb, tb_at=6, tb_target=7):
    """Distribution over (games_A, games_B) for one set, A serving first.

    Returns {(a, b): probability}. Keeping the whole distribution rather than
    just P(win set) is what makes total-games and games-handicap markets fall
    out of the same model.
    """
    ha, hb = game_prob(pa), game_prob(pb)
    out = defaultdict(float)
    # state: (games_a, games_b) -> prob;  server is decided by parity
    states = {(0, 0): 1.0}

    while states:
        nxt = defaultdict(float)
        for (a, b), pr in states.items():
            a_serving = (a + b) % 2 == 0
            hold = ha if a_serving else hb
            # server wins the game
            for winner, p in ((0, hold), (1, 1.0 - hold)):
                na, nb = (a + 1, b) if (winner == 0) == a_serving else (a, b + 1)
                w = pr * p
                if w <= 0.0:
                    continue
                if na == tb_at and nb == tb_at:
                    # A serves the first point of the tiebreak iff it is A's
                    # turn to serve the next game.
                    a_first = (na + nb) % 2 == 0
                    t = tiebreak_prob(pa, pb, tb_target) if a_first else \
                        1.0 - tiebreak_prob(pb, pa, tb_target)
                    out[(na + 1, nb)] += w * t
                    out[(na, nb + 1)] += w * (1.0 - t)
                elif (na >= tb_at and na - nb >= 2) or (nb >= tb_at and nb - na >= 2):
                    out[(na, nb)] += w
                elif na > tb_at or nb > tb_at:
                    out[(na, nb)] += w
                else:
                    nxt[(na, nb)] += w
        states = {k: v for k, v in nxt.items() if v > 1e-12}

    return dict(out)


# ---------------------------------------------------------------------------
# Set -> match
# ---------------------------------------------------------------------------

def match_dist(pa, pb, best_of=3, final_set_tb_target=7):
    """Full match distribution.

    Returns dict with win probability, the distribution over total games, the
    distribution over set scores, and P(A wins in straight sets).

    Serve parity is carried between sets: whoever receives the last game of a
    set serves the first game of the next, so the set distribution has to be
    computed both ways round.
    """
    need = best_of // 2 + 1
    dist_a = set_dist(pa, pb)             # A serves first
    dist_b = set_dist(pb, pa)             # B serves first (scores are B, A)

    # state: (sets_a, sets_b, total_games, a_serves_first_next) -> prob
    states = {(0, 0, 0, True): 1.0}
    win_a = 0.0
    totals = defaultdict(float)
    setline = defaultdict(float)
    straight = 0.0

    for set_no in range(best_of):
        nxt = defaultdict(float)
        for (sa, sb, tg, a_first), pr in states.items():
            decider = (sa == need - 1 and sb == need - 1)
            if decider and final_set_tb_target != 7:
                d = set_dist(pa if a_first else pb, pb if a_first else pa,
                             tb_at=6, tb_target=final_set_tb_target)
            else:
                d = dist_a if a_first else dist_b

            for (x, y), q in d.items():
                # x, y are (first server's games, other's games)
                ga, gb = (x, y) if a_first else (y, x)
                w = pr * q
                if w <= 1e-12:
                    continue
                nsa, nsb = (sa + 1, sb) if ga > gb else (sa, sb + 1)
                ntg = tg + ga + gb
                # Serve alternates across the set boundary too: an odd
                # number of games flips who opens the next set.
                nxt_first = ((ga + gb) % 2 == 1) != a_first

                if nsa == need or nsb == need:
                    if nsa == need:
                        win_a += w
                        if nsb == 0:
                            straight += w
                    totals[ntg] += w
                    setline[(nsa, nsb)] += w
                else:
                    nxt[(nsa, nsb, ntg, nxt_first)] += w
        states = nxt

    return {
        "p_win": win_a,
        "totals": dict(totals),
        "sets": dict(setline),
        "p_straight": straight,
        "exp_games": sum(g * p for g, p in totals.items()),
    }


def total_over(dist, line):
    """P(total games strictly exceeds a half-point line)."""
    return sum(p for g, p in dist["totals"].items() if g > line)


def spread_cover(pa, pb, best_of, handicap):
    """P(A's games minus B's games beats `handicap`). Rebuilt from the set
    distributions because match_dist only keeps the total, not the margin."""
    need = best_of // 2 + 1
    dist_a, dist_b = set_dist(pa, pb), set_dist(pb, pa)
    states = {(0, 0, 0, True): 1.0}
    cover = 0.0
    for _ in range(best_of):
        nxt = defaultdict(float)
        for (sa, sb, marg, a_first), pr in states.items():
            d = dist_a if a_first else dist_b
            for (x, y), q in d.items():
                ga, gb = (x, y) if a_first else (y, x)
                w = pr * q
                if w <= 1e-12:
                    continue
                nsa, nsb = (sa + 1, sb) if ga > gb else (sa, sb + 1)
                nm = marg + ga - gb
                nf = ((ga + gb) % 2 == 1) != a_first
                if nsa == need or nsb == need:
                    if nm + handicap > 0:
                        cover += w
                else:
                    nxt[(nsa, nsb, nm, nf)] += w
        states = nxt
    return cover


def spread_cover_form(pa, pb, best_of, handicap, sigma=0.0, nodes=3):
    """spread_cover under the same form variation as everything else.

    The games handicap is the one market on the site that was still priced off
    a fixed pair of serve percentages while the totals beside it came from the
    integrated distribution -- and the handicap is precisely the market that
    lives on how lopsided a match can get, which is what the integration is
    for. Two prices on one row should not come from two models.
    """
    if sigma <= 0:
        return spread_cover(pa, pb, best_of, handicap)
    return sum(w * spread_cover(qa, qb, best_of, handicap)
               for qa, qb, w in _form_pairs(pa, pb, sigma, nodes))


def serve_volume(pa, pb, best_of=3):
    """Expected service games and service points for each player.

    Counting props are rates multiplied by opportunity, and opportunity is the
    part the match model already knows: a player who is about to be bagelled
    will not serve enough to reach an ace line no matter how big the serve is.
    Getting this wrong is the most common way an ace projection goes wrong.

    Within a set of N games the player who served first serves ceil(N/2), so
    the serve parity carried through match_dist matters here too.
    """
    need = best_of // 2 + 1
    dist_a, dist_b = set_dist(pa, pb), set_dist(pb, pa)
    states = {(0, 0, True): 1.0}
    sg_a = sg_b = 0.0

    for _ in range(best_of):
        nxt = defaultdict(float)
        for (sa, sb, a_first), pr in states.items():
            d = dist_a if a_first else dist_b
            for (x, y), q in d.items():
                ga, gb = (x, y) if a_first else (y, x)
                w = pr * q
                if w <= 1e-12:
                    continue
                n = ga + gb
                first_serves = (n + 1) // 2
                if a_first:
                    sg_a += w * first_serves
                    sg_b += w * (n - first_serves)
                else:
                    sg_b += w * first_serves
                    sg_a += w * (n - first_serves)
                nsa, nsb = (sa + 1, sb) if ga > gb else (sa, sb + 1)
                if nsa < need and nsb < need:
                    nxt[(nsa, nsb, ((n % 2 == 1) != a_first))] += w
        states = nxt

    return {
        "sv_games_a": sg_a, "sv_games_b": sg_b,
        "sv_points_a": sg_a * game_points(pa),
        "sv_points_b": sg_b * game_points(pb),
    }


def count_dist(rate, trials, dispersion=1.0, cap=60):
    """Distribution over a per-serve count (aces, double faults).

    Binomial when dispersion is 1. Real ace counts are overdispersed -- a
    server has hot and cold days, and conditions move the whole match -- so the
    variance is inflated by matching a beta-binomial's first two moments. Using
    a plain binomial here quietly understates every over.
    """
    n = max(1, int(round(trials)))
    p = min(max(rate, 1e-6), 1 - 1e-6)
    if dispersion <= 1.0 + 1e-9:
        alpha = beta = None
    else:
        # Var = n p (1-p) [1 + (n-1)/(rho+1)] ; solve rho for the target factor
        extra = (dispersion ** 2 - 1.0)
        if extra <= 0 or n <= 1:
            alpha = beta = None
        else:
            rho = max((n - 1) / extra - 1.0, 0.05)
            alpha, beta = p * rho, (1 - p) * rho

    out = {}
    if alpha is None:
        lp = [0.0] * (min(n, cap) + 1)
        # log-space binomial to stay stable at large n
        for k in range(len(lp)):
            lp[k] = (math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
                     + k * math.log(p) + (n - k) * math.log(1 - p))
        for k, v in enumerate(lp):
            out[k] = math.exp(v)
    else:
        lb = math.lgamma(alpha + beta) - math.lgamma(alpha) - math.lgamma(beta)
        for k in range(min(n, cap) + 1):
            lv = (math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
                  + math.lgamma(k + alpha) + math.lgamma(n - k + beta)
                  - math.lgamma(n + alpha + beta) + lb)
            out[k] = math.exp(lv)
    tot = sum(out.values()) or 1.0
    return {k: v / tot for k, v in out.items()}


def over(dist, line):
    return sum(p for k, p in dist.items() if k > line)


# Gauss-Hermite nodes for a standard normal, in sigma units.
_GH = {
    1: [(0.0, 1.0)],
    3: [(-1.7320508, 1 / 6), (0.0, 2 / 3), (1.7320508, 1 / 6)],
    5: [(-2.8569700, 0.011257411), (-1.3556262, 0.22207592),
        (0.0, 0.53333333), (1.3556262, 0.22207592), (2.8569700, 0.011257411)],
}


def _form_pairs(pa, pb, sigma, nodes):
    """The (pa, pb, weight) grid both form integrations run over. Rounded so
    the dynamic programming caches hit across matches."""
    grid = _GH[nodes]
    for za, wa in grid:
        for zb, wb in grid:
            yield (round(min(max(pa + za * sigma, 0.30), 0.85), 4),
                   round(min(max(pb + zb * sigma, 0.30), 0.85), 4),
                   wa * wb)


def serve_volume_form(pa, pb, best_of=3, sigma=0.0, nodes=3):
    """serve_volume, integrated over the same day-to-day form variation the
    match distribution is already integrated over.

    Leaving it out was an inconsistency with a measurable cost. Expected points
    in a service game peak when the two players are level, so assuming both
    bring exactly their average serve credits every match with more service
    points than it will have -- and every counting prop is that number times a
    rate. Measured over 2024-2025 the fixed-form version expected about six
    service points per player per match too many, four percent on every ace and
    double-fault line and all in the same direction.
    """
    if sigma <= 0:
        return serve_volume(pa, pb, best_of=best_of)
    out = defaultdict(float)
    for qa, qb, w in _form_pairs(pa, pb, sigma, nodes):
        v = serve_volume(qa, qb, best_of=best_of)
        for k, x in v.items():
            out[k] += w * x
    return dict(out)


def match_dist_form(pa, pb, best_of=3, sigma=0.0, nodes=3, **kw):
    """match_dist, integrated over day-to-day variation in serve percentage.

    A player does not bring the same serve to every match, and using a single
    point estimate quietly assumes they do. That assumption makes every match
    look more evenly contested than it will be, and evenly contested matches
    run long -- which is exactly the direction the total-games bias pointed.

    Both players' forms are drawn independently, so the spread of the *gap*
    widens by more than either player's own spread, producing the lopsided
    scorelines a fixed-p model never generates.
    """
    if sigma <= 0:
        return match_dist(pa, pb, best_of=best_of, **kw)

    acc_win = 0.0
    totals = defaultdict(float)
    sets = defaultdict(float)
    straight = 0.0
    for qa, qb, w in _form_pairs(pa, pb, sigma, nodes):
        d = match_dist(qa, qb, best_of=best_of, **kw)
        acc_win += w * d["p_win"]
        straight += w * d["p_straight"]
        for g, p in d["totals"].items():
            totals[g] += w * p
        for s, p in d["sets"].items():
            sets[s] += w * p
    return {
        "p_win": acc_win, "totals": dict(totals), "sets": dict(sets),
        "p_straight": straight,
        "exp_games": sum(g * p for g, p in totals.items()),
    }
