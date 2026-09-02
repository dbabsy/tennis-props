"""Raw data pulls for the tennis model. Everything is cached under .cache/.

Five sources, all keyless:

  archive_matches  Sackmann mirror, match-level serve stats, ATP+WTA to 2026-05-25
  espn_draw        ESPN scoreboard: full draws, set scores, court, start time
  odds_rows        tennis-data.co.uk weekly xlsx: results + closing odds
  charting         Match Charting Project point-level derived stats
  weather          Open-Meteo hourly conditions + venue elevation

Jeff Sackmann's own tennis_atp/tennis_wta repos went private in 2026; ARCHIVE
is a mirror and is the reason this file does not point at the canonical URLs.
"""

import csv
import gzip
import io
import json
import re
import ssl
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / ".cache"
CACHE.mkdir(exist_ok=True)

ARCHIVE = "https://raw.githubusercontent.com/Aneeshers/tennis-sackmann-archive/main"
CHARTS = "https://raw.githubusercontent.com/JeffSackmann/tennis_MatchChartingProject/master"
ESPN = "https://site.api.espn.com/apis/site/v2/sports/tennis"
ODDS = "http://www.tennis-data.co.uk"
METEO = "https://api.open-meteo.com/v1"

# ESPN 403s any user agent that claims to be a browser -- its bot check is
# inverted, and a plain honest agent is what gets through. Do not "fix" this
# by pasting in a Chrome string.
UA = "tennis-props/1.0 (+https://github.com/dbabsy/tennis-props)"
_CTX = ssl.create_default_context()


def _get(url, ttl=86400, binary=False):
    """Fetch a URL, caching the body on disk for ttl seconds."""
    key = re.sub(r"[^A-Za-z0-9._-]", "_", url)[-180:]
    path = CACHE / key
    if path.exists() and time.time() - path.stat().st_mtime < ttl:
        return path.read_bytes() if binary else path.read_text(
            encoding="utf-8", errors="replace")

    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Encoding": "gzip"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60, context=_CTX) as r:
                body = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
            break
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)

    path.write_bytes(body)
    return body if binary else body.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------
# Sackmann archive: the only source with per-match serve statistics
# --------------------------------------------------------------------------

# Tour-level events only. Challengers and futures use a different calibre of
# opponent and pollute the serve ratings if mixed in unweighted.
TOUR_LEVELS = {"G", "M", "A", "F", "D", "P", "PM", "I", "T1", "T2"}

INT_COLS = ("w_ace w_df w_svpt w_1stIn w_1stWon w_2ndWon w_SvGms w_bpSaved "
            "w_bpFaced l_ace l_df l_svpt l_1stIn l_1stWon l_2ndWon l_SvGms "
            "l_bpSaved l_bpFaced minutes draw_size best_of winner_rank "
            "loser_rank winner_rank_points loser_rank_points").split()


def archive_matches(tour, year, ttl=86400 * 7):
    """One season of tour-level singles matches with serve stats."""
    stem = "atp" if tour == "atp" else "wta"
    url = f"{ARCHIVE}/{stem}/{stem}_matches_{year}.csv"
    try:
        text = _get(url, ttl=ttl)
    except Exception:
        return []

    out = []
    for row in csv.DictReader(io.StringIO(text)):
        surf = (row.get("surface") or "").strip().title()
        if surf not in ("Hard", "Clay", "Grass", "Carpet"):
            continue
        for col in INT_COLS:
            v = row.get(col)
            row[col] = int(v) if v not in (None, "", "NA") and v.lstrip(
                "-").isdigit() else None
        row["surface"] = surf
        row["tour"] = tour
        row["date"] = _tdate(row.get("tourney_date"))
        out.append(row)
    return out


def _tdate(yyyymmdd):
    try:
        return date(int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:8]))
    except Exception:
        return None


def archive_seasons(tour, start, end):
    rows = []
    for y in range(start, end + 1):
        rows.extend(archive_matches(tour, y))
    return rows


def rankings(tour, ttl=86400):
    """Current weekly ranking list: player_id -> (rank, points)."""
    stem = "atp" if tour == "atp" else "wta"
    text = _get(f"{ARCHIVE}/{stem}/{stem}_rankings_current.csv", ttl=ttl)
    latest, out = None, {}
    for row in csv.DictReader(io.StringIO(text)):
        d = row["ranking_date"]
        if latest is None or d > latest:
            latest, out = d, {}
        if d == latest:
            out[row["player"]] = (int(row["rank"]), int(row["points"] or 0))
    return out


