"""Keeps score of what the projections actually did.

The one rule that matters: a prediction is only ever recorded before the match
starts. Recording after the fact -- even accidentally, even for a match whose
result was not consulted -- turns the accuracy page into a lie, and the lie is
undetectable later because the stored row looks identical either way.

So: `record` refuses any match whose start time has passed, and refuses to
modify a row that already exists. `score` only fills in results. Both are
idempotent, because CI reruns them and races with pushes.
"""

import argparse
import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import fetch
import model
import project as P
import render as V

ROOT = Path(__file__).resolve().parent
LEDGER = ROOT / "ledger" / "picks.json"


def _load():
    if LEDGER.exists():
        return json.loads(LEDGER.read_text())
    return {"picks": {}}


def _save(db):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(db, indent=1, sort_keys=True))


def record(day=None, now=None):
    """Freeze today's projections. Silently skips matches already under way."""
    now = now or datetime.now(timezone.utc)
    db = _load()
    added = skipped_started = skipped_existing = 0

    for tour in ("atp", "wta"):
        try:
            _, _, rows = P.build(tour, day=day)
        except Exception as e:
            print(f"  {tour}: {type(e).__name__}: {e}")
            continue
        for r in rows:
            m = r["match"]
            key = f'{tour}:{m["id"]}'
            if key in db["picks"]:
                skipped_existing += 1
                continue
            if not m["start"] or m["start"] <= now:
                skipped_started += 1
                continue
            lines = (20.5, 21.5, 22.5, 23.5) if r["best_of"] == 3 else (36.5, 38.5, 40.5)
            mid = lines[len(lines) // 2]
            db["picks"][key] = {
                "tour": tour, "id": m["id"], "tourney": m["tourney"],
                "round": m["round"], "surface": r["surface"],
                "start": m["start"].isoformat(),
                "recorded": now.isoformat(),
                "best_of": r["best_of"],
                "p1": m["p1"]["name"], "p2": m["p2"]["name"],
                "p_p1": r["p_a"],
                "exp_games": r["exp_games"],
                "total_line": mid,
                "p_over": model.total_over(r["dist"], mid),
                "exp_aces_p1": r["props"]["a"]["exp_aces"],
                "exp_aces_p2": r["props"]["b"]["exp_aces"],
                "result": None,
            }
            added += 1

    _save(db)
    print(f"recorded {added}; {skipped_started} already started, "
          f"{skipped_existing} already on file")
    return added


def score(day=None):
    """Fill in results for frozen picks. Never touches the prediction fields."""
    db = _load()
    results = {}
    base = day or date.today()
    # Walk back three weeks: a slam runs a fortnight and ESPN keys the
    # scoreboard by tournament, so a pick can still be waiting on a result
    # from an event that started well before today.
    for tour in ("atp", "wta"):
        for back in range(0, 22, 7):
            try:
                got = fetch.espn_draw(base - timedelta(days=back),
                                      tour=tour, ttl=1800)
            except Exception:
                continue
            for m in got:
                if m["completed"]:
                    results[f'{m["tour"]}:{m["id"]}'] = m

    filled = 0
    for key, p in db["picks"].items():
        if p.get("result"):
            continue
        m = results.get(key)
        if not m:
            continue
        s1 = [x for x in m["p1"]["sets"] if x is not None]
        s2 = [x for x in m["p2"]["sets"] if x is not None]
        if not s1 or not s2:
            continue
        games = sum(s1) + sum(s2)
        # ESPN orders competitors independently of how the pick was stored.
        p1_is_first = m["p1"]["name"] == p["p1"]
        won = m["p1"]["won"] if p1_is_first else m["p2"]["won"]
        p["result"] = {
            "p1_won": bool(won), "games": games,
            "score": " ".join(f"{a}-{b}" for a, b in zip(s1, s2)),
        }
        filled += 1

    _save(db)
    print(f"scored {filled}")
    return filled


def _brier(rows):
    return sum((r["p"] - r["y"]) ** 2 for r in rows) / len(rows)


def _theme():
    """Match whatever build.py themed the rest of the site as."""
    f = ROOT / "data" / "theme.json"
    if f.exists():
        d = json.loads(f.read_text())
        return d.get("theme"), d.get("event")
    return None, None


def report():
    theme, event = _theme()
    db = _load()
    done = [p for p in db["picks"].values() if p.get("result")]
    if not done:
        return V.page("Accuracy", "Nothing has been scored yet",
                      '<p class="note">The ledger records each projection '
                      'before the match starts and fills in the result '
                      'afterwards. It is empty until the first frozen slate '
                      'completes.</p>', "accuracy.html",
                      theme=theme, event=event)

    wl = [{"p": p["p_p1"], "y": 1 if p["result"]["p1_won"] else 0} for p in done]
    n = len(wl)
    brier = _brier(wl)
    base = _brier([{"p": sum(x["y"] for x in wl) / n, "y": x["y"]} for x in wl])
    skill = 1 - brier / base if base else 0
    hits = sum(1 for x in wl if (x["p"] > .5) == bool(x["y"]))
    gerr = [p["exp_games"] - p["result"]["games"] for p in done]
    ov = [p for p in done if p.get("p_over") is not None]
    ov_hit = sum(1 for p in ov
                 if (p["p_over"] > .5) == (p["result"]["games"] > p["total_line"]))

    cards = f"""<div class="grid">
<div class="card"><div class="lab">Matches scored</div><div class="stat">{n}</div></div>
<div class="card"><div class="lab">Winner hit rate</div><div class="stat">{100*hits/n:.1f}%</div></div>
<div class="card"><div class="lab">Brier</div><div class="stat">{brier:.4f}</div>
<p class="note">skill vs base rate {skill:+.3f}</p></div>
<div class="card"><div class="lab">Games error</div>
<div class="stat">{sum(abs(g) for g in gerr)/len(gerr):.2f}</div>
<p class="note">bias {sum(gerr)/len(gerr):+.2f} games</p></div>
<div class="card"><div class="lab">Totals hit rate</div>
<div class="stat">{100*ov_hit/len(ov):.1f}%</div>
<p class="note">n={len(ov)}</p></div>
</div>"""

    buckets = defaultdict(list)
    for x in wl:
        buckets[min(int(x["p"] * 10), 9)].append(x)
    trs = []
    for b in sorted(buckets):
        v = buckets[b]
        if len(v) < 10:
            continue
        pred = sum(x["p"] for x in v) / len(v)
        act = sum(x["y"] for x in v) / len(v)
        trs.append([f"{b/10:.1f}–{(b+1)/10:.1f}", V.pct(pred), V.pct(act),
                    f'<span class="{"good" if abs(act-pred)<.06 else "warn"}">'
                    f'{act-pred:+.3f}</span>', str(len(v))])

    recent = sorted(done, key=lambda p: p["start"], reverse=True)[:40]
    rtrs = [[
        f'<span class="chip">{V.esc(p["tour"].upper())}</span> '
        f'<span class="name">{V.esc(p["p1"])}</span> v {V.esc(p["p2"])}',
        V.pct(p["p_p1"]),
        f'<span class="{"good" if (p["p_p1"]>.5)==p["result"]["p1_won"] else "bad"}">'
        f'{"✓" if (p["p_p1"]>.5)==p["result"]["p1_won"] else "✗"}</span>',
        V.esc(p["result"]["score"]),
        f'{p["exp_games"]:.1f} / {p["result"]["games"]}',
    ] for p in recent]

    body = [cards,
            "<h2>Calibration</h2>",
            V.table(["Predicted band", "Mean predicted", "Actual", "Gap", "n"],
                    trs, ["", "num", "num", "num", "num"]),
            '<p class="note">Calibration converges long before skill does. '
            'Roughly 300 scored matches before the gap column means anything; '
            'a few thousand before the Brier skill score does.</p>',
            "<h2>Recent</h2>",
            V.table(["Match", "Model", "", "Score", "Games exp/act"],
                    rtrs, ["name", "num", "", "", "num"])]
    return V.page("Accuracy", f"{n} matches scored since the ledger opened",
                  "\n".join(body), "accuracy.html", theme=theme, event=event)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args()
    if a.record or a.all:
        record()
    if a.score or a.all:
        score()
    if a.out:
        Path(a.out).write_text(report(), encoding="utf-8")
        print("wrote", a.out)
