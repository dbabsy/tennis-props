"""Walk-forward evaluation against the closing market.

Beating a coin flip is meaningless in tennis -- the favourite wins about 70% of
matches and the rank alone gets you most of the way. The only benchmark worth
measuring against is the closing price, which is why this joins the model to
tennis-data.co.uk's Pinnacle/Bet365 lines and reports both scores side by side.

Ratings are refit weekly on data strictly before that week, never on the match
being predicted.
"""

import argparse
import hashlib
import math
import pickle
import re
import unicodedata
import zlib
from collections import defaultdict
from datetime import date, timedelta

import fetch
import model
import ratings as R


# ---------------------------------------------------------------------------
# Name matching between the archive ("Jannik Sinner") and the odds file
# ("Sinner J."). Surnames can be multi-word, so the initial is the anchor.
# ---------------------------------------------------------------------------

def _strip(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z ]", " ", s.lower()).strip()


def key_odds(name):
    """'Carballes Baena R.' -> ('carballesbaena', 'r')"""
    t = _strip(name).split()
    if len(t) < 2:
        return (t[0] if t else "", "")
    return ("".join(t[:-1]), t[-1][:1])


def key_archive(name):
    """'Roberto Carballes Baena' -> ('carballesbaena', 'r')"""
    t = _strip(name).split()
    if len(t) < 2:
        return (t[0] if t else "", "")
    return ("".join(t[1:]), t[0][:1])


def devig(odds_w, odds_l):
    """Closing odds -> fair probability for the winner, proportional method."""
    if not odds_w or not odds_l or odds_w <= 1 or odds_l <= 1:
        return None
    iw, il = 1.0 / odds_w, 1.0 / odds_l
    return iw / (iw + il)


def market_index(tour, years):
    """(date, playerkey, playerkey) -> fair prob that the first key won."""
    idx = {}
    for y in years:
        for r in fetch.odds_rows(tour, y):
            if not r.get("date"):
                continue
            p = devig(r.get("PSW") or r.get("B365W") or r.get("AvgW"),
                      r.get("PSL") or r.get("B365L") or r.get("AvgL"))
            if p is None:
                continue
            idx[(key_odds(r["Winner"]), key_odds(r["Loser"]))] = (p, r["date"])
    return idx


def lookup_market(idx, kw, kl, d, slack=14):
    """The odds file dates a match to the day it was played, the archive
    dates it to the start of the tournament, so a fortnight of slack is
    needed to join a second-week slam match at all."""
    hit = idx.get((kw, kl))
    if hit and abs((hit[1] - d).days) <= slack:
        return hit[0]
    hit = idx.get((kl, kw))
    if hit and abs((hit[1] - d).days) <= slack:
        return 1.0 - hit[0]
    return None


# ---------------------------------------------------------------------------

def logloss(p, y):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return -(math.log(p) if y else math.log(1 - p))