def players(tour, ttl=86400 * 30):
    """player_id -> name, hand, height, country, birthdate."""
    stem = "atp" if tour == "atp" else "wta"
    text = _get(f"{ARCHIVE}/{stem}/{stem}_players.csv", ttl=ttl)
    out = {}
    for r in csv.DictReader(io.StringIO(text)):
        out[r["player_id"]] = {
            "name": f"{r.get('name_first','')} {r.get('name_last','')}".strip(),
            "hand": r.get("hand") or "U",
            "height": int(r["height"]) if (r.get("height") or "").isdigit() else None,
            "ioc": r.get("ioc") or "",
            "dob": r.get("dob") or "",
        }
    return out


# --------------------------------------------------------------------------
# ESPN: the live layer. Fills everything after the archive's 2026-05-25 cutoff.
# --------------------------------------------------------------------------

def espn_draw(day=None, tour="atp", ttl=900):
    """Every singles match ESPN knows about for the tournaments live on `day`.

    ESPN keys the scoreboard by tournament, not by match, so one call returns
    the entire draw -- played and unplayed alike. `day` may be any date; past
    dates return that week's completed events.
    """
    url = f"{ESPN}/{tour}/scoreboard"
    if day:
        url += "?dates=" + day.strftime("%Y%m%d")
    # Live scores must not be served stale; finished draws can be.
    doc = json.loads(_get(url, ttl=ttl))

    out = []
    for ev in doc.get("events", []):
        tname = ev.get("name") or ""
        for grouping in ev.get("groupings", []):
            draw = grouping.get("grouping", {}).get("displayName", "")
            if "Singles" not in draw:
                continue
            sex = "w" if draw.startswith("Women") else "m"
            for c in grouping.get("competitions", []):
                m = _espn_match(c, tname, sex, ev)
                if m:
                    out.append(m)
    return out


def _espn_match(c, tname, sex, ev):
    comps = c.get("competitors", [])
    if len(comps) != 2:
        return None

    side = []
    for x in comps:
        ath = x.get("athlete") or {}
        side.append({
            "id": x.get("id"),
            "name": ath.get("displayName") or x.get("name") or "",
            "short": ath.get("shortName") or "",
            "won": bool(x.get("winner")),
            "sets": [s.get("value") for s in (x.get("linescores") or [])],
            "tb": [s.get("tiebreak") for s in (x.get("linescores") or [])],
        })

    st = c.get("status", {}).get("type", {})
    # Who is serving, when ESPN says. It is worth knowing: at 5-4 in a
    # deciding set the same scoreline is a 0.93 win for the server and 0.66
    # for the receiver, so a live number that guesses is badly blurred. ESPN
    # has moved this field around, so several spellings are tried and the
    # answer is None rather than a guess when none of them is there.
    serving = None
    sit = c.get("situation") or {}
    holder = sit.get("possession") or sit.get("server")
    if isinstance(holder, dict):
        holder = holder.get("id") or holder.get("athleteId")
    for i, x in enumerate(comps):
        if x.get("possession") or x.get("serving") or x.get("hasPossession"):
            serving = i
        elif holder and str(holder) in (str(x.get("id")),
                                        str((x.get("athlete") or {}).get("id"))):
            serving = i
    venue = c.get("venue") or {}
    try:
        start = datetime.strptime(c["date"], "%Y-%m-%dT%H:%MZ").replace(
            tzinfo=timezone.utc)
    except Exception:
        start = None

    return {
        "id": c.get("id"),
        "event_id": ev.get("id"),
        "tourney": tname,
        "sex": sex,
        "tour": "wta" if sex == "w" else "atp",
        "round": (c.get("round") or {}).get("displayName", ""),
        "best_of": (c.get("format") or {}).get("regulation", {}).get("periods", 3),
        "state": st.get("state", ""),          # pre | in | post
        "serving": serving,                    # 0, 1, or None when unknown
        "completed": bool(st.get("completed")),
        "detail": st.get("detail", ""),
        "court": venue.get("court", ""),
        "site": venue.get("fullName", ""),
        "start": start,
        "p1": side[0],
        "p2": side[1],
    }


