"""DraftKings match-winner prices for tennis, from The Odds API.

What the free tier gives, measured before any of this was written (see
CLAUDE.md): head-to-head prices only, at the 46 tournaments The Odds API names
-- slams, Masters and WTA 1000s, a dozen 500s -- and nothing at the 250s. So
most matches on a given day will not have a price, and that is the feed, not a
bug.

The allowance is 500 credits a month. Listing the sports is free; one
match-winner request for one tournament is one credit. The build runs every two
hours, which would spend that in a week, so prices are cached in
data/dk_odds.json -- committed alongside the ledger, which is how anything
survives from one CI run to the next -- and refreshed only when the cache is
older than REFRESH_HOURS. At a two-hourly build that is twice a day.

A price is never allowed to be stale without saying so: the pages print when
it was fetched, and a cache older than MAX_AGE_HOURS is not used at all.
"""

import json
import os
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "data" / "dk_odds.json"
BASE = "https://api.the-odds-api.com/v4"
BOOK = "draftkings"

REFRESH_HOURS = 11      # at a two-hourly build: about twice a day
MAX_AGE_HOURS = 30      # older than this and the prices are not shown
FLOOR = 40              # credits kept back each month for probes and retries


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def norm(name):
    """A player's name as a key: accents stripped, letters only, no spaces.

    The Odds API writes "Viktória Morvayová" and ESPN may not; the archive
    writes "Xin Yu Wang" and ESPN writes "Xinyu Wang". Both have put the wrong
    price, or the wrong player, on a page before, so neither accent nor
    spacing is allowed to decide a match here.
    """
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join(c for c in s.lower() if c.isalpha())


def implied(price):
    """An American price as the probability it implies, margin included."""
    try:
        x = float(price)
    except (TypeError, ValueError):
        return None
    if x == 0:
        return None
    return -x / (-x + 100) if x < 0 else 100 / (x + 100)


def decimal(price):
    x = float(price)
    return 1 + (100 / -x if x < 0 else x / 100)


def no_vig(pa, pb):
    """DraftKings' own view of player A, with its margin taken out."""
    a, b = implied(pa), implied(pb)
    return a / (a + b) if a and b else None


def ev(p, price):
    """Expected profit per unit staked at this price, if p is the truth."""
    return p * decimal(price) - 1


# ---------------------------------------------------------------------------

def _get(path, key, **params):
    q = urllib.parse.urlencode(dict(params, apiKey=key))
    req = urllib.request.Request(f"{BASE}{path}?{q}", headers={
        "Accept": "application/json", "User-Agent": "tennis-props/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        left = r.headers.get("x-requests-remaining")
        return json.loads(r.read().decode("utf-8")), left


def fetch(key, get=_get):
    """Every DraftKings head-to-head price at the tennis events running now.

    `get` is injectable so selftest can run this without a network.
    """
    sports, left = get("/sports/", key)
    active = [s["key"] for s in sports
              if str(s.get("key", "")).startswith("tennis") and s.get("active")]
    matches = []
    for sk in active:
        events, left = get(f"/sports/{sk}/odds/", key, regions="us",
                           markets="h2h", oddsFormat="american",
                           bookmakers=BOOK)
        for e in events:
            book = next((b for b in e.get("bookmakers") or []
                         if b.get("key") == BOOK), None)
            mk = next((m for m in (book or {}).get("markets") or []
                       if m.get("key") == "h2h"), None)
            outs = (mk or {}).get("outcomes") or []
            if len(outs) != 2:
                continue
            matches.append({
                "event": e.get("sport_title") or sk,
                "start": e.get("commence_time"),
                "a": outs[0]["name"], "pa": outs[0]["price"],
                "b": outs[1]["name"], "pb": outs[1]["price"],
                "at": mk.get("last_update") or book.get("last_update"),
            })
    try:
        remaining = int(left) if left is not None else None
    except ValueError:
        remaining = None
    return {"fetched_at": _iso(_now()), "remaining": remaining,
            "events": active, "matches": matches}


def _read():
    try:
        return json.loads(CACHE.read_text())
    except (OSError, ValueError):
        return None


def load(key=None, now=None, get=_get, write=True):
    """The prices to use this build, refreshing them only when they are due.

    A refresh happens when there is a key, the cache is older than
    REFRESH_HOURS, and either the last known balance is above FLOOR or the
    month has turned since (the allowance resets monthly, and the balance on
    file would otherwise stop refreshes forever). Any failure keeps the cache
    as it was. Returns None when there is nothing recent enough to show.
    """
    now = now or _now()
    key = key if key is not None else os.environ.get("ODDS_API_KEY", "").strip()
    cache = _read()
    at = _parse((cache or {}).get("fetched_at"))
    due = at is None or now - at >= timedelta(hours=REFRESH_HOURS)
    left = (cache or {}).get("remaining")
    new_month = at is not None and (at.year, at.month) != (now.year, now.month)
    funded = left is None or left > FLOOR or new_month
    if key and due and funded:
        try:
            cache = fetch(key, get=get)
            if write:
                CACHE.parent.mkdir(parents=True, exist_ok=True)
                CACHE.write_text(json.dumps(cache, indent=1, sort_keys=True))
            print(f"dk: {len(cache['matches'])} DraftKings prices across "
                  f"{len(cache['events'])} events; "
                  f"{cache['remaining']} credits left this month")
        except Exception as e:  # noqa: BLE001 - prices are an extra
            print(f"dk: refresh failed ({type(e).__name__}); keeping the cache")
    elif key and due:
        print(f"dk: {left} credits left, at or under the floor of {FLOOR}; "
              f"not refreshing until the allowance resets")
    at = _parse((cache or {}).get("fetched_at"))
    if not cache or at is None or now - at > timedelta(hours=MAX_AGE_HOURS):
        return None
    return cache


def index(cache):
    """{frozenset of both players' keys: price record}."""
    out = {}
    for m in (cache or {}).get("matches", []):
        out[frozenset((norm(m["a"]), norm(m["b"])))] = m
    return out


def lookup(idx, p1, p2, start=None):
    """DraftKings' prices for p1 and p2, oriented to them, or None.

    Both names must match -- one matching name and one not is how a result
    gets inverted -- and when both sides know the start time they must agree
    within a day, so a rematch next month cannot borrow today's price.
    """
    k1, k2 = norm(p1), norm(p2)
    m = idx.get(frozenset((k1, k2)))
    if not m or k1 == k2:
        return None
    ms = _parse(m.get("start"))
    if start and ms and abs((ms - start).total_seconds()) > 36 * 3600:
        return None
    pa, pb = (m["pa"], m["pb"]) if norm(m["a"]) == k1 else (m["pb"], m["pa"])
    return {"p1": pa, "p2": pb, "at": m.get("at")}