def collect(tour, test_year, train_years=4, halflife=R.HALFLIFE_DAYS,
            sigma=0.0, nodes=3, min_seen=400, final_tb=True, cache=True, **kw):
    """Walk-forward predictions for one tour-season: one row per match.

    Split out from run() because the expensive part -- refitting the ratings
    every week of the season -- does not depend on anything applied after the
    point model. A sweep over a probability recalibration or a different total
    line can reuse one pass instead of paying for it once per candidate value.

    Rows carry the serve-count side too (expected rate, expected service
    points, actual aces and double faults), so the props layer is measurable
    on the same footing as the match odds rather than not at all.
    """
    key = None
    if cache:
        key = _cache_key(tour, test_year, train_years, halflife, sigma, nodes,
                         min_seen, final_tb, sorted(kw.items()))
        hit = _cache_read(key)
        if hit is not None:
            return hit

    obs = R.load(tour, test_year - train_years, test_year)
    matches = [m for m in fetch.archive_seasons(tour, test_year, test_year)
               if m["date"] and m["date"].year == test_year]
    matches = [m for m in matches
               if m.get("w_svpt") and m.get("l_svpt")
               and "RET" not in (m.get("score") or "")
               and "W/O" not in (m.get("score") or "")]
    matches.sort(key=lambda m: m["date"])

    mkt = market_index(tour, [test_year])

    rows = []
    fitted, cur = None, None
    for m in matches:
        wk = m["date"] - timedelta(days=m["date"].weekday())
        if wk != cur:
            cur = wk
            try:
                fitted = R.Ratings(tour, obs, wk, halflife=halflife, **kw)
            except ValueError:
                continue
        if fitted is None:
            continue

        wid, lid = m["winner_id"], m["loser_id"]
        # Require both players to have been seen; a debutant has no rating.
        if fitted.seen(wid) < min_seen or fitted.seen(lid) < min_seen:
            continue

        # Orient A/B by a deterministic coin rather than always putting the
        # winner first. The log loss is the same either way, but calibration
        # is meaningless if every label is a 1.
        flip = zlib.crc32(f'{m["tourney_id"]}:{m["match_num"]}'.encode()) & 1 == 1
        aid, bid = (lid, wid) if flip else (wid, lid)
        a_name = m["loser_name"] if flip else m["winner_name"]
        b_name = m["winner_name"] if flip else m["loser_name"]
        y = 0 if flip else 1

        bo = m.get("best_of") or 3
        pa, pb = fitted.matchup(aid, bid, m["surface"], best_of=bo)
        # Round before the DP so its caches hit across matches; the model is
        # nowhere near precise enough for the fourth decimal to mean anything.
        pa, pb = round(pa, 4), round(pb, 4)
        tb = _final_set_kw(m.get("tourney_name"), bo) if final_tb else {}
        d = model.match_dist_form(pa, pb, best_of=bo, sigma=sigma, nodes=nodes,
                                  **tb)
        vol = model.serve_volume_form(pa, pb, best_of=bo, sigma=sigma,
                                      nodes=nodes)

        props = []
        for tag, pid, oid in (("a", aid, bid), ("b", bid, aid)):
            # vol is keyed by the A/B orientation, the archive columns by
            # winner/loser -- so map back through the same flip.
            side = "w" if (pid == wid) else "l"
            svpt = m.get(f"{side}_svpt")
            if not svpt:
                continue
            props.append({
                "svpt": svpt,
                "exp_svpt": vol["sv_points_a" if tag == "a" else "sv_points_b"],
                "aces": m.get(f"{side}_ace"),
                "dfs": m.get(f"{side}_df"),
                "ace_rate": fitted.ace_rate(pid, oid, surface=m["surface"]),
                "df_rate": fitted.df_rate(pid, surface=m["surface"]),
            })

        rows.append({
            "p": d["p_win"], "y": y,
            "mkt": lookup_market(mkt, key_archive(a_name), key_archive(b_name),
                                 m["date"]),
            "exp_games": d["exp_games"], "games": _score_games(m.get("score") or ""),
            "totals": d["totals"],
            "surface": m["surface"], "bo": bo,
            "props": props,
        })

    if key:
        _cache_write(key, rows)
    return rows


def _final_set_kw(tourney, best_of):
    """All four slams decide a 6-6 final set with a 10-point tiebreak; every
    other event uses the ordinary 7-point one."""
    n = (tourney or "").lower()
    if best_of == 5 or any(s in n for s in SLAMS):
        return {"final_set_tb_target": 10}
    return {}


SLAMS = ("australian open", "roland garros", "wimbledon", "us open")


def _cache_key(*parts):
    """Include the source of the two files that decide what a row means, so a
    change to the model or the ratings invalidates the cache instead of
    silently comparing today's code against yesterday's numbers."""
    h = hashlib.sha256()
    for f in (R.__file__, model.__file__):
        h.update(open(f, "rb").read())
    h.update(repr(parts).encode())
    return h.hexdigest()[:32]


def _cache_read(key):
    p = fetch.CACHE / f"bt_{key}.pkl"
    if not p.exists():
        return None
    try:
        return pickle.loads(p.read_bytes())
    except Exception:
        return None


def _cache_write(key, rows):
    (fetch.CACHE / f"bt_{key}.pkl").write_bytes(pickle.dumps(rows))


def run(tour, test_year, train_years=4, halflife=R.HALFLIFE_DAYS, quiet=False,
        sigma=0.0, nodes=3, **kw):
    return summarize(collect(tour, test_year, train_years, halflife,
                             sigma, nodes, **kw), tour, test_year, quiet)


def _score_games(score):
    """Total games from a score string like '7-6(3) 6-3'."""
    tot = 0
    for s in score.split():
        mm = re.match(r"^(\d+)-(\d+)", s)
        if mm:
            tot += int(mm.group(1)) + int(mm.group(2))
    return tot or None