def espn_backfill(start, end, tour="atp", step=7):
    """Walk a date range a week at a time and dedupe. Recovers the matches the
    archive is missing -- results and set scores, but not serve statistics."""
    seen, out = set(), []
    d = start
    while d <= end:
        for m in espn_draw(d, tour=tour, ttl=86400 * 7):
            if m["id"] not in seen and m["completed"]:
                seen.add(m["id"])
                out.append(m)
        d += timedelta(days=step)
    return out


# --------------------------------------------------------------------------
# tennis-data.co.uk: results plus closing odds. Updated weekly.
# --------------------------------------------------------------------------

_XLNS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _xlsx_rows(blob):
    z = zipfile.ZipFile(io.BytesIO(blob))
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(_XLNS + "si"):
            shared.append("".join(t.text or "" for t in si.iter(_XLNS + "t")))

    sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    grid = []
    for r in sheet.iter(_XLNS + "row"):
        cells = {}
        for c in r.findall(_XLNS + "c"):
            col = re.match(r"[A-Z]+", c.get("r")).group()
            v = c.find(_XLNS + "v")
            if v is None:
                val = ""
            elif c.get("t") == "s":
                val = shared[int(v.text)]
            else:
                val = v.text
            cells[col] = val
        grid.append(cells)

    if not grid:
        return []
    header = grid[0]
    return [{header[k]: row.get(k) for k in header if header.get(k)}
            for row in grid[1:]]


def _excel_date(serial):
    try:
        return date(1899, 12, 30) + timedelta(days=int(float(serial)))
    except Exception:
        return None


def odds_rows(tour, year, ttl=86400):
    """Closing odds and results. Bet365, Pinnacle, Max, Average, Betfair."""
    path = f"{year}/{year}.xlsx" if tour == "atp" else f"{year}w/{year}.xlsx"
    try:
        blob = _get(f"{ODDS}/{path}", ttl=ttl, binary=True)
    except Exception:
        return []

    out = []
    for r in _xlsx_rows(blob):
        if not r.get("Winner"):
            continue
        r["date"] = _excel_date(r.get("Date"))
        r["tour"] = tour
        for k in ("PSW", "PSL", "B365W", "B365L", "AvgW", "AvgL", "MaxW", "MaxL"):
            try:
                r[k] = float(r[k])
            except (TypeError, ValueError):
                r[k] = None
        out.append(r)
    return out


# --------------------------------------------------------------------------
# Match Charting Project: point-level detail on a charted subset
# --------------------------------------------------------------------------

def charting(sex="m", table="Overview", ttl=86400 * 7):
    """Derived point-level stats. table: Overview, ServeBasics, ServeDirection,
    KeyPointsServe, KeyPointsReturn, Rally, NetPoints, ReturnDepth, SnV ..."""
    url = f"{CHARTS}/charting-{sex}-stats-{table}.csv"
    return list(csv.DictReader(io.StringIO(_get(url, ttl=ttl))))


# --------------------------------------------------------------------------
# Open-Meteo: the conditions layer
# --------------------------------------------------------------------------

def weather(lat, lon, when=None, tz="UTC", ttl=3600):
    """Hourly temp/humidity/wind/pressure plus the venue's ground elevation."""
    q = urllib.parse.urlencode({
        "latitude": round(lat, 4), "longitude": round(lon, 4),
        "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,"
                  "wind_direction_10m,surface_pressure,precipitation_probability",
        "timezone": tz, "forecast_days": 3,
    })
    doc = json.loads(_get(f"{METEO}/forecast?{q}", ttl=ttl))
    h = doc.get("hourly", {})
    hours = []
    for i, t in enumerate(h.get("time", [])):
        hours.append({
            "time": t,
            "temp_c": h["temperature_2m"][i],
            "rh": h["relative_humidity_2m"][i],
            "wind_kmh": h["wind_speed_10m"][i],
            "wind_dir": h["wind_direction_10m"][i],
            "pressure_hpa": h["surface_pressure"][i],
            "precip_pct": h["precipitation_probability"][i],
        })
    return {"elevation_m": doc.get("elevation"), "hours": hours}


def air_density(temp_c, rh, pressure_hpa):
    """kg/m3 by the CIPM approximation. Thin air lets the ball through faster,
    which is the whole reason conditions matter to a serve model."""
    t = temp_c + 273.15
    # Saturation vapour pressure, Tetens, in Pa
    svp = 610.78 * pow(10, (7.5 * temp_c) / (temp_c + 237.3))
    pv = (rh / 100.0) * svp
    pd = pressure_hpa * 100.0 - pv
    return (pd / (287.058 * t)) + (pv / (461.495 * t))
