#!/usr/bin/env python3
"""Find out whether ESPN gives point-by-point tennis, and where.

The live page already knows what to do with a point score -- model.py has the
hold probabilities and selftest proves the match is Markov at game boundaries,
so a point score costs nothing but the reading of it. What is not known is
whether ESPN publishes one, and under what key.

That has to be answered against a real in-progress match. Run this while
something is on court:

    python3 espn_probe.py                 # today, both tours
    python3 espn_probe.py --event 401234  # one specific match

It tries the scoreboard we already use, the per-event summary endpoint we do
not, and the core API's play feed, and it reports every field that looks like
a tennis point score or a serve indicator. Standard library only.

Send me the espn_sample.json it writes and I will wire the browser side
against the real shape. The serving field is the cautionary tale: it was
guessed at defensively, shipped, and is still unverified.
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request

SITE = "https://site.api.espn.com/apis/site/v2/sports/tennis"
CORE = "https://sports.core.api.espn.com/v2/sports/tennis/leagues"
# ESPN 403s anything claiming to be a browser -- see CLAUDE.md. Plain agent.
UA = "tennis-props/1.0 (+https://github.com/dbabsy/tennis-props)"

POINTS = {"0", "15", "30", "40", "AD", "A", "ad"}
SERVE_HINTS = ("possession", "serv", "active", "hasball")


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:
        print(f"    {type(e).__name__}: {e}")
        return None, None


def crawl(node, path=""):
    """Every leaf in the document, with the path that reaches it."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from crawl(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node[:6]):
            yield from crawl(v, f"{path}[{i}]")
    else:
        yield path, node


def interesting(doc, label):
    """Anything that could be a point score or a server flag."""
    pts, srv = [], []
    for path, val in crawl(doc):
        s = str(val)
        low = path.lower()
        if s in POINTS and not re.search(r"linescores\[\d+\]\.value$", path):
            pts.append((path, s))
        if any(h in low for h in SERVE_HINTS) and val not in (None, "", False):
            srv.append((path, s[:40]))
    print(f"  {label}:")
    if pts:
        print("    point-score candidates:")
        for p, v in pts[:12]:
            print(f"      {p} = {v}")
    else:
        print("    point-score candidates: none")
    if srv:
        print("    serve-indicator candidates:")
        for p, v in srv[:12]:
            print(f"      {p} = {v}")
    else:
        print("    serve-indicator candidates: none")
    return bool(pts), bool(srv)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", help="ESPN competition id to inspect directly")
    a = ap.parse_args()

    live = []
    if a.event:
        live = [(a.event, "atp", None)]
    else:
        print("1. finding a match that is actually on court")
        for tour in ("atp", "wta"):
            st, doc = get(f"{SITE}/{tour}/scoreboard")
            print(f"   {st} {tour} scoreboard")
            if st != 200 or not doc:
                continue
            for ev in doc.get("events", []):
                for g in ev.get("groupings", []):
                    for c in g.get("competitions", []):
                        state = ((c.get("status") or {}).get("type") or {}
                                 ).get("state")
                        if state == "in":
                            live.append((c.get("id"), tour, c))
        if not live:
            sys.exit("\nnothing is in progress right now. Run this again while "
                     "a match is on court -- an in-progress match is the only "
                     "thing that can answer the question.")
        print(f"   {len(live)} match(es) in progress; inspecting the first")

    cid, tour, comp = live[0]
    print(f"\n2. what the scoreboard already carries for {cid}")
    found_any = False
    if comp:
        p, s = interesting(comp, "scoreboard competition")
        found_any = found_any or p

    print(f"\n3. endpoints we do not currently use")
    dump = {}
    for label, url in (
            ("summary", f"{SITE}/{tour}/summary?event={cid}"),
            ("core play-by-play",
             f"{CORE}/{tour}/events/{cid}/competitions/{cid}/plays?limit=25")):
        st, doc = get(url)
        print(f"   {st} {label}")
        if st == 200 and doc:
            dump[label] = doc
            p, s = interesting(doc, label)
            found_any = found_any or p

    out = "espn_sample.json"
    with open(out, "w") as f:
        json.dump({"competition": comp, **dump}, f, indent=1, default=str)
    print(f"\n   wrote {out}")
    print("\n" + ("   VERDICT: a point score is in there -- send me the file "
                  "and I will wire it up."
                  if found_any else
                  "   VERDICT: no point score found in any of these. Point-level "
                  "would need a different feed, and the live page stays at "
                  "game resolution."))


if __name__ == "__main__":
    main()
