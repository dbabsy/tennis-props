#!/usr/bin/env python3
"""Find out what The Odds API gives for tennis, before building on it.

SportsGameOdds' free tier turned out to carry no tennis league at all (see
CLAUDE.md), so this asks the same questions of the next candidate:

  1. Which tennis events does it list, and are any active now? The Odds API
     keys tennis by tournament -- tennis_atp_us_open and so on -- rather than
     by tour, so coverage is a list of events, not a yes or no.
  2. Does DraftKings price them, and which markets: match winner only, or
     games totals and spreads too?
  3. What does it cost? The free plan is a monthly credit allowance, and a
     call costs one credit per market per region. The remaining balance
     comes back in response headers, which is the only usage report there is.

Run (the workflow odds-probe.yml does this with the ODDS_API_KEY secret):
    export ODDS_API_KEY=...          # never put it in a file in the repo
    python3 odds_probe.py            # listing only, costs nothing
    python3 odds_probe.py --odds     # about four credits

The key travels in the query string, so no URL is ever printed whole: every
line shows the path and the other parameters, never apiKey. The workflow's
log is public because the repository is.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.the-odds-api.com/v4"
KEY = os.environ.get("ODDS_API_KEY", "").strip()
if not KEY:
    sys.exit("set ODDS_API_KEY in the environment first (do not put it in a file)")

USAGE = ("x-requests-remaining", "x-requests-used", "x-requests-last")


def call(path, **params):
    shown = path + ("?" + urllib.parse.urlencode(params) if params else "")
    q = urllib.parse.urlencode(dict(params, apiKey=KEY))
    req = urllib.request.Request(f"{BASE}{path}?{q}", headers={
        "Accept": "application/json", "User-Agent": "tennis-props/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            use = {k: r.headers.get(k) for k in USAGE if r.headers.get(k)}
            return r.status, json.loads(r.read().decode("utf-8")), use, shown
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300], {}, shown
    except Exception as e:
        return None, f"{type(e).__name__}: {e}", {}, shown


def usage(use):
    if use:
        print("     credits: " + "  ".join(
            f"{k.replace('x-requests-', '')}={v}" for k, v in use.items()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--odds", action="store_true",
                    help="also price the active tennis events (costs credits)")
    a = ap.parse_args()

    print("1. every tennis event the API knows, active or not")
    st, doc, use, shown = call("/sports/", all="true")
    print(f"   {st} {shown}")
    if st != 200:
        sys.exit(f"   refused: {doc}")
    usage(use)
    tennis = [s for s in doc if str(s.get("group", "")).lower() == "tennis"
              or str(s.get("key", "")).startswith("tennis")]
    for s in tennis:
        print(f"     {'ACTIVE ' if s.get('active') else 'off    '}"
              f"{s.get('key'):40} {s.get('title')}")
    active = [s["key"] for s in tennis if s.get("active")]
    print(f"   {len(tennis)} tennis events listed, {len(active)} active now")

    if not a.odds:
        print("\n2. skipped the odds calls (they cost credits); re-run with --odds")
        return
    if not active:
        print("\n2. nothing active, so nothing to price -- re-run during an "
              "event the listing above names")
        return

    print("\n2. DraftKings match-winner prices on the active events (1 credit each)")
    sample = None
    for key in active[:3]:
        st, ev, use, shown = call(f"/sports/{key}/odds/", regions="us",
                                  markets="h2h", oddsFormat="american",
                                  bookmakers="draftkings")
        print(f"   {st} {shown}")
        usage(use)
        if st != 200:
            print(f"      {ev}")
            continue
        priced = [e for e in ev if e.get("bookmakers")]
        print(f"     {len(ev)} events, {len(priced)} priced by DraftKings")
        for e in priced[:4]:
            outs = e["bookmakers"][0]["markets"][0]["outcomes"]
            print("       " + "  v  ".join(
                f"{o['name']} {o['price']:+d}" for o in outs)
                + f"   ({e.get('commence_time')})")
        if priced and sample is None:
            sample = (key, priced[0])

    if sample:
        key, e = sample
        print(f"\n3. which other markets DraftKings prices on {key} (2 credits)")
        st, ev, use, shown = call(f"/sports/{key}/odds/", regions="us",
                                  markets="totals,spreads",
                                  oddsFormat="american", bookmakers="draftkings")
        print(f"   {st} {shown}")
        usage(use)
        if st == 200:
            kinds = {}
            for x in ev:
                for b in x.get("bookmakers", []):
                    for m in b.get("markets", []):
                        kinds[m["key"]] = kinds.get(m["key"], 0) + 1
            print(f"     markets seen, by event count: {kinds or 'none'}")
            full = next((x for x in ev if x.get("bookmakers")), None)
        else:
            print(f"      {ev}")
            full = None
        with open("odds_sample.json", "w") as f:
            json.dump({"h2h": e, "totals_spreads": full}, f, indent=1)
        print("\n4. one event in full, to write a parser against:")
        print(json.dumps({"h2h": e, "totals_spreads": full}, indent=1)[:12000])


if __name__ == "__main__":
    main()
