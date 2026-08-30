"""Does the air actually change how tennis is played?

The premise worth testing, borrowed from the ballpark work: thinner air (hot,
humid, high, low pressure) offers the ball less resistance, so serves arrive
faster and are harder to return. If true, ace rates and hold rates should rise
as air density falls, and indoor events -- whose air is conditioned and whose
density barely moves -- should show no such slope.

Nothing here is assumed. `measure()` fits the slope and reports it with a
standard error so it can be dismissed if it is noise. Treat a slope inside two
standard errors as no effect and leave the conditions multiplier at 1.0.
"""

import json
import math
import urllib.parse
from collections import defaultdict
from datetime import date, timedelta

import fetch
import venues

ARCHIVE_API = "https://archive-api.open-meteo.com/v1/archive"

# Reference density: roughly sea level, 20C, half humidity. Multipliers are
# expressed relative to this so that 1.0 means "ordinary conditions".
RHO_REF = 1.195


def venue_weather(lat, lon, start, end, ttl=86400 * 30):
    """Daily means for one site over a date range, in a single call."""
    q = urllib.parse.urlencode({
        "latitude": round(lat, 3), "longitude": round(lon, 3),
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "daily": "temperature_2m_mean,relative_humidity_2m_mean,"
                 "surface_pressure_mean,wind_speed_10m_max,precipitation_sum",
        "timezone": "UTC",
    })
    doc = json.loads(fetch._get(f"{ARCHIVE_API}?{q}", ttl=ttl))
    d = doc.get("daily", {})
    elev = doc.get("elevation")
    out = {}
    for i, t in enumerate(d.get("time", [])):
        temp = d["temperature_2m_mean"][i]
        rh = d["relative_humidity_2m_mean"][i]
        pres = d["surface_pressure_mean"][i]
        if None in (temp, rh, pres):
            continue
        out[t] = {
            "temp_c": temp, "rh": rh, "pressure_hpa": pres,
            "wind_kmh": d["wind_speed_10m_max"][i],
            "precip_mm": d["precipitation_sum"][i],
            "elevation_m": elev,
            "rho": fetch.air_density(temp, rh, pres),
        }
    return out


def attach(matches, span_days=14):
    """Give every match at a known outdoor site its air density.

    Tournament dates in the archive are the start of the event, so a window is
    fetched and the match is charged the mean over the event -- charging it to
    day one would be wrong for a fortnight-long slam.
    """
    want = defaultdict(list)
    for m in matches:
        v = venues.find(m.get("tourney_name"))
        if not v or v["indoor"]:
            continue
        want[(v["key"], v["lat"], v["lon"])].append(m)

    tagged = []
    for (key, lat, lon), group in want.items():
        lo = min(m["date"] for m in group)
        hi = max(m["date"] for m in group) + timedelta(days=span_days)
        try:
            wx = venue_weather(lat, lon, lo, hi)
        except Exception:
            continue
        for m in group:
            days = [wx.get((m["date"] + timedelta(days=i)).isoformat())
                    for i in range(span_days)]
            days = [d for d in days if d]
            if not days:
                continue
            m = dict(m)
            m["rho"] = sum(d["rho"] for d in days) / len(days)
            m["temp_c"] = sum(d["temp_c"] for d in days) / len(days)
            m["elevation_m"] = days[0]["elevation_m"]
            m["wind_kmh"] = sum(d["wind_kmh"] or 0 for d in days) / len(days)
            m["venue"] = key
            tagged.append(m)
    return tagged


def _ols(xs, ys):
    """Slope, intercept and the slope's standard error."""
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - b * mx
    resid = [y - (a + b * x) for x, y in zip(xs, ys)]
    s2 = sum(r * r for r in resid) / (n - 2)
    return {"slope": b, "intercept": a, "se": math.sqrt(s2 / sxx), "n": n,
            "r": _corr(xs, ys)}


def _corr(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx * sy == 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def measure(tagged, ratings=None):
    """Regress per-match ace rate and serve-points-won on air density.

    When `ratings` is supplied the response is the residual after removing who
    was serving and who was returning, which is the only version worth
    believing: high-altitude events draw different fields, and without the
    adjustment the slope would partly be measuring the entry list.
    """
    rows = []
    for m in tagged:
        for me, opp in (("w", "l"), ("l", "w")):
            svpt = m.get(f"{me}_svpt")
            if not svpt or svpt < 40:
                continue
            pid = m[f"{'winner' if me == 'w' else 'loser'}_id"]
            oid = m[f"{'winner' if opp == 'w' else 'loser'}_id"]
            ace = (m.get(f"{me}_ace") or 0) / svpt
            spw = ((m.get(f"{me}_1stWon") or 0)
                   + (m.get(f"{me}_2ndWon") or 0)) / svpt
            if ratings is not None:
                exp_ace = ratings.ace_rate(pid, oid)
                exp_spw = ratings.matchup(pid, oid, m["surface"])[0]
                ace, spw = ace - exp_ace, spw - exp_spw
            rows.append({"rho": m["rho"], "ace": ace, "spw": spw,
                         "elev": m["elevation_m"], "temp": m["temp_c"],
                         "surface": m["surface"], "venue": m["venue"]})

    out = {}
    for field in ("ace", "spw"):
        out[field] = _ols([r["rho"] for r in rows], [r[field] for r in rows])
    out["rows"] = len(rows)
    out["rho_range"] = (min(r["rho"] for r in rows), max(r["rho"] for r in rows))
    return out, rows


def multiplier(rho, slope, ref=RHO_REF, base=None):
    """Turn a fitted slope into a rate multiplier. Returns 1.0 when the slope
    is None, which is what a non-result should do."""
    if not slope or not base:
        return 1.0
    return max(0.75, min(1.25, 1.0 + slope * (rho - ref) / base))
