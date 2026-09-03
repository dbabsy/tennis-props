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

import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

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


def test_live():
    """The live number has to BE the pre-match number, restarted.

    That is the whole claim the live page makes, and it is the one thing that
    could break silently: a live figure that quietly disagreed with the match
    page about the same match would look plausible from either side.
    """
    print("live")
    for bo in (3, 5):
        live = model.live_dist(0.65, 0.62, 0, 0, 0, 0, True, best_of=bo)
        pre = model.match_dist(0.65, 0.62, best_of=bo)
        check(f"bo{bo} live at 0-0 is the pre-match win probability",
              close(live["p_win"], pre["p_win"], 1e-12))
        check(f"bo{bo} live at 0-0 is the pre-match games total",
              close(live["exp_remaining"], pre["exp_games"], 1e-9))

    check("set_from at 0-0 is set_dist",
          all(close(v, model.set_dist(0.65, 0.62).get(k, 0.0), 1e-12)
              for k, v in model.set_from(0.65, 0.62, 0, 0, True).items()))

    done = model.live_dist(0.65, 0.62, 2, 0, 0, 0, True, best_of=3)
    check("a finished match is not still a probability", done["p_win"] == 1.0
          and done["exp_remaining"] == 0.0)

    up = model.live_dist(0.65, 0.62, 1, 0, 3, 1, True, best_of=3)["p_win"]
    level = model.live_dist(0.65, 0.62, 0, 0, 0, 0, True, best_of=3)["p_win"]
    down = model.live_dist(0.65, 0.62, 0, 1, 1, 3, True, best_of=3)["p_win"]
    check("a set and a break up beats level beats a set and a break down",
          down < level < up, f"{down:.3f} < {level:.3f} < {up:.3f}")

    serve = model.live_dist(0.65, 0.62, 1, 1, 5, 4, True, best_of=3)["p_win"]
    recv = model.live_dist(0.65, 0.62, 1, 1, 5, 4, False, best_of=3)["p_win"]
    check("serving for the match beats receiving at the same score",
          serve > recv + 0.15, f"{serve:.3f} vs {recv:.3f}")

    # The index arithmetic the browser does, done here against the table it
    # will be given. If these two ever disagree the page reads the wrong row
    # for the right score and nothing looks wrong.
    for bo in (3, 5):
        tbl = model.live_table(0.65, 0.62, best_of=bo)
        games, sets = model.game_states(), model.set_states(bo)
        check(f"bo{bo} table covers every state exactly once",
              len(tbl) == len(sets) * len(games) * 2)
        enc = model.encode_table(tbl)
        check(f"bo{bo} table encodes to two characters a state",
              len(enc) == 2 * len(tbl))
        worst = 0.0
        for si, (sa, sb) in enumerate(sets):
            for gi, (ga, gb) in enumerate(games):
                for k, serving in enumerate((True, False)):
                    i = (si * len(games) + gi) * 2 + k
                    want = model.live_dist(0.65, 0.62, sa, sb, ga, gb, serving,
                                           best_of=bo)["p_win"]
                    got = int(enc[2 * i:2 * i + 2], 36) / 1295
                    worst = max(worst, abs(got - want))
        check(f"bo{bo} every encoded entry matches its own state",
              worst < 0.001, f"worst {worst:.5f}")


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


def _obs_row(pid, name, svpt):
    return {"pid": pid, "oid": "z", "name": name, "date": date(2025, 6, 1),
            "surface": "Hard", "tour": "wta", "svpt": svpt, "spw": 0.62,
            "ace": 0.04, "df": 0.05, "best_of": 3, "level": "A"}