# Standard published lines, so the totals distribution is scored on the shape
# the site actually prices rather than only on its mean.
TOTAL_LINES = {3: (20.5, 21.5, 22.5, 23.5), 5: (36.5, 38.5, 40.5)}


def summarize(rows, tour, year, quiet=False):
    n = len(rows)
    if not n:
        print("no rows")
        return {}

    mll = sum(logloss(r["p"], r["y"]) for r in rows) / n
    brier = sum((r["p"] - r["y"]) ** 2 for r in rows) / n
    acc = sum(1 for r in rows if (r["p"] > 0.5) == bool(r["y"])) / n

    paired = [r for r in rows if r["mkt"] is not None]
    out = {"n": n, "logloss": mll, "brier": brier, "acc": acc,
           "n_market": len(paired)}

    if paired:
        m_ll = sum(logloss(r["mkt"], r["y"]) for r in paired) / len(paired)
        o_ll = sum(logloss(r["p"], r["y"]) for r in paired) / len(paired)
        m_acc = sum(1 for r in paired if (r["mkt"] > 0.5) == bool(r["y"])) / len(paired)
        o_acc = sum(1 for r in paired if (r["p"] > 0.5) == bool(r["y"])) / len(paired)
        out.update(market_logloss=m_ll, model_logloss_paired=o_ll,
                   market_acc=m_acc, model_acc_paired=o_acc)

    g = [r for r in rows if r["games"]]
    if g:
        err = [r["exp_games"] - r["games"] for r in g]
        out["games_mae"] = sum(abs(e) for e in err) / len(g)
        out["games_bias"] = sum(err) / len(g)
        out["games_rmse"] = math.sqrt(sum(e * e for e in err) / len(g))

    out.update(_totals_scores(rows))
    out.update(_prop_scores(rows))

    if not quiet:
        print(f"\n=== {tour.upper()} {year} — {n} matches "
              f"({out['n_market']} matched to closing odds) ===")
        print(f"model   log loss {mll:.4f}   brier {brier:.4f}   acc {acc:.3f}")
        if paired:
            print(f"market  log loss {m_ll:.4f}                    acc {m_acc:.3f}")
            print(f"model   log loss {o_ll:.4f}  (same {len(paired)} matches) "
                  f"acc {o_acc:.3f}")
            gap = o_ll - m_ll
            print(f"        gap {gap:+.4f} — "
                  + ("model beats the close" if gap < 0 else "market is sharper"))
        else:
            # The market benchmark is the only one worth having; losing it
            # silently would leave the model looking fine against itself.
            print("market  NO CLOSING ODDS JOINED — tennis-data.co.uk is "
                  "unreachable or the season file is empty, so the only "
                  "benchmark that matters is missing from this run.")
        if g:
            print(f"games   MAE {out['games_mae']:.2f}  RMSE {out['games_rmse']:.2f}"
                  f"  bias {out['games_bias']:+.2f}  (n={len(g)})")
            _by_format(rows)
        if out.get("totals_logloss") is not None:
            print(f"totals  log loss {out['totals_logloss']:.4f}  "
                  f"hit {out['totals_acc']:.3f}  "
                  f"over-rate model {out['totals_pred']:.3f} vs actual "
                  f"{out['totals_actual']:.3f}  (n={out['n_totals']})")
        if out.get("ace_mae") is not None:
            print(f"aces    MAE {out['ace_mae']:.2f}  bias {out['ace_bias']:+.2f}"
                  f"  dispersion {out['ace_disp']:.2f}  (n={out['n_props']})")
            print(f"        rate-only MAE {out['ace_mae_truevol']:.2f} "
                  f"(charged actual service points, so volume error is removed)")
            print(f"dfs     MAE {out['df_mae']:.2f}  bias {out['df_bias']:+.2f}"
                  f"  dispersion {out['df_disp']:.2f}")
            print(f"volume  service-points MAE {out['svpt_mae']:.1f}  "
                  f"bias {out['svpt_bias']:+.1f}")
            _ace_by_surface(rows)
        _calibration(rows)

    return out


def _totals_scores(rows):
    """Score the totals distribution at the lines the site publishes."""
    ll, hit, n, pred, act = 0.0, 0, 0, 0.0, 0.0
    for r in rows:
        if not r["games"] or not r.get("totals"):
            continue
        for line in TOTAL_LINES.get(r["bo"], ()):
            p = sum(v for k, v in r["totals"].items() if k > line)
            y = 1 if r["games"] > line else 0
            ll += logloss(p, y)
            hit += (p > 0.5) == bool(y)
            pred += p
            act += y
            n += 1
    if not n:
        return {"totals_logloss": None}
    return {"totals_logloss": ll / n, "totals_acc": hit / n, "n_totals": n,
            "totals_pred": pred / n, "totals_actual": act / n}


