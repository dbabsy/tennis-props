"""Offline checks on the parts that are easy to break quietly.

Nothing here touches the network, so it runs in CI before a build and on a
laptop with no cache. It is not a test of whether the model is any good --
backtest.py is that -- but of whether the machinery still means what the
comments say it means. Every check below corresponds to something that has
either gone wrong or would go wrong undetectably:

  * a distribution that stops summing to one
  * the serve rotation across a set boundary, which is invisible in win
    probability and visible only in the set scores
  * the ledger recording a match that has already started, which is the one
    failure that would make the accuracy page a lie
  * a retirement being scored as though it were a short match
  * the return rating changing sign, which produces a rating table that looks
    perfect and predictions that are noise

Run:  python3 selftest.py
"""

import sys
from datetime import date, datetime, timedelta, timezone

import ledger
import model
import ratings as R
import render as V

FAILURES = []


def check(name, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + name + (f"  {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def close(a, b, tol=1e-6):
    return abs(a - b) <= tol


# ---------------------------------------------------------------------------

def test_distributions():
    print("distributions")
    d = model.set_dist(0.65, 0.62)
    check("set_dist sums to 1", close(sum(d.values()), 1.0, 1e-9))
    check("set_dist scores are legal",
          all(max(a, b) in (6, 7) and (max(a, b) - min(a, b) >= 2 or max(a, b) == 7)
              for a, b in d))

    for bo in (3, 5):
        m = model.match_dist(0.65, 0.62, best_of=bo)
        check(f"match_dist bo{bo} totals sum to 1",
              close(sum(m["totals"].values()), 1.0, 1e-9))
        check(f"match_dist bo{bo} set scores sum to 1",
              close(sum(m["sets"].values()), 1.0, 1e-9))
        need = bo // 2 + 1
        check(f"match_dist bo{bo} win prob equals its set scores",
              close(m["p_win"], sum(p for (a, _), p in m["sets"].items() if a == need),
                    1e-9))

    c = model.count_dist(0.08, 90, 1.0)
    mean = sum(k * p for k, p in c.items())
    check("count_dist sums to 1", close(sum(c.values()), 1.0, 1e-9))
    check("binomial count_dist has the right mean", close(mean, 0.08 * 90, 0.02),
          f"{mean:.2f}")

    # The whole point of the beta-binomial is that it is wider. Check the width
    # it is asked for is the width it delivers.
    for disp in (1.2, 1.43):
        c = model.count_dist(0.08, 90, disp)
        mu = sum(k * p for k, p in c.items())
        var = sum((k - mu) ** 2 * p for k, p in c.items())
        want = 90 * 0.08 * 0.92 * disp ** 2
        check(f"count_dist at dispersion {disp} widens the variance",
              abs(var / want - 1) < 0.02, f"var {var:.1f} want {want:.1f}")


def test_serve_rotation():
    print("serve rotation")
    # Whoever serves first in a set can win it 6-3 by holding throughout; 6-4
    # needs a break. So 6-3 outruns 6-4 for the first server and the other way
    # round for the returner. Getting the parity wrong flips this and nothing
    # else in the model notices.
    d = model.set_dist(0.68, 0.68)
    check("6-3 beats 6-4 for the player serving first",
          d[(6, 3)] > d[(6, 4)], f"{d[(6,3)]:.4f} vs {d[(6,4)]:.4f}")
    check("6-4 beats 6-3 for the player receiving first",
          d[(4, 6)] > d[(3, 6)], f"{d[(4,6)]:.4f} vs {d[(3,6)]:.4f}")

    check("a longer final-set tiebreak favours the better player",
          model.match_dist(0.70, 0.62, best_of=5,
                           final_set_tb_target=10)["p_win"] >=
          model.match_dist(0.70, 0.62, best_of=5)["p_win"] - 1e-9)

    # Service games have to add up to games played, or every counting prop is
    # scaled wrong.
    for bo in (3, 5):
        v = model.serve_volume(0.65, 0.62, best_of=bo)
        m = model.match_dist(0.65, 0.62, best_of=bo)
        check(f"bo{bo} service games account for every game played",
              close(v["sv_games_a"] + v["sv_games_b"], m["exp_games"], 1e-6))

    # Form integration widens the match, so it must also shorten it -- the two
    # were computed off different models until it was made to.
    fixed = model.serve_volume(0.65, 0.62, best_of=3)
    formed = model.serve_volume_form(0.65, 0.62, best_of=3, sigma=0.05, nodes=3)
    check("form integration lowers expected service points",
          formed["sv_points_a"] < fixed["sv_points_a"],
          f"{formed['sv_points_a']:.1f} vs {fixed['sv_points_a']:.1f}")
    check("serve_volume_form with no sigma is serve_volume",
          close(model.serve_volume_form(0.65, 0.62, sigma=0.0)["sv_points_a"],
                fixed["sv_points_a"]))


def test_monotonicity():
    print("monotonicity")
    check("a better server wins more often",
          model.match_dist(0.66, 0.62)["p_win"] > model.match_dist(0.64, 0.62)["p_win"])
    check("a bigger mismatch plays fewer games",
          model.match_dist(0.72, 0.58)["exp_games"] < model.match_dist(0.64, 0.62)["exp_games"])
    check("best-of-five plays more games than best-of-three",
          model.match_dist(0.65, 0.62, best_of=5)["exp_games"] >
          model.match_dist(0.65, 0.62, best_of=3)["exp_games"])
    check("the better player wins best-of-five more often than best-of-three",
          model.match_dist(0.68, 0.60, best_of=5)["p_win"] >
          model.match_dist(0.68, 0.60, best_of=3)["p_win"])


def _synthetic_obs():
    """Two servers, two returners, played against each other for a season.

    A is the better server and Y the better returner, by construction. Anything
    that gets the sign of the return rating backwards will fail the checks
    below while still producing a plausible-looking rating table -- which is
    exactly how that bug survived once already.
    """
    out = []
    d = date(2025, 1, 6)
    for i in range(200):
        for pid, oid, spw, ace in (("A", "X", 0.70, 0.12), ("X", "A", 0.62, 0.05),
                                   ("A", "Y", 0.64, 0.10), ("Y", "A", 0.62, 0.05),
                                   ("B", "X", 0.64, 0.06), ("X", "B", 0.62, 0.05),
                                   ("B", "Y", 0.58, 0.05), ("Y", "B", 0.62, 0.05)):
            out.append({
                "pid": pid, "oid": oid, "name": pid, "date": d + timedelta(days=i),
                "surface": "Hard", "tour": "atp", "svpt": 70, "spw": spw,
                "ace": ace, "df": 0.035, "best_of": 3, "level": "A",
            })
    return out


def test_ratings():
    print("ratings")
    obs = _synthetic_obs()
    rt = R.Ratings("atp", obs, date(2026, 1, 1), k_serve=50, k_return=50,
                   k_surface=50, k_ace=50, k_df=50, gap_mult=1.0)
    check("the better server is rated the better server",
          rt.serve_rating("A", "Hard") > rt.serve_rating("B", "Hard"))
    check("the better returner is rated the better returner",
          rt.return_rating("Y", "Hard") > rt.return_rating("X", "Hard"))
    # The sign check that matters: facing the good returner must lower the
    # server's projected serve percentage, not raise it.
    pa_vs_x, _ = rt.matchup("A", "X", "Hard")
    pa_vs_y, _ = rt.matchup("A", "Y", "Hard")
    check("facing a better returner lowers the serve projection",
          pa_vs_y < pa_vs_x, f"{pa_vs_y:.4f} vs {pa_vs_x:.4f}")
    check("the big server is projected for more aces",
          rt.ace_rate("A", "X", surface="Hard") > rt.ace_rate("B", "X", surface="Hard"))

    # Surface has to reach the count rates at all -- it used not to.
    obs2 = [dict(o, surface="Clay", ace=o["ace"] * 0.6) for o in obs] + obs
    rt2 = R.Ratings("atp", obs2, date(2026, 1, 1), k_ace=50)
    check("ace rates differ by surface",
          rt2.ace_rate("A", "X", surface="Clay") < rt2.ace_rate("A", "X", surface="Hard"),
          f"{rt2.ace_rate('A','X',surface='Clay'):.4f} vs "
          f"{rt2.ace_rate('A','X',surface='Hard'):.4f}")

    # And the format shift has to be signed the right way when it is present.
    obs3 = obs + [dict(o, best_of=5, spw=o["spw"] - 0.01) for o in obs]
    rt3 = R.Ratings("atp", obs3, date(2026, 1, 1), gap_mult=1.0)
    check("best-of-five is projected to hold less",
          rt3.matchup("A", "X", "Hard", best_of=5)[0]
          < rt3.matchup("A", "X", "Hard", best_of=3)[0])


def test_ledger():
    print("ledger")
    # The rule the whole page rests on: a match that has started is never
    # recorded. This is the automated version of the manual check in CLAUDE.md.
    started = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    saved = ledger._load, ledger._save, ledger.P.build
    written = {}
    try:
        ledger._load = lambda: {"picks": {}}
        ledger._save = lambda db: written.update(db)
        # Only the ATP side answers, so the count below is the number of
        # matches recorded rather than the number of tours walked.
        ledger.P.build = lambda tour, day=None: (None, None, [] if tour != "atp" else [{
            "match": {"id": "1", "tourney": "T", "round": "R",
                      "p1": {"name": "A"}, "p2": {"name": "B"},
                      "start": started},
            "surface": "Hard", "best_of": 3, "p_a": 0.6, "exp_games": 22.0,
            "dist": model.match_dist(0.65, 0.62),
            "props": {"a": {"exp_aces": 5.0}, "b": {"exp_aces": 4.0}},
        }])
        n = ledger.record(now=started + timedelta(minutes=1))
        check("record refuses a match that has already started", n == 0)
        n = ledger.record(now=started - timedelta(hours=1))
        check("record accepts a match that has not", n == 1)
    finally:
        ledger._load, ledger._save, ledger.P.build = saved

    check("a completed best-of-three is a clean finish",
          ledger._clean([6, 6], [3, 4], 3))
    check("a retirement led on the scoreboard is not",
          not ledger._clean([6, 2], [3, 1], 3))
    check("a best-of-five stopped after two sets is not",
          not ledger._clean([6, 6], [3, 4], 5))
    check("the status wording is believed when the score looks complete",
          not ledger._clean([6, 6], [3, 4], 3, "Final/Ret."))
    check("scores parse back out of the ledger's own format",
          ledger._sets_from_score("6.0-3.0 2.0-1.0") == ([6, 2], [3, 1]))


def test_pipeline():
    """A whole slate, end to end, on synthetic ratings and a fake scoreboard.

    build.py cannot be exercised without the network, so nothing else here
    notices when a signature changes underneath it -- a page function calling
    project() with the wrong arguments would only ever fail in production, two
    hours after the change was pushed.
    """
    print("pipeline")
    import build
    import project as P

    obs = _synthetic_obs()
    rt = R.Ratings("atp", obs, date(2026, 1, 1), k_serve=50, k_return=50)
    res = P.Resolver(obs)
    slate = [{
        "id": "1", "event_id": "e", "tourney": tourney, "sex": "m",
        "tour": "atp", "round": "R1", "best_of": bo, "state": "pre",
        "completed": False, "detail": "", "court": "", "site": "",
        "start": datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        "p1": {"name": "A", "won": False, "sets": [], "tb": []},
        "p2": {"name": "B", "won": False, "sets": [], "tb": []},
    } for tourney, bo in (("Wimbledon", 5), ("Cincinnati", 3))]

    rows = [P.project(rt, res, m) for m in slate]
    check("every synthetic match projects", all(r is not None for r in rows))
    rows = [r for r in rows if r]
    check("win probabilities are probabilities",
          all(0 < r["p_a"] < 1 for r in rows))
    check("the slam is recognised as one and the tour stop is not",
          P._slam("Wimbledon", 5) and P._slam("US Open", 3)
          and not P._slam("Cincinnati", 3))
    check("expected aces are positive and finite",
          all(0 < r["props"][t]["exp_aces"] < 60 for r in rows for t in "ab"))
    check("the games handicap carries the form integration",
          all(model.spread_cover_form(r["pa"], r["pb"], r["best_of"], -3.5,
                                      sigma=P.FORM_SIGMA, nodes=P.FORM_NODES)
              != model.spread_cover(r["pa"], r["pb"], r["best_of"], -3.5)
              for r in rows))
    check("serve points carry the form integration",
          all(r["props"]["a"]["sv_points"] <
              model.serve_volume(r["pa"], r["pb"],
                                 best_of=r["best_of"])["sv_points_a"]
              for r in rows))

    theme, event = build.slate_theme(rows)
    for name, fn in (("index.html", build.page_conditions),
                     ("matches.html", build.page_matches),
                     ("props.html", build.page_props),
                     ("edges.html", build.page_edges)):
        html = fn(rows, theme, event)
        check(f"{name} renders", html.strip().endswith("</html>") and len(html) > 800)


def test_render():
    print("render")
    html = V.page("T", "S", V.table(["a"], [["1"]], ["num"]), "matches.html",
                  theme="usopen", event="US Open")
    check("a page closes its html", html.strip().endswith("</html>"))
    check("a page carries the themed palette", "--accent" in html)
    check("the build stamp is formatted as a date, not a bare clock",
          'data-fmt="datetime"' in html)
    check("cell contents are escaped",
          "&lt;script&gt;" in V.table(["a"], [[V.esc("<script>")]]))


if __name__ == "__main__":
    for fn in (test_distributions, test_serve_rotation, test_monotonicity,
               test_ratings, test_ledger, test_pipeline, test_render):
        fn()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} failed: " + ", ".join(FAILURES))
        sys.exit(1)
    print("all checks passed")