def test_resolver():
    """Names, which is where a live page quietly goes wrong.

    A missing match is visible. A match resolved to the wrong player is not:
    it is still priced, still shown, and every number on the row belongs to
    somebody else.
    """
    print("resolver")
    import project as P

    obs = [_obs_row("A", "Xin Yu Wang", 13707),
           _obs_row("B", "Xiyu Wang", 8812),
           _obs_row("C", "Anna Kalinskaya", 12385)]
    r = P.Resolver(obs)
    # The archive writes her with a space, ESPN without one. Ignoring spaces
    # is still an exact match on the letters, so it is tried before anything
    # that guesses -- and before this it returned the other Wang outright.
    check("a name that differs only in spacing finds the right player",
          r.find("Xinyu Wang") == "A", r.find("Xinyu Wang") or "None")
    check("the other Wang is still herself", r.find("Xiyu Wang") == "B")
    check("surname-first ordering resolves too", r.find("Wang Xin Yu") == "A")
    check("an ordinary name is unaffected",
          r.find("Anna Kalinskaya") == "C")

    # Two established players sharing a surname and an initial: nothing to
    # choose between them, so choose neither.
    tie = P.Resolver([_obs_row("D", "Alex Silva", 5000),
                      _obs_row("E", "Ana Silva", 4000)])
    check("an ambiguous surname is refused, not guessed",
          tie.find("Alejandro Silva") is None, tie.find("Alejandro Silva"))

    # The usual collision is a tour regular against a qualifier, and that one
    # is safe to call.
    lop = P.Resolver([_obs_row("F", "Alex Silva", 20000),
                      _obs_row("G", "Ana Silva", 300)])
    check("a dominant candidate still wins an ambiguous surname",
          lop.find("Alejandro Silva") == "F")

    # And every skip has to be able to say why.
    class _Rt:
        def seen(self, pid):
            return {"A": 5000, "B": 5000, "C": 5000}.get(pid, 0)

    m = {"p1": {"name": "Xinyu Wang"}, "p2": {"name": "Anna Kalinskaya"}}
    check("a priceable match reports no reason",
          P.why_not(_Rt(), r, m) is None)
    m2 = {"p1": {"name": "Nobody At All"}, "p2": {"name": "Anna Kalinskaya"}}
    check("an unmatched name says so",
          (P.why_not(_Rt(), r, m2) or "").startswith("unresolved"),
          P.why_not(_Rt(), r, m2))


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
        }], [])
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

    live = [P.live_view(r) for r in rows]
    check("a live view carries a table for every state",
          all(len(v["table"]) == 2 * len(model.set_states(v["best_of"]))
              * len(model.game_states()) * 2 for v in live))
    check("the live table agrees with the pre-match number it ships beside",
          all(abs(int(v["table"][0:2], 36) / 1295 - v["p_pre"]) < 0.002
              for v in live))

    theme, event = build.slate_theme(rows)
    check("live.html renders with matches",
          build.page_live(live, "usopen", "US Open").strip().endswith("</html>"))
    check("live.html renders with nothing on court",
          "fills in when a match is under way" in build.page_live([]))
    for name, fn in (("index.html", build.page_conditions),
                     ("matches.html", build.page_matches),
                     ("props.html", build.page_props),
                     ("edges.html", build.page_edges)):
        html = fn(rows, theme, event)
        check(f"{name} renders", html.strip().endswith("</html>") and len(html) > 800)


HARNESS = """
globalThis.window = globalThis;
window.__LIVE__ = PAYLOAD;
const PAINTED = {};
function cell() {
  return {textContent: "", innerHTML: "", className: "", style: {}};
}
const CHROME = {};
globalThis.document = {
  getElementById(id) {
    if (id.indexOf("live-") === 0) return CHROME[id] || (CHROME[id] = cell());
    const row = PAINTED[id] || (PAINTED[id] = {});
    return {querySelector(sel) { return row[sel] || (row[sel] = cell()); }};
  }
};
globalThis.setInterval = function () {};
globalThis.fetch = function (url) {
  const doc = url.indexOf("/atp/") >= 0 ? SCOREBOARD : {events: []};
  return Promise.resolve({json: () => Promise.resolve(doc)});
};
LIVE_JS
setTimeout(function () {
  const out = {rows: {}, pill: "", clock: ""};
  for (const id of Object.keys(PAINTED)) {
    out.rows[id] = {
      p: PAINTED[id][".js-p"] ? PAINTED[id][".js-p"].textContent : null,
      sb: PAINTED[id][".js-sb"] ? PAINTED[id][".js-sb"].innerHTML : null
    };
  }
  out.pill = CHROME["live-pill"] ? CHROME["live-pill"].innerHTML : "";
  out.clock = CHROME["live-clock"] ? CHROME["live-clock"].textContent : "";
  console.log(JSON.stringify(out));
}, 20);
"""


