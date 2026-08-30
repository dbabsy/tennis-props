"""Tour-stop coordinates, so conditions can be attached to a tournament.

Only the site matters, not the individual court: air density is a property of
the air over the whole grounds. Elevation is filled in from Open-Meteo on first
use rather than hard-coded, so a wrong guess here self-corrects.

`indoor` marks venues that are roofed by default -- those are the control group
for any conditions effect, because their air is conditioned and their ball
flight does not care what the weather is doing outside.
"""

import re

# name fragment -> (lat, lon, indoor)
VENUES = {
    "melbourne":        (-37.8216, 144.9787, False),
    "australian open":  (-37.8216, 144.9787, False),
    "roland garros":    (48.8470, 2.2530, False),
    "french open":      (48.8470, 2.2530, False),
    "wimbledon":        (51.4340, -0.2140, False),
    "us open":          (40.7500, -73.8458, False),
    "indian wells":     (33.7240, -116.3050, False),
    "miami":            (25.9580, -80.2390, False),
    "monte carlo":      (43.7500, 7.4280, False),
    "madrid":           (40.4360, -3.6710, False),
    "rome":             (41.9330, 12.4550, False),
    "canada":           (43.6640, -79.4180, False),
    "montreal":         (45.5040, -73.6100, False),
    "toronto":          (43.6640, -79.4180, False),
    "cincinnati":       (39.3450, -84.2700, False),
    "shanghai":         (31.1200, 121.3120, False),
    "paris masters":    (48.8380, 2.2530, True),
    "tour finals":      (45.0740, 7.6510, True),
    "doha":             (25.2620, 51.4460, False),
    "dubai":            (25.2280, 55.3390, False),
    "acapulco":         (16.8280, -99.9060, False),
    "rio":              (-22.9770, -43.2210, False),
    "buenos aires":     (-34.5450, -58.4200, False),
    "santiago":         (-33.4200, -70.6100, False),
    "barcelona":        (41.3860, 2.1180, False),
    "munich":           (48.1490, 11.5860, False),
    "estoril":          (38.7060, -9.3960, False),
    "geneva":           (46.2100, 6.1400, False),
    "hamburg":          (53.5760, 9.9880, False),
    "stuttgart":        (48.7700, 9.2130, False),
    "halle":            (52.0570, 8.3630, False),
    "queen":            (51.4840, -0.2130, False),
    "eastbourne":       (50.7660, 0.2740, False),
    "mallorca":         (39.6250, 2.5300, False),
    "newport":          (41.4790, -71.3170, False),
    "bastad":           (56.4250, 12.8500, False),
    "gstaad":           (46.4720, 7.2830, False),
    "umag":             (45.4330, 13.5230, False),
    "kitzbuhel":        (47.4460, 12.3920, False),
    "atlanta":          (33.8470, -84.3660, False),
    "washington":       (38.9210, -77.0130, False),
    "winston":          (36.1320, -80.2760, False),
    "tokyo":            (35.6660, 139.7160, False),
    "beijing":          (39.9880, 116.3860, False),
    "vienna":           (48.2070, 16.3980, True),
    "basel":            (47.5410, 7.6030, True),
    "rotterdam":        (51.8930, 4.4700, True),
    "marseille":        (43.2700, 5.3950, True),
    "montpellier":      (43.6100, 3.8990, True),
    "metz":             (49.1100, 6.1770, True),
    "antwerp":          (51.2300, 4.4210, True),
    "stockholm":        (59.3470, 18.0790, True),
    "sofia":            (42.6680, 23.2900, True),
    "brisbane":         (-27.4720, 153.0290, False),
    "adelaide":         (-34.9210, 138.6000, False),
    "auckland":         (-36.8620, 174.7460, False),
    "delray":           (26.4630, -80.0730, False),
    "houston":          (29.7180, -95.4180, False),
    "los cabos":        (22.9080, -109.9160, False),
    "chengdu":          (30.6280, 104.0650, False),
    "hangzhou":         (30.2470, 120.2100, False),
    "almaty":           (43.2380, 76.9450, True),
    "bogota":           (4.6600, -74.0900, False),
    "quito":            (-0.1800, -78.4800, False),
    "cluj":             (46.7700, 23.5900, True),
    "guadalajara":      (20.6740, -103.3900, False),
    "monterrey":        (25.6510, -100.2900, False),
    "charleston":       (32.8000, -79.7900, False),
    "strasbourg":       (48.5830, 7.7500, False),
    "nottingham":       (52.9400, -1.1700, False),
    "birmingham":       (52.4600, -1.9200, False),
    "berlin":           (52.4900, 13.2600, False),
    "prague":           (50.1000, 14.4100, False),
    "lausanne":         (46.5200, 6.6300, False),
    "palermo":          (38.1200, 13.3400, False),
    "cleveland":        (41.5000, -81.6900, False),
    "seoul":            (37.5100, 127.0700, False),
    "osaka":            (34.6900, 135.5000, False),
    "ningbo":           (29.8700, 121.5500, False),
    "wuhan":            (30.5100, 114.3100, False),
    "riyadh":           (24.7100, 46.6700, True),
    "merida":           (20.9700, -89.6200, False),
    "linz":             (48.3100, 14.2900, True),
    "tenerife":         (28.4700, -16.2500, False),
    "rabat":            (34.0200, -6.8400, False),
    "s-hertogenbosch":  (51.6900, 5.3000, False),
    "hertogenbosch":    (51.6900, 5.3000, False),
    "adelaide 2":       (-34.9210, 138.6000, False),
}

_elev_cache = {}


def find(tourney_name):
    """Best-effort match of a tournament name to a site. Returns None if the
    stop is unknown -- callers must treat conditions as unavailable, not zero."""
    n = re.sub(r"[^a-z ]", " ", (tourney_name or "").lower())
    hits = [(k, v) for k, v in VENUES.items() if k in n]
    if not hits:
        return None
    # longest fragment wins: "paris masters" beats a bare "paris"
    k, v = max(hits, key=lambda kv: len(kv[0]))
    return {"key": k, "lat": v[0], "lon": v[1], "indoor": v[2]}
