"""Turn a slate of upcoming matches into projections and priced props.

This is the join point: ESPN says who is playing, the archive says how well
they serve and return, Open-Meteo says what the air is doing, and model.py
turns all of that into distributions. Every page is a rendering of what comes
out of here.
"""

import math
import re
import unicodedata
from collections import defaultdict
from datetime import date, timedelta

import conditions as C
import fetch
import model
import ratings as R
import venues

# Measured on 2021-2026 outdoor matches, opponent-adjusted, n=39,085.
# Denser air suppresses both, so the slopes are negative. These are small
# effects -- about one ace per match across the full observed density range --
# and they are here because they were measured, not because they are decisive.
ACE_RHO_SLOPE = -0.0418      # per kg/m3, t = -8.4
SPW_RHO_SLOPE = -0.0665      # per kg/m3, t = -7.4

# Ace and double-fault counts are wider than a binomial. Measured on the 2025
# season against the fitted rates: the standard deviation of the z-score is the
# factor by which a binomial understates the spread. Serving is streaky and the
# men's game is streakier, which is why ATP aces need the widest allowance.
DISPERSION = {
    "atp": {"ace": 1.43, "df": 1.19},     # n=4,908 player-matches
    "wta": {"ace": 1.31, "df": 1.16},     # n=4,475
}

# Day-to-day variation in serve percentage, integrated over rather than
# assumed away. Measured by sweeping it against held-out seasons: at 0.05 the
# total-games bias falls from +1.8 games to +0.6 and games MAE improves in both
# test years, while match log loss moves by less than 0.004 in either
# direction. Raising it to 0.065 all but eliminates the games bias but starts
# costing match accuracy, so this sits deliberately short of that.
FORM_SIGMA = 0.05
FORM_NODES = 3

SURFACE_BY_TOURNEY = {
    "roland garros": "Clay", "wimbledon": "Grass", "us open": "Hard",
    "australian open": "Hard",
}


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z ]", " ", s.lower()).split()


class Resolver:
    """Map a display name to an archive player id.

    ESPN and the archive mostly agree on spelling, so a normalised full-name
    match carries the load; the surname-plus-initial fallback catches the
    reorderings and the dropped middle names. Ambiguous fallbacks are refused
    rather than guessed -- a wrong id silently projects the wrong player.
    """

    def __init__(self, obs):
        self.full = {}
        self.short = defaultdict(set)
        self.seen = defaultdict(float)
        for o in obs:
            t = _norm(o["name"])
            if not t:
                continue
            self.full.setdefault(" ".join(t), o["pid"])
            if len(t) >= 2:
                self.short[("".join(t[1:]), t[0][:1])].add(o["pid"])
            self.seen[o["pid"]] += o["svpt"]

    def find(self, name):
        t = _norm(name)
        if not t:
            return None
        hit = self.full.get(" ".join(t))
        if hit:
            return hit
        if len(t) >= 2:
            for key in (("".join(t[1:]), t[0][:1]), ("".join(t[:-1]), t[-1][:1])):
                cands = self.short.get(key)
                if cands and len(cands) == 1:
                    return next(iter(cands))
                if cands:
                    # prefer the player with the most tour-level serve points
                    return max(cands, key=lambda p: self.seen[p])
        return None


def surface_for(tourney, fallback="Hard"):
    n = (tourney or "").lower()
    for k, v in SURFACE_BY_TOURNEY.items():
        if k in n:
            return v
    return fallback


