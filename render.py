"""Shared HTML shell for the published pages.

Everything is inlined -- no CDN, no build step -- so a page is one file that
opens correctly from disk or from Pages. The palette is defined on :root and
overridden for dark, so the pages follow the reader's system theme.
"""

import html
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


def bar(p, width=52):
    p = max(0.0, min(1.0, p or 0))
    return f'<span class="bar" style="width:{width}px"><i style="width:{100*p:.0f}%"></i></span>'


def page(title, subtitle, body, active="", note="", theme=None, event=None):
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
<title>{esc(title)}</title><style>{CSS}</style>{skin}</head>
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
