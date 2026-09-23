"""Shared HTML shell for the published pages.

Everything is inlined -- no CDN, no build step -- so a page is one file that
opens correctly from disk or from Pages. The palette is defined on :root and
overridden for dark, so the pages follow the reader's system theme.
"""

import html
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import themes

NAV = [
    ("index.html", "Conditions"),
    ("live.html", "Live"),
    ("matches.html", "Matches"),
    ("props.html", "Props"),
    ("edges.html", "Edges"),
    ("accuracy.html", "Accuracy"),
]

CSS = """
:root{--bg:#fbfbfa;--fg:#1a1a18;--dim:#6b6b66;--line:#e3e3df;--card:#fff;
--good:#1a7f4b;--bad:#b3261e;--warn:#96690c;--accent:#2f5fd0;--chip:#f0f0ec;--glow:none}
@media(prefers-color-scheme:dark){:root{--bg:#141414;--fg:#e8e8e4;--dim:#9a9a94;
--line:#2c2c2a;--card:#1c1c1b;--good:#4ec27f;--bad:#f2695f;--warn:#d9a640;
--accent:#7aa2f7;--chip:#242422}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);min-height:100vh;
font:15px/1.5 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif}
/* The tournament wash sits behind the content, never over it. */
body::before{content:"";position:fixed;inset:0;z-index:-1;
background:var(--glow,none);pointer-events:none}
.wrap{max-width:1180px;margin:0 auto;padding:20px 18px 60px}
h1{font-size:22px;margin:0 0 4px;letter-spacing:-.01em}
h2{font-size:16px;margin:28px 0 10px;letter-spacing:-.01em}
.sub{color:var(--dim);font-size:13px;margin:0 0 18px}
nav{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 20px;
border-bottom:1px solid var(--line);padding-bottom:12px}
nav a{color:var(--dim);text-decoration:none;font-size:13px;padding:5px 10px;
border-radius:6px}
nav a:hover{background:var(--chip);color:var(--fg)}
nav a.on{background:var(--fg);color:var(--bg)}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;font-size:13px;min-width:640px}
th{text-align:left;font-weight:600;color:var(--dim);font-size:11px;
text-transform:uppercase;letter-spacing:.04em;padding:8px 10px;
border-bottom:1px solid var(--line);white-space:nowrap;position:sticky;top:0;
background:var(--bg);backdrop-filter:blur(6px)}
td{padding:8px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
tr:hover td{background:var(--chip)}
.num{font-variant-numeric:tabular-nums;text-align:right}
.name{font-weight:550}
.good{color:var(--good)}.bad{color:var(--bad)}.warn{color:var(--warn)}
.dim{color:var(--dim)}
.chip{display:inline-block;background:var(--chip);border-radius:5px;
padding:1px 7px;font-size:11px;color:var(--dim)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:14px 16px;margin:0 0 14px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}
.stat{font-size:24px;font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.lab{font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:.04em}
.bar{height:5px;background:var(--chip);border-radius:3px;overflow:hidden;
min-width:52px}
.bar>i{display:block;height:100%;background:var(--accent)}
.note{font-size:12px;color:var(--dim);margin:8px 0 0;max-width:70ch;line-height:1.6}
.event{display:inline-block;font-size:11px;letter-spacing:.09em;
text-transform:uppercase;color:var(--accent);font-weight:650;margin:0 0 6px}
.tz{font-size:10px;color:var(--dim);font-weight:500}
footer{margin-top:36px;padding-top:14px;border-top:1px solid var(--line);
font-size:12px;color:var(--dim)}
/* A tournament header inside a table: one tbody per event, so the columns
   line up down the whole page instead of re-sizing per event. */
tr.evh td,tr.evh:hover td{background:none;padding:22px 10px 8px;
border-bottom:1px solid var(--fg)}
tbody:first-of-type tr.evh td{padding-top:8px}
.ev{font-weight:650;font-size:14.5px;color:var(--fg);margin-right:8px;
letter-spacing:-.01em;white-space:normal}
.evc{color:var(--dim);font-size:12px;margin-left:4px}
h2.evt{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;
padding-bottom:6px;border-bottom:1px solid var(--fg)}
/* A player: initials underneath, the photograph over them when it loads.
   If it does not, the image removes itself and the initials are what is
   left -- never a broken-image icon. */
.av{position:relative;display:inline-flex;align-items:center;
justify-content:center;width:var(--s,28px);height:var(--s,28px);flex:none;
border-radius:50%;background:var(--chip);color:var(--dim);
font-size:calc(var(--s,28px)*.36);vertical-align:middle}
.av>b{font-weight:650;letter-spacing:.02em}
.av>img{position:absolute;inset:0;width:100%;height:100%;border-radius:50%;
object-fit:cover;object-position:50% 18%;background:var(--chip)}
.av>img.fl{inset:auto -2px -2px auto;width:44%;height:44%;
object-position:50% 50%;box-shadow:0 0 0 1.5px var(--bg)}
.who{display:flex;align-items:center;gap:8px;min-height:30px}
.who+.who{margin-top:3px}
/* Per-player values stacked to sit level with each player's .who line. */
.two>div{min-height:30px;display:flex;align-items:center}
.two>div+div{margin-top:3px}
.num .two>div{justify-content:flex-end}
.callout{border:1px solid var(--line);border-left:3px solid var(--warn);
border-radius:8px;padding:10px 14px;margin:0 0 18px;font-size:12.5px;
max-width:88ch;background:var(--card)}
.callout b{color:var(--fg)}
.callout ul{margin:4px 0 0;padding-left:18px}
.callout li{margin:3px 0;color:var(--dim)}
.px{font-variant-numeric:tabular-nums}
"""


