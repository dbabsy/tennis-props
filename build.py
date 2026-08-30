"""Build every published page from one pass over the slate.

Ratings are the expensive part, so they are fitted once per tour and shared by
all pages rather than refitted per script.

On prices: there is no keyless source of live odds, so this does not pretend to
compute live edges. It publishes fair prices and the break-even price each
market needs, and the accuracy page reports how far the model has historically
sat from the closing line -- which is the number that tells you how much edge
to demand before backing anything here.
"""

import argparse
import json
import math
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import fetch
import model
import project as P
import ratings as R
import render as V
import themes
import venues

OUT = Path(__file__).resolve().parent / "public"
TOTAL_LINES = {3: (20.5, 21.5, 22.5, 23.5), 5: (36.5, 38.5, 40.5)}


def slate_theme(rows):
    """Colour the whole site after whatever is actually being played.

    During a slam that is unambiguous. An ordinary week runs several events at
    once, so the busiest one wins and the rest are named alongside it -- a page
    tinted like clay while listing a grass final would be worse than no theme.
    """
    if not rows:
        return "hard", None
    by_event = defaultdict(list)
    for r in rows:
        by_event[r["match"]["tourney"]].append(r)
    main = max(by_event, key=lambda k: len(by_event[k]))
    surface = by_event[main][0]["surface"]
    v = venues.find(main)
    key = themes.pick(main, surface, indoor=bool(v and v["indoor"]))

    others = sorted((e for e in by_event if e != main),
                    key=lambda e: -len(by_event[e]))
    label = main
    if others:
        label += " · " + " · ".join(others[:2])
        if len(others) > 2:
            label += f" +{len(others)-2}"
    return key, label


def _price(p):
    return "—" if not p or p <= 0 else f"{1/p:.2f}"


def _sides(pr):
    m = pr["match"]
    return m["p1"]["name"], m["p2"]["name"]


# ---------------------------------------------------------------------------