def _prop_scores(rows):
    """Ace and double-fault accuracy, split so the two ways of being wrong --
    the rate and the opportunity -- are visible separately. Charging the model
    the actual number of service points isolates the rate; the gap between the
    two columns is what the match model contributes."""
    ace_e, ace_t, df_e, sv_e = [], [], [], []
    ace_z, df_z = [], []
    for r in rows:
        for pp in r.get("props", ()):
            if pp["aces"] is None or pp["dfs"] is None:
                continue
            ace_e.append(pp["ace_rate"] * pp["exp_svpt"] - pp["aces"])
            ace_t.append(pp["ace_rate"] * pp["svpt"] - pp["aces"])
            df_e.append(pp["df_rate"] * pp["exp_svpt"] - pp["dfs"])
            sv_e.append(pp["exp_svpt"] - pp["svpt"])
            for rate, cnt, bag in ((pp["ace_rate"], pp["aces"], ace_z),
                                   (pp["df_rate"], pp["dfs"], df_z)):
                mu = pp["svpt"] * rate
                sd = math.sqrt(pp["svpt"] * rate * (1 - rate))
                if sd > 0:
                    bag.append((cnt - mu) / sd)
    if not ace_e:
        return {"ace_mae": None}
    return {
        "n_props": len(ace_e),
        "ace_mae": _mean(abs(x) for x in ace_e), "ace_bias": _mean(ace_e),
        "ace_mae_truevol": _mean(abs(x) for x in ace_t),
        "ace_disp": _sd(ace_z),
        "df_mae": _mean(abs(x) for x in df_e), "df_bias": _mean(df_e),
        "df_disp": _sd(df_z),
        "svpt_mae": _mean(abs(x) for x in sv_e), "svpt_bias": _mean(sv_e),
    }


def _mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def _sd(xs):
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def _by_format(rows):
    """Best-of-three and best-of-five fail differently and the blended number
    hides it: a slam's games distribution is far wider than a tour event's."""
    buckets = defaultdict(list)
    for r in rows:
        if r["games"]:
            buckets[r["bo"]].append(r)
    if len(buckets) < 2:
        return
    for bo in sorted(buckets):
        v = buckets[bo]
        err = [r["exp_games"] - r["games"] for r in v]
        print(f"        best-of-{bo}: MAE {_mean(abs(e) for e in err):.2f}  "
              f"bias {_mean(err):+.2f}  n={len(v)}")


def _ace_by_surface(rows):
    """Ace rates are a different game on clay than on grass; a blended rate is
    wrong on both, and only a per-surface split shows it."""
    buckets = defaultdict(list)
    for r in rows:
        for pp in r.get("props", ()):
            if pp["aces"] is not None:
                buckets[r["surface"]].append(pp["ace_rate"] * pp["svpt"] - pp["aces"])
    print("  ace bias by surface (actual service points charged):")
    for s in sorted(buckets, key=lambda k: -len(buckets[k])):
        v = buckets[s]
        if len(v) < 50:
            continue
        print(f"    {s:<7} bias {_mean(v):+.2f}  MAE "
              f"{_mean(abs(x) for x in v):.2f}  n={len(v)}")


def _calibration(rows):
    buckets = defaultdict(list)
    for r in rows:
        buckets[min(int(r["p"] * 10), 9)].append(r)
    print("  calibration:")
    for b in sorted(buckets):
        v = buckets[b]
        if len(v) < 20:
            continue
        pred = sum(x["p"] for x in v) / len(v)
        act = sum(x["y"] for x in v) / len(v)
        print(f"    p {b/10:.1f}-{(b+1)/10:.1f}  predicted {pred:.3f}  "
              f"actual {act:.3f}  n={len(v)}  {act-pred:+.3f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tour", default="atp")
    ap.add_argument("--year", type=int, default=2025)
    ap.add_argument("--train", type=int, default=4)
    ap.add_argument("--halflife", type=float, default=R.HALFLIFE_DAYS)
    a = ap.parse_args()
    run(a.tour, a.year, a.train, a.halflife)