def esc(s):
    return html.escape(str(s if s is not None else ""))


def pct(p, digits=1):
    return "—" if p is None else f"{100*p:.{digits}f}%"


def num(x, digits=1):
    return "—" if x is None else f"{x:.{digits}f}"


def clock(dt, tz="America/Chicago"):
    """A match start time, rendered in Central and tagged for the browser.

    ESPN reports starts in UTC. Printing that unlabelled is how a 10:00 local
    match ends up reading as "15:00" -- so the server renders Central, and the
    script in `page` rewrites it to whatever zone the reader is actually in.
    Readers without JavaScript keep a correct, explicitly labelled CT time.
    """
    if not dt:
        return ""
    try:
        local = dt.astimezone(ZoneInfo(tz))
    except Exception:
        local = dt
    # 12-hour to match what Intl renders for a US reader, so the column does
    # not jump width when the localiser runs.
    return (f'<time datetime="{dt.strftime("%Y-%m-%dT%H:%M:%SZ")}">'
            f'{local.strftime("%I:%M %p").lstrip("0")}</time>')


def american(p):
    """A probability as the fair American price, the way a US book prints it.

    Rounded half-up to match Math.round, because the live page computes the
    same number in the browser and the two must not disagree by one.
    """
    if p is None or p <= 0 or p >= 1:
        return "—"
    if p > 0.5:
        return f"-{int(100 * p / (1 - p) + 0.5)}"
    return f"+{int(100 * (1 - p) / p + 0.5)}"


def fair(p):
    """The break-even price, American first -- that is the number it gets
    compared against -- with the decimal on hover."""
    if p is None or p <= 0 or p >= 1:
        return "—"
    return f'<span class="px" title="decimal {1 / p:.2f}">{american(p)}</span>'


# Player photographs. These are ESPN's, loaded by the reader's browser from
# ESPN's image host exactly as the live page already loads ESPN's scoreboard;
# nothing is copied into this repository. They are licensed photographs, which
# is a different kind of thing from a tournament's colours, so this is the one
# switch that turns them off -- the initials underneath stay either way.
PHOTOS = True
HEADSHOT = "https://a.espncdn.com/i/headshots/tennis/players/full/{}.png"


def initials(name):
    parts = [w for w in re.split(r"[\s\-]+", name or "") if w]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def photo_url(side):
    """The feed's own headshot link when it gives one, else ESPN's standard
    path for the athlete id. A wrong guess costs nothing: the image fails,
    removes itself, and the initials show."""
    if not PHOTOS or not side:
        return None
    if side.get("photo"):
        return side["photo"]
    aid = str(side.get("aid") or "")
    return HEADSHOT.format(aid) if aid.isdigit() else None


def avatar(side, size=28):
    side = side or {}
    name = side.get("name", "")
    url = photo_url(side)
    img = (f'<img src="{esc(url)}" alt="" loading="lazy" decoding="async" '
           f'referrerpolicy="no-referrer" onerror="this.remove()">'
           if url else "")
    fl = (f'<img class="fl" src="{esc(side["flag"])}" alt="" loading="lazy" '
          f'referrerpolicy="no-referrer" onerror="this.remove()">'
          if side.get("flag") else "")
    title = name + (f" · {side['country']}" if side.get("country") else "")
    return (f'<span class="av" style="--s:{size}px" title="{esc(title)}">'
            f'<b>{esc(initials(name))}</b>{img}{fl}</span>')