def conditions_for(tourney, when=None):
    """Air density at the venue, or None when the stop is unknown or roofed."""
    v = venues.find(tourney)
    if not v or v["indoor"]:
        return None
    try:
        wx = fetch.weather(v["lat"], v["lon"])
    except Exception:
        return None
    hours = wx.get("hours") or []
    if not hours:
        return None
    if when is not None:
        target = when.strftime("%Y-%m-%dT%H:00")
        pick = next((h for h in hours if h["time"] >= target), hours[0])
    else:
        pick = hours[len(hours) // 2]
    rho = fetch.air_density(pick["temp_c"], pick["rh"], pick["pressure_hpa"])
    return {
        "rho": rho, "temp_c": pick["temp_c"], "rh": pick["rh"],
        "wind_kmh": pick["wind_kmh"], "precip_pct": pick["precip_pct"],
        "elevation_m": wx.get("elevation_m"), "venue": v["key"],
        "time": pick["time"],
    }


def _rho_shift(cond, slope):
    if not cond:
        return 0.0
    return slope * (cond["rho"] - C.RHO_REF)


def project(rt, resolver, m, surface=None, cond=None):
    """One match -> every market the point model supports.

    Returns None when either player cannot be resolved or has too little
    tour-level history to rate. Guessing at an unrated player is how a model
    ends up confidently pricing a qualifier it has never seen.
    """
    aid = resolver.find(m["p1"]["name"])
    bid = resolver.find(m["p2"]["name"])
    if not aid or not bid:
        return None
    if rt.seen(aid) < 300 or rt.seen(bid) < 300:
        return None

    surface = surface or surface_for(m.get("tourney"))
    pa, pb = rt.matchup(aid, bid, surface)

    spw_adj = _rho_shift(cond, SPW_RHO_SLOPE)
    pa, pb = pa + spw_adj, pb + spw_adj

    bo = m.get("best_of") or 3
    d = model.match_dist_form(pa, pb, best_of=bo,
                              sigma=FORM_SIGMA, nodes=FORM_NODES)
    vol = model.serve_volume(pa, pb, best_of=bo)

    ace_mult = 1.0
    if cond:
        ace_mult = 1.0 + _rho_shift(cond, ACE_RHO_SLOPE) / max(rt.lg_ace, 1e-6)
        ace_mult = max(0.75, min(1.30, ace_mult))

    disp = DISPERSION.get(rt.tour, DISPERSION["atp"])
    props = {}
    for tag, pid, opp, svpt in (("a", aid, bid, vol["sv_points_a"]),
                                ("b", bid, aid, vol["sv_points_b"])):
        ar = rt.ace_rate(pid, opp, cond_mult=ace_mult)
        dr = rt.df_rate(pid)
        props[tag] = {
            "sv_points": svpt,
            "ace_rate": ar,
            "aces": model.count_dist(ar, svpt, disp["ace"]),
            "exp_aces": ar * svpt,
            "df_rate": dr,
            "dfs": model.count_dist(dr, svpt, disp["df"]),
            "exp_dfs": dr * svpt,
        }

    return {
        "match": m, "surface": surface, "cond": cond,
        "aid": aid, "bid": bid, "pa": pa, "pb": pb, "best_of": bo,
        "p_a": d["p_win"], "p_b": 1 - d["p_win"],
        "exp_games": d["exp_games"], "totals": d["totals"],
        "sets": d["sets"], "p_straight_a": d["p_straight"],
        "props": props, "dist": d,
    }


def total_line(d, line):
    return {"over": model.total_over(d["dist"], line),
            "under": 1 - model.total_over(d["dist"], line)}


def fair_odds(p):
    return float("inf") if p <= 0 else 1.0 / p


def edge(p_model, decimal_odds):
    """Expected value per unit staked at the offered price."""
    if not decimal_odds or decimal_odds <= 1:
        return None
    return p_model * decimal_odds - 1.0


def build(tour, day=None, seasons=5, asof=None):
    """Load ratings and project every unplayed singles match on the slate."""
    asof = asof or date.today()
    obs = R.load(tour, asof.year - seasons + 1, asof.year)
    rt = R.Ratings(tour, obs, asof)
    res = Resolver(obs)

    slate = [m for m in fetch.espn_draw(day, tour=tour)
             if m["tour"] == tour and m["state"] == "pre"]

    cond_cache = {}
    out = []
    for m in slate:
        key = m["tourney"]
        if key not in cond_cache:
            cond_cache[key] = conditions_for(key, m.get("start"))
        p = project(rt, res, m, cond=cond_cache[key])
        if p:
            out.append(p)
    return rt, res, out