def _espn_stub(mid, names, linescores, serving_id=None, tiebreaks=None):
    tiebreaks = tiebreaks or [[None] * len(ls) for ls in linescores]
    comps = [{"id": f"c{i}", "athlete": {"displayName": n, "id": f"a{i}"},
              "linescores": [{"value": v, "tiebreak": t}
                             for v, t in zip(ls, tb)]}
             for i, (n, ls, tb) in enumerate(zip(names, linescores, tiebreaks))]
    comp = {"id": mid, "competitors": comps}
    if serving_id is not None:
        comp["situation"] = {"possession": f"c{serving_id}"}
    return comp


def test_live_js():
    """Run the JavaScript the page actually ships, against the table it
    actually ships, and check it reads the same answer model.py would.

    The index arithmetic in the browser and the enumeration in model.py are
    two halves of one agreement. If they drift, the page shows a real
    probability for the wrong scoreline and nothing anywhere looks broken --
    which is the sort of bug that survives for a season.
    """
    print("live javascript")
    node = shutil.which("node")
    if not node:
        check("node is available to exercise the live page", True,
              "skipped: no node on this machine")
        return

    import build
    import project as P

    obs = _synthetic_obs()
    rt = R.Ratings("atp", obs, date(2026, 1, 1), k_serve=50, k_return=50)
    res = P.Resolver(obs)

    def match(mid, bo, tourney):
        return {"id": mid, "event_id": "e", "tourney": tourney, "sex": "m",
                "tour": "atp", "round": "R2", "best_of": bo, "state": "in",
                "completed": False, "detail": "", "court": "", "site": "",
                "start": datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
                "serving": None,
                "p1": {"name": "A", "won": False, "sets": [], "tb": []},
                "p2": {"name": "B", "won": False, "sets": [], "tb": []}}

    rows_ = [P.project(rt, res, match("m1", 5, "Wimbledon")),
             P.project(rt, res, match("m2", 3, "Cincinnati")),
             P.project(rt, res, match("m3", 3, "Cincinnati")),
             P.project(rt, res, match("m4", 3, "Cincinnati"))]
    live = [P.live_view(r) for r in rows_]

    board = {"events": [{"groupings": [{"competitions": [
        # A up a set and a break, A serving.
        _espn_stub("m1", ["A", "B"], [[6, 3], [4, 1]], serving_id=0),
        # ESPN lists the two the other way round -- the page has to read the
        # orientation off the names, not the position.
        _espn_stub("m2", ["B", "A"], [[1, 3], [6, 4]], serving_id=1),
        # Nobody says who is serving.
        _espn_stub("m3", ["A", "B"], [[5], [4]]),
        # Over, and the first set went to a tiebreak.
        _espn_stub("m4", ["A", "B"], [[7, 6], [6, 3]],
                   tiebreaks=[[5, None], [7, None]]),
    ]}]}]}

    src = (HARNESS.replace("PAYLOAD", json.dumps({
        "built": "2026-06-01T12:00:00+00:00",
        "games": [f"{a}-{b}" for a, b in model.game_states()],
        "sets": {str(bo): [f"{a}-{b}" for a, b in model.set_states(bo)]
                 for bo in (3, 5)},
        "matches": live}))
        .replace("SCOREBOARD", json.dumps(board))
        .replace("LIVE_JS", build.LIVE_JS))

    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "harness.js"
        f.write_text(src, encoding="utf-8")
        proc = subprocess.run([node, str(f)], capture_output=True, text=True,
                              timeout=60)
    if proc.returncode != 0:
        check("the live page's javascript runs", False,
              proc.stderr.strip().splitlines()[-1] if proc.stderr else "")
        return
    got = json.loads(proc.stdout)
    rows = got["rows"]

    def want(row, sa, sb, ga, gb, serving):
        # The same call live_view makes, form integration included -- the
        # page is compared against the model, not against a simpler model.
        return model.live_prob(round(row["pa"], 4), round(row["pb"], 4),
                               sa, sb, ga, gb, serving,
                               best_of=row["best_of"],
                               final_set_tb_target=(10 if row["best_of"] == 5
                                                    else 7),
                               sigma=P.FORM_SIGMA, nodes=P.FORM_NODES)

    def pct(x):
        return f"{100 * x:.0f}%"

    check("a set and a break up, server known",
          rows["m-m1"]["p"] == pct(want(rows_[0], 1, 0, 3, 1, True)),
          f'js {rows["m-m1"]["p"]} vs model {pct(want(rows_[0], 1, 0, 3, 1, True))}')
    check("orientation is read off the names, not ESPN's ordering",
          rows["m-m2"]["p"] == pct(want(rows_[1], 1, 0, 3, 1, True)),
          f'js {rows["m-m2"]["p"]} vs model {pct(want(rows_[1], 1, 0, 3, 1, True))}')
    mix = (want(rows_[2], 0, 0, 5, 4, True)
           + want(rows_[2], 0, 0, 5, 4, False)) / 2
    check("an unknown server averages the two rather than guessing",
          rows["m-m3"]["p"] == pct(mix),
          f'js {rows["m-m3"]["p"]} vs model {pct(mix)}')
    check("a finished match reads 100%, not a probability",
          rows["m-m4"]["p"] == "100%", rows["m-m4"]["p"])

    # -- the scorebug -----------------------------------------------------
    def boxes(html):
        return re.findall(r'<span class="(sb-g[^"]*)">(\d*)', html or "")

    def servers(html):
        return re.findall(r'<span class="sb-sv">([^<]*)</span>', html or "")

    check("the scorebug draws a box per set per player, in order",
          [v for _, v in boxes(rows["m-m1"]["sb"])] == ["6", "3", "4", "1"],
          str(boxes(rows["m-m1"]["sb"])))
    check("a completed set marks its winner and leaves the loser plain",
          [c for c, _ in boxes(rows["m-m1"]["sb"])][0] == "sb-g w"
          and [c for c, _ in boxes(rows["m-m1"]["sb"])][2] == "sb-g")
    check("the set being played is marked as current, not as won",
          all("cur" in [c for c, _ in boxes(rows["m-m1"]["sb"])][i]
              for i in (1, 3)))
    check("the server is marked on their own row and nobody else's",
          servers(rows["m-m1"]["sb"]) == ["●", ""],
          str(servers(rows["m-m1"]["sb"])))
    check("an unknown server marks neither row",
          servers(rows["m-m3"]["sb"]) == ["", ""])
    check("a tiebreak margin is raised beside the set it belongs to",
          '<span class="sb-tb">5</span>' in rows["m-m4"]["sb"]
          and '<span class="sb-tb">7</span>' in rows["m-m4"]["sb"])
    check("the player ahead is marked ahead",
          rows["m-m1"]["sb"].count('class="sb-r up"') == 1)
    check("names from the scoreboard are escaped, not injected",
          "<img" not in build.LIVE_JS.replace("esc(", "SAFE(")
          and 'replace(/[&<>"\']/g' in build.LIVE_JS)

    # -- how fresh the numbers are ----------------------------------------
    check("a successful pull reports the page as live",
          "live" in got["pill"] and "stale" not in got["pill"], got["pill"])
    check("a successful pull timestamps itself",
          "updated" in got["clock"], got["clock"])