def who(side, bold=False, size=28, extra=""):
    """A player as a line: face, then name."""
    side = side or {}
    cls = "name" if bold else ""
    return (f'<div class="who">{avatar(side, size)}'
            f'<span class="{cls}">{esc(side.get("name", ""))}</span>{extra}</div>')


def bar(p, width=52):
    p = max(0.0, min(1.0, p or 0))
    return f'<span class="bar" style="width:{width}px"><i style="width:{100*p:.0f}%"></i></span>'


def page(title, subtitle, body, active="", note="", theme=None, event=None,
         extra_css=""):
    """extra_css carries styles only one page needs -- the live scorebug is
    a fair amount of CSS to put on four pages that will never draw one."""
    nav = "".join(
        f'<a href="{h}" class="{"on" if h == active else ""}">{esc(t)}</a>'
        for h, t in NAV)
    now = datetime.now(timezone.utc)
    built = now.astimezone(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d %H:%M CT")
    built_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    skin = f"<style>{themes.css(theme)}</style>" if theme else ""
    badge = f'<div class="event">{esc(event)}</div>' if event else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><style>{CSS}</style>{skin}{f"<style>{extra_css}</style>" if extra_css else ""}</head>
<body><div class="wrap">
<nav>{nav}</nav>
{badge}<h1>{esc(title)}</h1>
<p class="sub">{esc(subtitle)}</p>
{body}
<footer>Built <time datetime="{built_iso}" data-fmt="datetime">{built}</time>.
Model and data notes in the repository README.
Projections are estimates, not advice.{(" " + note) if note else ""}</footer>
</div>
<script>
// Times are served in US Central and tagged with their UTC instant. Rewrite
// them to the reader's own zone, and relabel the column so the number is never
// ambiguous. If this does not run, the page still shows a correct CT time.
(function () {{
  try {{
    var tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    var fmt = new Intl.DateTimeFormat([], {{hour: "2-digit", minute: "2-digit"}});
    // The build stamp is a date as well as a time; a start time is not. Using
    // the clock-only formatter on both is how the footer lost its date.
    var stamp = new Intl.DateTimeFormat([], {{year: "numeric", month: "short",
      day: "numeric", hour: "2-digit", minute: "2-digit"}});
    document.querySelectorAll("time[datetime]").forEach(function (el) {{
      var d = new Date(el.getAttribute("datetime"));
      if (isNaN(d)) return;
      el.textContent = (el.getAttribute("data-fmt") === "datetime"
        ? stamp : fmt).format(d);
    }});
    var abbr = new Intl.DateTimeFormat([], {{timeZoneName: "short"}})
      .formatToParts(new Date())
      .filter(function (p) {{ return p.type === "timeZoneName"; }})
      .map(function (p) {{ return p.value; }})[0] || tz;
    document.querySelectorAll(".tz").forEach(function (el) {{
      el.textContent = abbr;
    }});
  }} catch (e) {{ /* leave the server-rendered CT times alone */ }}
}})();
</script>
</body></html>"""


def grouped_table(headers, groups, aligns=None):
    """One table, one tbody per group, the group's header as its first row.

    A table per tournament would let each one size its own columns, and the
    page would jitter from event to event. One table keeps every column where
    the eye left it.
    """
    aligns = aligns or [""] * len(headers)
    th = "".join(f'<th class="{a}">{h}</th>' for h, a in zip(headers, aligns))
    bodies = []
    for head, rows in groups:
        trs = [f'<tr class="evh"><td colspan="{len(headers)}">{head}</td></tr>']
        for r in rows:
            tds = "".join(f'<td class="{a}">{c}</td>' for c, a in zip(r, aligns))
            trs.append(f"<tr>{tds}</tr>")
        bodies.append(f"<tbody>{''.join(trs)}</tbody>")
    return (f'<div class="scroll"><table><thead><tr>{th}</tr></thead>'
            f'{"".join(bodies)}</table></div>')


def table(headers, rows, aligns=None):
    """rows are lists of pre-rendered cell HTML."""
    aligns = aligns or [""] * len(headers)
    # headers may carry markup (the timezone tag), so they are not escaped
    th = "".join(f'<th class="{a}">{h}</th>' for h, a in zip(headers, aligns))
    body = []
    for r in rows:
        tds = "".join(f'<td class="{a}">{c}</td>' for c, a in zip(r, aligns))
        body.append(f"<tr>{tds}</tr>")
    return (f'<div class="scroll"><table><thead><tr>{th}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')