def page_matches(rows, theme=None, event=None):
    body = []
    for tour, label in (("atp", "ATP"), ("wta", "WTA")):
        rs = [r for r in rows if r["match"]["tour"] == tour]
        if not rs:
            continue
        rs.sort(key=lambda r: (r["match"]["start"] or datetime.max.replace(
            tzinfo=timezone.utc)))
        trs = []
        for r in rs:
            a, b = _sides(r)
            m = r["match"]
            fav, p = (a, r["p_a"]) if r["p_a"] >= 0.5 else (b, r["p_b"])
            lines = TOTAL_LINES[r["best_of"]]
            mid = lines[len(lines) // 2]
            ov = model.total_over(r["dist"], mid)
            sets_txt = " ".join(
                f'{k[0]}-{k[1]}&nbsp;{100*v:.0f}%'
                for k, v in sorted(r["sets"].items(), key=lambda x: -x[1])[:3])
            trs.append([
                f'<span class="dim">{V.esc(m["start"].strftime("%H:%M") if m["start"] else "")}</span>',
                f'<span class="name">{V.esc(a)}</span><br><span class="dim">{V.esc(b)}</span>',
                f'{V.pct(r["p_a"])}<br><span class="dim">{V.pct(r["p_b"])}</span>',
                f'{_price(r["p_a"])}<br><span class="dim">{_price(r["p_b"])}</span>',
                V.bar(p) + f' <span class="dim">{V.esc(fav.split()[-1])}</span>',
                V.num(r["exp_games"], 1),
                f'{V.pct(ov, 0)} <span class="dim">o{mid}</span>',
                V.pct(r["p_straight_a"] + _straight_b(r), 0),
                f'<span class="dim">{sets_txt}</span>',
                f'<span class="chip">{V.esc(r["surface"])}</span> '
                f'<span class="dim">{V.esc(m["round"])}</span>',
            ])
        body.append(f"<h2>{label} — {len(rs)} matches</h2>")
        body.append(V.table(
            ["Time", "Match", "Win %", "Fair", "Favourite", "Games",
             "Total", "Straight", "Likeliest sets", "Context"],
            trs,
            ["", "name", "num", "num", "", "num", "num", "num", "", ""]))
    body.append(
        '<p class="note">Win percentages come from a point model, not a '
        'match model: each player\'s serve and return rates are opponent- and '
        'surface-adjusted, then propagated point to game to set to match. '
        '"Fair" is the decimal price at which a bet breaks even — you need a '
        'price better than that, by more than the model\'s measured gap to the '
        'closing line, before there is an edge.</p>')
    return V.page("Match projections",
                  "Win probability, total games and set scores for today's slate",
                  "\n".join(body), "matches.html", theme=theme, event=event)


def _straight_b(r):
    """P(B wins in straight sets) -- match_dist reports it only for A."""
    need = r["best_of"] // 2 + 1
    return r["sets"].get((0, need), 0.0)


def page_props(rows, theme=None, event=None):
    body = []
    for tour, label in (("atp", "ATP"), ("wta", "WTA")):
        rs = [r for r in rows if r["match"]["tour"] == tour]
        if not rs:
            continue
        trs = []
        for r in rs:
            a, b = _sides(r)
            for tag, who, opp in (("a", a, b), ("b", b, a)):
                pp = r["props"][tag]
                ace_line = max(1.5, round(pp["exp_aces"]) - 0.5)
                df_line = max(1.5, round(pp["exp_dfs"]) - 0.5)
                o_ace = model.over(pp["aces"], ace_line)
                o_df = model.over(pp["dfs"], df_line)
                trs.append([
                    f'<span class="name">{V.esc(who)}</span>'
                    f'<br><span class="dim">v {V.esc(opp.split()[-1])}</span>',
                    V.num(pp["sv_points"], 0),
                    V.num(pp["exp_aces"], 1),
                    f'o{ace_line} <span class="{"good" if o_ace>.5 else "dim"}">'
                    f'{V.pct(o_ace,0)}</span> <span class="dim">'
                    f'({_price(o_ace)})</span>',
                    V.num(pp["exp_dfs"], 1),
                    f'o{df_line} <span class="{"good" if o_df>.5 else "dim"}">'
                    f'{V.pct(o_df,0)}</span> <span class="dim">'
                    f'({_price(o_df)})</span>',
                    f'<span class="chip">{V.esc(r["surface"])}</span>'
                    + (f' <span class="dim">ρ {r["cond"]["rho"]:.3f}</span>'
                       if r["cond"] else ' <span class="dim">indoor</span>'),
                ])
        body.append(f"<h2>{label}</h2>")
        body.append(V.table(
            ["Player", "Serve pts", "Aces", "Ace line", "DFs", "DF line",
             "Conditions"],
            trs, ["name", "num", "num", "", "num", "", ""]))
    body.append(
        '<p class="note">A counting prop is a rate times an opportunity, and '
        'the opportunity is the part most projections get wrong: a player who '
        'is about to be beaten in straight sets does not serve enough to reach '
        'a big ace line. Serve points here come from the same match model that '
        'produces the win probabilities. Counts are drawn from a beta-binomial '
        'rather than a binomial, because measured ace counts are about 35% '
        'wider than binomial — treating them as binomial understates every '
        'over.</p>')
    return V.page("Player props",
                  "Ace and double-fault projections, with the serve volume behind them",
                  "\n".join(body), "props.html", theme=theme, event=event)


def page_edges(rows, theme=None, event=None):
    """Fair prices across every market the point model supports."""
    body = ['<p class="note">No keyless source of live odds exists, so this '
            'page prices the markets rather than claiming an edge. Compare each '
            'fair price to your book: you need a better price, by enough to '
            'cover the gap between this model and the closing line reported on '
            'the accuracy page.</p>']
    rs = sorted(rows, key=lambda r: -max(r["p_a"], r["p_b"]))
    trs = []
    for r in rs:
        a, b = _sides(r)
        lines = TOTAL_LINES[r["best_of"]]
        cells = []
        for ln in lines:
            ov = model.total_over(r["dist"], ln)
            cells.append(f'{ln}: <span class="dim">o</span>{_price(ov)}'
                         f' <span class="dim">u</span>{_price(1-ov)}')
        cover = model.spread_cover(r["pa"], r["pb"], r["best_of"], -3.5)
        trs.append([
            f'<span class="chip">{V.esc(r["match"]["tour"].upper())}</span> '
            f'<span class="name">{V.esc(a)}</span>'
            f'<br><span class="dim">{V.esc(b)}</span>',
            f'{_price(r["p_a"])}<br><span class="dim">{_price(r["p_b"])}</span>',
            "<br>".join(cells),
            f'-3.5 {_price(cover)}',
            f'{_price(r["p_straight_a"])}<br>'
            f'<span class="dim">{_price(_straight_b(r))}</span>',
        ])
    body.append(V.table(
        ["Match", "Winner", "Total games", "Games spread", "Straight sets"],
        trs, ["name", "num", "", "num", "num"]))
    return V.page("Fair prices",
                  "Break-even decimal odds for every market the model supports",
                  "\n".join(body), "edges.html", theme=theme, event=event)


def page_conditions(rows, theme=None, event=None):
    cards = []
    seen = {}
    for r in rows:
        c = r["cond"]
        if c and c["venue"] not in seen:
            seen[c["venue"]] = c
    for key, c in sorted(seen.items()):
        ace_mult = 1.0 + P.ACE_RHO_SLOPE * (c["rho"] - 1.195) / 0.08
        cards.append(f"""<div class="card">
<div class="lab">{V.esc(key)}</div>
<div class="stat">{c['rho']:.3f} <span class="lab">kg/m³</span></div>
<p class="note">{c['temp_c']:.0f}°C · {c['rh']:.0f}% RH ·
{c['wind_kmh']:.0f} km/h wind · {c['elevation_m']:.0f} m ·
rain {c['precip_pct']:.0f}%<br>
ace rate ×{ace_mult:.3f} versus still, sea-level air</p></div>""")

    body = [f'<div class="grid">{"".join(cards)}</div>' if cards else
            '<p class="note">No outdoor venue on today\'s slate resolved to a '
            'known site.</p>']
    body.append("""<h2>What the air actually does</h2>
<p class="note">Measured on 39,085 player-matches at 60 outdoor venues,
2021–2026, after adjusting for who was serving and who was returning:</p>
<ul class="note">
<li><b>Ace rate</b> falls by 0.042 per kg/m³ of air density (SE 0.005,
t = −8.4). Across the full observed range — Bogotá's thin air to a cold
sea-level night — that is 1.3 points of ace rate, roughly one ace a match.</li>
<li><b>Serve points won</b> falls by 0.067 per kg/m³ (SE 0.009, t = −7.4),
about 2.1 points across the same range.</li>
<li>The <b>unadjusted</b> slopes are indistinguishable from zero (t = −0.2 and
+1.8). The effect only appears once you control for the players, because
high-altitude events draw fields that mask it. That is the opposite of the
usual confounding story and is the main reason to believe the adjusted
number.</li>
<li>These are real but small: r = −0.04, so conditions explain well under 1% of
match-to-match variation. They are a nudge on a projection, never a reason to
back one.</li>
</ul>
<p class="note">Indoor events are excluded throughout — their air is
conditioned and does not move with the weather, which makes them the control
group for the whole exercise.</p>""")
    return V.page("Conditions",
                  "How the air at each venue moves serve outcomes",
                  "\n".join(body), "index.html", theme=theme, event=event)


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    day = date.fromisoformat(a.date) if a.date else None
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    rows = []
    for tour in ("atp", "wta"):
        try:
            _, _, rs = P.build(tour, day=day)
        except Exception as e:
            print(f"  {tour}: {type(e).__name__}: {e}")
            continue
        print(f"  {tour}: {len(rs)} matches projected")
        rows += rs

    theme, event = slate_theme(rows)
    print(f"  theme: {theme} ({themes.label(theme)}) — {event}")

    for name, fn in (("index.html", page_conditions),
                     ("matches.html", page_matches),
                     ("props.html", page_props),
                     ("edges.html", page_edges)):
        (out / name).write_text(fn(rows, theme, event), encoding="utf-8")
        print(f"  wrote {name}")

    # The accuracy page is built by ledger.py but must not look like a
    # different site, so the chosen theme is handed over on disk.
    (Path(__file__).resolve().parent / "data" / "theme.json").write_text(
        json.dumps({"theme": theme, "event": event}))


if __name__ == "__main__":
    main()
