#!/usr/bin/env python3
"""Find out what the SportsGameOdds key actually buys, before building on it.

Three questions have to be answered before any integration is worth writing,
and none of them can be answered from a sandbox with no route to the API:

  1. Is tennis in the plan at all? The free tier advertises 8 leagues and the
     ones visible on the pricing page are US team sports. If ATP/WTA are not
     among them, everything downstream is moot.
  2. What shape is the response? Guessing a schema and writing a parser
     against the guess is how you get a build that fails at 3am.
  3. What counts as an "object"? The cap is 2,500 a month. A tennis slate can
     be 120 matches. If one match is one object, that is twenty slates a
     month -- which decides the whole design.

Run:
    export SGO_API_KEY=...            # never paste it into a file in the repo
    python3 sgo_probe.py              # metadata only, spends ~nothing
    python3 sgo_probe.py --events     # one events call, spends real objects

Standard library only, like the rest of the repo.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

BASES = ["https://api.sportsgameodds.com/v2",
         "https://api.sportsgameodds.com/v1",
         "https://api.sportsgameodds.com"]

KEY = os.environ.get("SGO_API_KEY", "").strip()
if not KEY:
    sys.exit("set SGO_API_KEY in the environment first (do not put it in a file)")

# The docs and the wild disagree about which header the key goes in, so try
# each and report which one the server actually accepted.
AUTHS = [("X-Api-Key", lambda k: k),
         ("Authorization", lambda k: f"Bearer {k}"),
         ("x-api-key", lambda k: k)]

INTERESTING = ("limit", "remaining", "usage", "quota", "ratelimit", "x-request")


def get(url, header, value, timeout=25):
    req = urllib.request.Request(url, headers={
        header: value, "Accept": "application/json",
        "User-Agent": "tennis-props/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            hdrs = {k.lower(): v for k, v in r.headers.items()
                    if any(t in k.lower() for t in INTERESTING)}
            return r.status, body, hdrs
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:400], {}
    except Exception as e:
        return None, f"{type(e).__name__}: {e}", {}


def find_working():
    """The first base + auth combination the server accepts."""
    for base in BASES:
        for path in ("/sports/", "/sports", "/leagues/", "/leagues"):
            for header, fmt in AUTHS:
                st, body, hdrs = get(base + path, header, fmt(KEY))
                if st == 200:
                    print(f"  OK  {base}{path}  via header {header}")
                    return base, header, fmt(KEY), path, body, hdrs
                if st in (401, 403):
                    print(f"  {st} {base}{path}  via {header}  (reached, refused)")
                elif st is not None:
                    print(f"  {st} {base}{path}  via {header}")
    return (None,) * 6


def walk(obj, depth=0, prefix=""):
    """Print the shape of a response without dumping all of it."""
    pad = "  " * (depth + 2)
    if isinstance(obj, dict):
        for k, v in list(obj.items())[:14]:
            if isinstance(v, (dict, list)):
                print(f"{pad}{k}: {type(v).__name__}")
                if depth < 3:
                    walk(v, depth + 1, prefix + k + ".")
            else:
                s = repr(v)
                print(f"{pad}{k}: {s[:70]}")
    elif isinstance(obj, list):
        print(f"{pad}[{len(obj)} items]")
        if obj and depth < 3:
            walk(obj[0], depth + 1, prefix)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", action="store_true",
                    help="also pull one page of events (spends objects)")
    ap.add_argument("--league", default="",
                    help="league id to try for events, e.g. ATP")
    a = ap.parse_args()

    print("1. finding an endpoint the key is accepted on")
    base, header, value, path, body, hdrs = find_working()
    if not base:
        sys.exit("\nno endpoint accepted the key. Check the key, and check "
                 "whether the plan is active.")
    if hdrs:
        print("\n   usage headers the server volunteered:")
        for k, v in sorted(hdrs.items()):
            print(f"     {k}: {v}")

    print("\n2. what is in the plan")
    try:
        doc = json.loads(body)
    except ValueError:
        print("   response was not JSON:", body[:200])
        return
    walk(doc)

    text = body.lower()
    tennis = [w for w in ("tennis", "atp", "wta", "itf") if w in text]
    print("\n   tennis mentioned in the listing: "
          + (", ".join(tennis) if tennis else "NO — this is the answer that matters"))

    print("\n   every sport in the plan:")
    for s in doc.get("data", []) if isinstance(doc, dict) else []:
        print(f"     {str(s.get('sportID')):14} enabled={s.get('enabled')}"
              f"  {s.get('name')}")

    # The league ids are what the events call needs, and the first run showed
    # guessing them costs a 400 each. Ask instead.
    print("\n3. the tennis leagues, by their real ids")
    leagues = []
    for q in ("/leagues/?sportID=TENNIS", "/leagues/"):
        st, lbody, _ = get(base + q, header, value)
        print(f"   {st} {q}")
        if st != 200:
            print(f"      {lbody[:300]}")
            continue
        try:
            ldoc = json.loads(lbody)
        except ValueError:
            continue
        rows = ldoc.get("data", []) if isinstance(ldoc, dict) else ldoc
        rows = [x for x in rows if isinstance(x, dict)]
        # Every league, not just the tennis ones: "none came back" has to be
        # distinguishable from "the filter looked at the wrong field".
        print(f"     {len(rows)} leagues returned:")
        for x in rows:
            print(f"       {str(x.get('leagueID')):16} "
                  f"{str(x.get('sportID')):12} enabled={x.get('enabled')}")
        leagues = [x for x in rows
                   if "TENNIS" in str(x.get("sportID", "")).upper()]
        for x in leagues:
            print(f"     {str(x.get('leagueID')):16} enabled={x.get('enabled')}"
                  f"  {x.get('name') or x.get('shortName')}")
        if leagues:
            break
    if not leagues:
        print("   no tennis league came back -- the sport may be listed but "
              "not carry leagues on this plan")

    # This endpoint also returns the account's email, customer id and key
    # id. The workflow's log is public because the repository is, so only
    # the plan and its limits are printed -- never who the account is. An
    # earlier version walked the whole response and put the email in a
    # public log; that log was deleted.
    print("\n4. what the account has used")
    for q in ("/account/usage/", "/account/usage"):
        st, ubody, uh = get(base + q, header, value)
        print(f"   {st} {q}")
        if st == 200:
            try:
                u = json.loads(ubody).get("data", {})
            except (ValueError, AttributeError):
                print("   not JSON")
                break
            print(f"     tier: {u.get('tier')}  active: {u.get('isActive')}")
            for span, lim in (u.get("rateLimits") or {}).items():
                if isinstance(lim, dict):
                    print(f"     {span:10} " + "  ".join(
                        f"{k}={v}" for k, v in lim.items()))
            break
        print(f"      {ubody[:300]}")

    if not a.events:
        print("\n5. skipped the events call (costs objects). Re-run with "
              "--events to see one.")
        return

    print("\n5. one small events call per tennis league")
    ids = ([a.league] if a.league else
           [x.get("leagueID") for x in leagues if x.get("enabled", True)])
    for lid in ids[:4]:
        q = f"/events/?leagueID={lid}&oddsAvailable=true&limit=3"
        st, ebody, ehdrs = get(base + q, header, value)
        print(f"   {st} {q}")
        if st != 200:
            # The first run printed only "400". The body says why.
            print(f"      {ebody[:400]}")
            continue
        try:
            edoc = json.loads(ebody)
        except ValueError:
            print("   not JSON"); continue
        walk(edoc)
        with open("sgo_sample.json", "w") as f:
            json.dump(edoc, f, indent=1)
        print("\n   full response written to sgo_sample.json")
        if ehdrs:
            print("   usage headers:")
            for k, v in sorted(ehdrs.items()):
                print(f"     {k}: {v}")
        return
    print("   no tennis events came back with odds -- see the messages above")


if __name__ == "__main__":
    main()