def test_parlay():
    """A parlay is where independence gets assumed by accident.

    Legs on the same match are not independent, and the direction is not
    subtle: a straight-sets win and a long match are close to mutually
    exclusive. Multiplying the two marginals -- which is what a parlay
    calculator does -- overstates that ticket several times over.
    """
    print("parlay")
    import parlay
    import project as P

    obs = _synthetic_obs()
    rt = R.Ratings("atp", obs, date(2026, 1, 1), k_serve=50, k_return=50)
    res = P.Resolver(obs)
    m = {"id": "1", "event_id": "e", "tourney": "Cincinnati", "sex": "m",
         "tour": "atp", "round": "R16", "best_of": 3, "state": "pre",
         "completed": False, "detail": "", "court": "", "site": "",
         "start": datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
         "serving": None,
         "p1": {"name": "A", "won": False, "sets": [], "tb": []},
         "p2": {"name": "B", "won": False, "sets": [], "tb": []}}
    pr = P.project(rt, res, m)
    proj = parlay.from_slate([pr], P.DISPERSION["atp"])
    k = "A v B"

    j = pr["dist"]["joint"]
    check("the joint is a distribution", close(sum(j.values()), 1.0, 1e-9))
    need = pr["best_of"] // 2 + 1
    check("the joint's winner marginal is the win probability",
          close(sum(p for (a, _, _), p in j.items() if a == need),
                pr["p_a"], 1e-9))
    check("the joint's length marginal is the totals distribution",
          all(close(v, sum(p for (_, _, g), p in j.items() if g == gm), 1e-9)
              for gm, v in pr["dist"]["totals"].items()))

    def two(m1, m2):
        return parlay.price([m1, m2], proj)

    long_straight = two({"match": k, "market": "sets", "side": "a", "odds": 150},
                        {"match": k, "market": "total", "side": "over",
                         "line": 22.5, "odds": -110})
    check("a straight-sets win and a long match are worth far less together "
          "than multiplied",
          long_straight["p"] < 0.4 * long_straight["p_naive"],
          f'joint {long_straight["p"]:.4f} vs product '
          f'{long_straight["p_naive"]:.4f}')

    short_straight = two({"match": k, "market": "sets", "side": "a", "odds": 150},
                         {"match": k, "market": "total", "side": "under",
                          "line": 22.5, "odds": -110})
    check("and the same pair the other way round is worth more, not less",
          short_straight["p"] > short_straight["p_naive"])

    check("the two totals sides still add to one",
          close(long_straight["rows"][1]["p"] + short_straight["rows"][1]["p"],
                1.0, 1e-9))

    # Different matches really are independent, so there the product is right.
    m2 = dict(m, id="2")
    m2["p1"] = dict(m["p1"]); m2["p2"] = dict(m["p2"])
    pr2 = P.project(rt, res, m2)
    proj2 = parlay.from_slate([pr, dict(pr2, match=dict(m2, p1={"name": "C"},
                                                        p2={"name": "D"}))],
                             P.DISPERSION["atp"])
    across = parlay.price(
        [{"match": "A v B", "market": "ml", "side": "a", "odds": -140},
         {"match": "C v D", "market": "ml", "side": "a", "odds": -140}], proj2)
    check("legs on different matches multiply",
          close(across["p"], across["p_naive"], 1e-12))

    # Vig, stated rather than assumed away.
    fair = parlay.price(
        [{"match": k, "market": "ml", "side": "a",
          "odds": parlay.to_american(1 / pr["p_a"])}], proj)
    check("a leg priced at exactly the model's number has no edge",
          abs(fair["ev"]) < 0.01, f'{100*fair["ev"]:+.2f}%')

    check("a games handicap is refused rather than assumed independent",
          _raises(parlay.price,
                  [{"match": k, "market": "spread", "side": "a", "line": -3.5,
                    "odds": -110}], proj))
    check("an unknown market is refused",
          _raises(parlay.price,
                  [{"match": k, "market": "first_set_winner", "side": "a",
                    "odds": -110}], proj))

    check("american and decimal odds round-trip",
          all(abs(parlay.to_decimal(parlay.to_american(d)) - d) < 0.02
              for d in (1.5, 2.0, 3.5, 5.0)))


def _raises(fn, *args):
    try:
        fn(*args)
    except Exception:
        return True
    return False


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

    probe = Path(__file__).resolve().parent / "livecheck.html"
    check("the live feed check ships with the site", probe.exists())
    if probe.exists():
        text = probe.read_text(encoding="utf-8")
        check("the live feed check is self-contained",
              "src=" not in text and text.count("<script") == 1)


if __name__ == "__main__":
    for fn in (test_distributions, test_serve_rotation, test_live,
               test_monotonicity, test_ratings, test_resolver, test_ledger,
               test_pipeline, test_parlay, test_live_js, test_render):
        fn()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} failed: " + ", ".join(FAILURES))
        sys.exit(1)
    print("all checks passed")
