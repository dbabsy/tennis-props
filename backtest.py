"""Walk-forward evaluation against the closing market.

Beating a coin flip is meaningless in tennis -- the favourite wins about 70% of
matches and the rank alone gets you most of the way. The only benchmark worth
measuring against is the closing price, which is why this joins the model to
tennis-data.co.uk's Pinnacle/Bet365 lines and reports both scores side by side.

Ratings are refit weekly on data strictly before that week, never on the match
being predicted.
"""

import argparse
import math
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


def lookup_market(idx, kw, kl, d, slack=4):
    """Odds dates and tourney dates disagree by a few days; allow slack."""
    hit = idx.get((kw, kl))
    if hit and abs((hit[1] - d).days) <= 14:
        return hit[0]
    hit = idx.get((kl, kw))
    if hit and abs((hit[1] - d).days) <= 14:
        return 1.0 - hit[0]
    return None


# ---------------------------------------------------------------------------

def logloss(p, y):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return -(math.log(p) if y else math.log(1 - p))


def run(tour, test_year, train_years=4, halflife=R.HALFLIFE_DAYS, quiet=False,
        sigma=0.0, nodes=3, **kw):
    start = test_year - train_years
    obs = R.load(tour, start, test_year)
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
        if fitted.seen(wid) < 400 or fitted.seen(lid) < 400:
            continue

        # Orient A/B by a deterministic coin rather than always putting the
        # winner first. The log loss is the same either way, but calibration
        # is meaningless if every label is a 1.
        flip = zlib.crc32(f'{m["tourney_id"]}:{m["match_num"]}'.encode()) & 1 == 1
        aid, bid = (lid, wid) if flip else (wid, lid)
        a_name = m["loser_name"] if flip else m["winner_name"]
        b_name = m["winner_name"] if flip else m["loser_name"]
        y = 0 if flip else 1

        pa, pb = fitted.matchup(aid, bid, m["surface"])
        bo = m.get("best_of") or 3
        d = model.match_dist_form(pa, pb, best_of=bo, sigma=sigma, nodes=nodes)

        actual_games = _score_games(m.get("score") or "")
        mp = lookup_market(mkt, key_archive(a_name), key_archive(b_name),
                           m["date"])
        rows.append({
            "p": d["p_win"], "y": y,
            "mkt": mp,
            "exp_games": d["exp_games"], "games": actual_games,
            "surface": m["surface"], "bo": bo,
            "dist": d,
        })

    return summarize(rows, tour, test_year, quiet)


def _score_games(score):
    """Total games from a score string like '7-6(3) 6-3'."""
    tot = 0
    for s in score.split():
        mm = re.match(r"^(\d+)-(\d+)", s)
        if mm:
            tot += int(mm.group(1)) + int(mm.group(2))
    return tot or None


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
        if g:
            print(f"games   MAE {out['games_mae']:.2f}  RMSE {out['games_rmse']:.2f}"
                  f"  bias {out['games_bias']:+.2f}  (n={len(g)})")
        _calibration(rows)

    return out


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
