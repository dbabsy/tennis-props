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
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import conditions as C
import dk
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


def event_head(tourney, tour, surface, n, unit=("match", "matches")):
    """A tournament, as a header: name, tour, surface, whether it is under a
    roof, and how many rows follow. Before this the event was named only in
    the page badge, which on a week with five events does not say which match
    is in which."""
    v = venues.find(tourney)
    chips = [f'<span class="chip">{V.esc((tour or "").upper())}</span>']
    if surface:
        chips.append(f'<span class="chip">{V.esc(surface)}</span>')
    if v and v["indoor"]:
        chips.append('<span class="chip">indoor</span>')
    return (f'<span class="ev">{V.esc(tourney or "Other events")}</span>'
            + " ".join(chips)
            + f'<span class="evc">{n} {unit[0] if n == 1 else unit[1]}</span>')


def event_groups(items, key, unit=("match", "matches")):
    """Split items by tournament and tour, busiest tournament first.

    key(item) -> (tourney, tour, surface). Order within a group is the
    caller's, so sort by start time first. Men and women at the same event
    are separate groups under the same name -- they are different draws with
    different formats, and at a slam different numbers of sets.
    """
    by, size = defaultdict(list), Counter()
    for it in items:
        tn, tour, _ = key(it)
        by[(tn, tour)].append(it)
        size[tn] += 1
    order = sorted(by, key=lambda k: (-size[k[0]], k[0] or "", k[1] or ""))
    return [(event_head(tn, tour, key(by[(tn, tour)][0])[2],
                        len(by[(tn, tour)]), unit), by[(tn, tour)])
            for tn, tour in order]


def attach_dk(rows, cache):
    """DraftKings' prices for each row, oriented to its players, or None."""
    idx = dk.index(cache)
    n = 0
    for r in rows:
        m = r["match"]
        r["dk"] = dk.lookup(idx, m["p1"]["name"], m["p2"]["name"], m["start"])
        n += r["dk"] is not None
    return n


def _am(x):
    return f"{int(x):+d}"


def _value(r):
    """The side DraftKings is paying more than the model thinks it should, as
    that side's expected return at DraftKings' price -- or nothing."""
    d = r.get("dk")
    if not d:
        return '<span class="dim">—</span>'
    m = r["match"]
    sides = [(dk.ev(r["p_a"], d["p1"]), m["p1"]["name"]),
             (dk.ev(r["p_b"], d["p2"]), m["p2"]["name"])]
    e, who = max(sides)
    if e <= 0:
        return '<span class="dim">none</span>'
    return (f'<span class="good">{V.esc(who.split()[-1])} +{100 * e:.1f}%</span>')


def two(a, b):
    """A value per player, stacked to sit level with that player's row."""
    return f'<div class="two"><div>{a}</div><div>{b}</div></div>'


# What a bettor most needs to know about this model is where not to trust it,
# and every item here is a measurement rather than a caveat for its own sake.
BETTOR_NOTE = (
    '<div class="callout"><b>Before betting off these numbers</b><ul>'
    '<li><b>Fair is break-even, not a bet.</b> A price is only worth taking if '
    'it beats fair by more than this model\'s own error.</li>'
    '<li><b>The closing line has beaten this model</b> — by about 0.03 in log '
    'loss, steadily across both tours in 2024 and 2025, when last measured. '
    'Where the two disagree, the market is right more often.</li>'
    '<li><b>Totals lean over.</b> Best-of-three overs priced at 52.7% landed '
    '46.9% (ATP 2024), and the WTA leans the same way. Read every over here as '
    'a few points too high and every under as a few points too low.</li>'
    '</ul></div>')



def _sides(pr):
    m = pr["match"]
    return m["p1"]["name"], m["p2"]["name"]


# ---------------------------------------------------------------------------

def page_matches(rows, theme=None, event=None, dk_at=None):
    priced = any(r.get("dk") for r in rows)
    never = datetime.max.replace(tzinfo=timezone.utc)
    rs = sorted(rows, key=lambda r: r["match"]["start"] or never)
    groups = []
    for head, grp in event_groups(
            rs, lambda r: (r["match"]["tourney"], r["match"]["tour"],
                           r["surface"])):
        trs = []
        for r in grp:
            m = r["match"]
            fa = r["p_a"] >= 0.5
            lines = TOTAL_LINES[r["best_of"]]
            mid = lines[len(lines) // 2]
            ov = model.total_over(r["dist"], mid)
            sets_txt = " ".join(
                f'{k[0]}-{k[1]}&nbsp;{100*v:.0f}%'
                for k, v in sorted(r["sets"].items(), key=lambda x: -x[1])[:3])
            d = r.get("dk")
            dkcells = ([two(_am(d["p1"]), _am(d["p2"])) if d else
                        '<span class="dim">—</span>', _value(r)]
                       if priced else [])
            trs.append([
                f'<span class="dim">{V.clock(m["start"])}</span>',
                V.who(m["p1"], bold=fa) + V.who(m["p2"], bold=not fa),
                two(V.pct(r["p_a"]), V.pct(r["p_b"])),
                two(V.fair(r["p_a"]), V.fair(r["p_b"])),
                *dkcells,
                V.num(r["exp_games"], 1),
                f'o{mid} {V.pct(ov, 0)} <span class="dim">{V.fair(ov)}</span>',
                V.pct(r["p_straight_a"] + _straight_b(r), 0),
                f'<span class="dim">{sets_txt}</span>',
                f'<span class="dim">{V.esc(m["round"])}</span>',
            ])
        groups.append((head, trs))
    heads = (['Time <span class="tz">CT</span>', "Match", "Win %", "Fair"]
             + (["DK", "Value"] if priced else [])
             + ["Games", "Total", "Straight", "Likeliest sets", "Round"])
    aligns = (["", "", "num", "num"] + (["num", ""] if priced else [])
              + ["num", "num", "num", "", ""])
    body = [BETTOR_NOTE, V.grouped_table(heads, groups, aligns)]
    if priced:
        body.append(
            '<p class="note">DK is DraftKings\' match-winner price, from The '
            'Odds API, fetched <time datetime="' + V.esc(dk_at or "") + '" '
            'data-fmt="datetime">' + V.esc((dk_at or "")[:16].replace("T", " "))
            + ' UTC</time>. It is refreshed about twice a day and covers only '
            'the bigger events — slams, 1000s and some 500s — so most '
            'matches in a normal week have none. Value is the model\'s expected '
            'return on the side DraftKings is paying more for than the model '
            'thinks fair; read it with the box above, because the closing line '
            'has beaten this model. Check the live price before betting.</p>')
    body.append(
        '<p class="note">Win percentages come from a point model, not a '
        'match model: each player\'s serve and return rates are opponent- and '
        'surface-adjusted, then propagated point to game to set to match. '
        '"Fair" is the American price at which a bet breaks even; hover it for '
        'the decimal. The favourite is in bold.</p>')
    return V.page("Match projections",
                  "Win probability, total games and set scores for today's slate",
                  "\n".join(body), "matches.html", theme=theme, event=event)


def _straight_b(r):
    """P(B wins in straight sets) -- match_dist reports it only for A."""
    need = r["best_of"] // 2 + 1
    return r["sets"].get((0, need), 0.0)


def page_props(rows, theme=None, event=None):
    body = []
    groups = []
    for head, grp in event_groups(
            rows, lambda r: (r["match"]["tourney"], r["match"]["tour"],
                             r["surface"]), unit=("match", "matches")):
        trs = []
        for r in grp:
            m = r["match"]
            for tag, me, opp in (("a", m["p1"], m["p2"]),
                                 ("b", m["p2"], m["p1"])):
                pp = r["props"][tag]
                ace_line = max(1.5, round(pp["exp_aces"]) - 0.5)
                df_line = max(1.5, round(pp["exp_dfs"]) - 0.5)
                o_ace = model.over(pp["aces"], ace_line)
                o_df = model.over(pp["dfs"], df_line)
                trs.append([
                    V.who(me, extra=f' <span class="dim">v '
                          f'{V.esc(opp["name"].split()[-1])}</span>'),
                    V.num(pp["sv_points"], 0),
                    V.num(pp["exp_aces"], 1),
                    f'o{ace_line} <span class="{"good" if o_ace>.5 else "dim"}">'
                    f'{V.pct(o_ace,0)}</span> <span class="dim">'
                    f'{V.fair(o_ace)}</span>',
                    V.num(pp["exp_dfs"], 1),
                    f'o{df_line} <span class="{"good" if o_df>.5 else "dim"}">'
                    f'{V.pct(o_df,0)}</span> <span class="dim">'
                    f'{V.fair(o_df)}</span>',
                    # No reading is not the same as a roof: an outdoor event
                    # whose weather lookup failed used to say "indoor" here,
                    # under a header that now says otherwise.
                    (f'<span class="dim">ρ {r["cond"]["rho"]:.3f}</span>'
                     if r["cond"] else
                     '<span class="dim">indoor</span>'
                     if (venues.find(m["tourney"]) or {}).get("indoor") else
                     '<span class="dim" title="no weather reading">—</span>'),
                ])
        groups.append((head, trs))
    body.append(V.grouped_table(
        ["Player", "Serve pts", "Aces", "Ace line", "DFs", "DF line",
         "Air"],
        groups, ["", "num", "num", "", "num", "", ""]))
    body.append(
        '<p class="note">A counting prop is a rate times an opportunity, and '
        'the opportunity is the part most projections get wrong: a player who '
        'is about to be beaten in straight sets does not serve enough to reach '
        'a big ace line. Serve points here come from the same match model that '
        'produces the win probabilities, carrying the same day-to-day '
        'variation in form — an even match runs longer, and a projection that '
        'widened the match but not the serve count would quietly inflate every '
        'over. The rate is a property of the surface as much as the server: '
        'the same arm hits about two thirds as many aces on clay as on hard, '
        'so the rate is fitted per surface rather than blended across them. '
        'Counts are drawn from a beta-binomial rather than a binomial, because '
        'measured ace counts run about a third wider than binomial — treating '
        'them as binomial understates every over.</p>')
    return V.page("Player props",
                  "Ace and double-fault projections, with the serve volume behind them",
                  "\n".join(body), "props.html", theme=theme, event=event)


def page_edges(rows, theme=None, event=None):
    """Fair prices across every market the point model supports."""
    body = [BETTOR_NOTE,
            '<p class="note">No keyless source of live odds exists, so this '
            'page prices the markets rather than claiming an edge. Every price '
            'is American and break-even; hover for the decimal.</p>']
    rs = sorted(rows, key=lambda r: -max(r["p_a"], r["p_b"]))
    groups = []
    for head, grp in event_groups(
            rs, lambda r: (r["match"]["tourney"], r["match"]["tour"],
                           r["surface"])):
        trs = []
        for r in grp:
            m = r["match"]
            lines = TOTAL_LINES[r["best_of"]]
            cells = []
            for ln in lines:
                ov = model.total_over(r["dist"], ln)
                cells.append(f'{ln}: <span class="dim">o</span> {V.fair(ov)}'
                             f' <span class="dim">u</span> {V.fair(1-ov)}')
            cover = model.spread_cover_form(r["pa"], r["pb"], r["best_of"],
                                            -3.5, sigma=P.FORM_SIGMA,
                                            nodes=P.FORM_NODES)
            trs.append([
                f'<span class="name">{V.esc(m["p1"]["name"])}</span>'
                f'<br><span class="dim">{V.esc(m["p2"]["name"])}</span>',
                f'{V.fair(r["p_a"])}<br><span class="dim">'
                f'{V.fair(r["p_b"])}</span>',
                "<br>".join(cells),
                f'-3.5 {V.fair(cover)}',
                f'{V.fair(r["p_straight_a"])}<br>'
                f'<span class="dim">{V.fair(_straight_b(r))}</span>',
            ])
        groups.append((head, trs))
    body.append(V.grouped_table(
        ["Match", "Winner", "Total games", "Games spread", "Straight sets"],
        groups, ["name", "num", "", "num", "num"]))
    return V.page("Fair prices",
                  "Break-even American odds for every market the model supports",
                  "\n".join(body), "edges.html", theme=theme, event=event)


# A television scorebug rather than a line of numbers: one row per player,
# a column per set, the server marked, tiebreak margins raised. Only the live
# page ever draws one, so it is not in the shared stylesheet.
LIVE_CSS = """
.sb{display:flex;flex-direction:column;gap:4px;min-width:250px}
.sb-r{display:flex;align-items:center;gap:3px}
.sb-n{flex:1;min-width:0;white-space:nowrap;overflow:hidden;
text-overflow:ellipsis;max-width:180px}
.sb-r.up .sb-n{font-weight:650}
/* The ball sits beside whoever is serving. It is the one thing on this page
   the model knows and the reader cannot see, and at 5-4 in a deciding set it
   is worth more than the scoreline -- 0.93 against 0.66 for the same score.
   Ball yellow rather than the theme accent: it has to read as an object, and
   it is legible against every palette in both modes. */
.sb-sv{width:14px;height:14px;flex:none;display:flex;align-items:center;
justify-content:center}
.sb-ball{display:block}
.sb-g{position:relative;width:21px;height:21px;line-height:21px;flex:none;
text-align:center;border-radius:5px;background:var(--chip);color:var(--dim);
font-variant-numeric:tabular-nums;font-size:12.5px}
.sb-g.w{color:var(--fg);font-weight:650}
.sb-g.cur{outline:1.5px solid var(--accent);outline-offset:-1.5px;
color:var(--fg)}
.sb-tb{position:absolute;top:-3px;right:-3px;font-size:8.5px;line-height:1;
color:var(--dim);font-weight:500}
/* The score inside the game being played, kept visually apart from the sets
   because it is a different kind of number and it changes every rally. */
.sb-p{width:26px;height:21px;line-height:21px;flex:none;text-align:center;
border-radius:5px;margin-left:5px;font-size:12px;font-weight:650;
font-variant-numeric:tabular-nums;background:var(--accent);color:var(--bg)}
.livebar{display:flex;align-items:center;gap:9px;margin:0 0 14px;
font-size:12px;color:var(--dim);flex-wrap:wrap}
.pill{display:inline-flex;align-items:center;gap:5px;padding:2px 9px;
border-radius:999px;font-size:10.5px;letter-spacing:.07em;font-weight:650;
text-transform:uppercase;background:var(--chip);color:var(--dim)}
.pill.on{color:var(--good)}
.pill.warn{color:var(--warn)}
.pill.off{color:var(--bad)}
.dot{width:6px;height:6px;border-radius:50%;background:currentColor}
.pill.on .dot{animation:pulse 2s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}
@media(prefers-reduced-motion:reduce){.pill.on .dot{animation:none}}
/* Cards, grouped under their tournament. A table made the reader scroll
   sideways on a phone to reach the probability, which is the one number the
   page exists for. */
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));
gap:12px;margin:0 0 6px}
.mc{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:12px 14px 10px;min-width:0}
.mc-top{display:flex;gap:10px;flex-wrap:wrap;font-size:11px;font-weight:600;
text-transform:uppercase;letter-spacing:.05em;margin:0 0 8px}
.mc-top .dim{font-weight:500}
.mc-main{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:0 12px;
align-items:start}
.mc .sb{min-width:0;gap:4px}
.mc .sb-r{height:34px}
.sb-av{display:inline-flex;margin-right:6px}
.mc-p{display:flex;flex-direction:column;gap:4px}
.mc-p>div{height:34px;display:flex;align-items:center;justify-content:flex-end;
gap:8px;font-variant-numeric:tabular-nums}
.mc-p b{font-size:17px;font-weight:650;min-width:42px;text-align:right;
color:var(--dim)}
.mc-p b.hi{color:var(--fg)}
.mc-p .px{font-size:12px;color:var(--dim);min-width:48px;text-align:right}
.mc .bar{display:block;height:4px;margin:10px 0 0;min-width:0}
.mc-foot{display:flex;justify-content:space-between;align-items:baseline;
gap:6px 12px;flex-wrap:wrap;margin-top:8px;font-size:12px}
/* What the game in play is worth: the server's chance if they hold and if
   they are broken. Both are already in the table the page was sent. */
.js-sw{display:flex;gap:4px 12px;flex-wrap:wrap;align-items:baseline}
.sw-l{color:var(--dim)}
.sw-o b{font-weight:650;font-variant-numeric:tabular-nums}
.sw-o .px{color:var(--dim);font-size:11.5px;margin-left:3px}
@media(max-width:420px){.cards{grid-template-columns:1fr}
.mc .sb-n{max-width:130px}}
"""

LIVE_JS = r"""
// The page ships every answer the model has; this only decides which one to
// read and how to draw it. Nothing here re-derives a probability -- that is
// model.py's job, and a second implementation in JavaScript would be a second
// thing to keep correct.
(function () {
  var D = window.__LIVE__;
  if (!D || !D.matches.length) return;
  var NG = D.games.length, gi = {}, si = {};
  D.games.forEach(function (g, i) { gi[g] = i; });
  Object.keys(D.sets).forEach(function (bo) {
    si[bo] = {};
    D.sets[bo].forEach(function (s, i) { si[bo][s] = i; });
  });
  var lastOk = null, failing = false;

  // Names arrive from ESPN and are written into innerHTML, so they are
  // escaped rather than trusted.
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
              "'": "&#39;"}[c];
    });
  }
  function dec(tbl, i) {                       // two base-36 chars -> 0..1
    return parseInt(tbl.substr(i * 2, 2), 36) / 1295;
  }
  // 15-30-40-AD as the model counts them: points won, not the scoreboard word.
  var PVAL = {"0": 0, "15": 1, "30": 2, "40": 3, "AD": 4};
  var NP = D.ptStates.length, pidx = {};
  D.ptStates.forEach(function (s, i) { pidx[s] = i; });

  function ptIndex(pts, servingIsA) {
    if (!pts) return null;
    var a = PVAL[pts[0]], b = PVAL[pts[1]];
    if (a == null || b == null) return null;
    var sv = servingIsA ? a : b, rc = servingIsA ? b : a;
    var k = pidx[sv + "-" + rc];
    return k == null ? null : k;
  }
  // The twin of ledger._set_over: a set still being played, or abandoned, is
  // not a set somebody has won.
  function setOver(a, b) {
    var hi = Math.max(a, b), lo = Math.min(a, b);
    return hi >= 6 && (hi - lo >= 2 || hi === 7);
  }
  function derive(s1, s2, bestOf) {
    var sa = 0, sb = 0, ga = 0, gb = 0;
    for (var i = 0; i < Math.max(s1.length, s2.length); i++) {
      var a = s1[i], b = s2[i];
      if (a == null || b == null) continue;
      if (setOver(a, b)) { if (a > b) sa++; else sb++; }
      else { ga = a; gb = b; }
    }
    var need = (bestOf >> 1) + 1;
    return {sa: sa, sb: sb, ga: ga, gb: gb, done: sa >= need || sb >= need};
  }

  // Drawn rather than an emoji: an emoji is a different picture in every
  // browser and cannot be sized against a 21px games chip.
  var BALL = '<svg class="sb-ball" viewBox="0 0 12 12" width="11" height="11"'
    + ' aria-hidden="true"><circle cx="6" cy="6" r="5.3" fill="#d8e64a"/>'
    + '<path d="M2.0 2.2Q5.1 6 2.0 9.8M10.0 2.2Q6.9 6 10.0 9.8" fill="none"'
    + ' stroke="#fbfbf5" stroke-width="1" stroke-linecap="round"/></svg>';

  function scorebug(names, ls, tbs, servingRow, st, pts, avs) {
    var n = Math.max(ls[0].length, ls[1].length), html = "";
    for (var r = 0; r < 2; r++) {
      var cells = "";
      for (var i = 0; i < n; i++) {
        var a = ls[r][i], b = ls[1 - r][i];
        if (a == null) { cells += '<span class="sb-g"></span>'; continue; }
        var over = b != null && setOver(a, b);
        var cls = "sb-g" + (over ? (a > b ? " w" : "") : " cur");
        var tb = tbs && tbs[r] ? tbs[r][i] : null;
        cells += '<span class="' + cls + '">' + a
          + (over && tb != null && tb !== "" ? '<span class="sb-tb">'
             + esc(tb) + "</span>" : "")
          + "</span>";
      }
      var ahead = st && (r === 0 ? st.sa > st.sb : st.sb > st.sa);
      // The live game score, when the scoreboard gives one.
      var pt = pts && pts[r] != null && pts[r] !== ""
        ? '<span class="sb-p">' + esc(pts[r]) + "</span>" : "";
      html += '<div class="sb-r' + (ahead ? " up" : "") + '">'
        // The face is markup built and escaped by render.avatar at build
        // time; it is not re-derived from anything the scoreboard sends.
        + (avs && avs[r] ? '<span class="sb-av">' + avs[r] + "</span>" : "")
        + '<span class="sb-n">' + esc(names[r]) + "</span>"
        + '<span class="sb-sv"'
        + (servingRow === r ? ' title="serving">' + BALL : ">")
        + "</span>"
        + cells + pt + "</div>";
    }
    return html;
  }

  function lookup(m, st, servingIsA) {
    var sIdx = si[m.best_of] && si[m.best_of][st.sa + "-" + st.sb];
    var gIdx = gi[st.ga + "-" + st.gb];
    if (sIdx == null || gIdx == null) return null;
    var base = (sIdx * NG + gIdx) * 2;
    if (servingIsA === null) {
      return (dec(m.table, base) + dec(m.table, base + 1)) / 2;
    }
    return dec(m.table, base + (servingIsA ? 0 : 1));
  }

  // Where the match stands once the current game resolves. Serve alternates,
  // and a game that finishes the set resets the games and banks it.
  function afterGame(st, servingIsA, held) {
    var sa = st.sa, sb = st.sb, ga = st.ga, gb = st.gb;
    if (servingIsA === held) ga++; else gb++;
    if (setOver(ga, gb)) {
      if (ga > gb) sa++; else sb++;
      ga = 0; gb = 0;
    }
    return {sa: sa, sb: sb, ga: ga, gb: gb, srvA: !servingIsA};
  }

  function outcome(m, s) {
    var need = (m.best_of >> 1) + 1;
    if (s.sa >= need) return 1;
    if (s.sb >= need) return 0;
    return lookup(m, s, s.srvA);
  }

  // The point-level number. The match is Markov at game boundaries, so a
  // point score changes nothing except whether THIS game is held -- both
  // continuation states are already in the table. See model.point_table.
  function withPoints(m, st, servingIsA, pts) {
    if (servingIsA === null || !m.pts) return null;
    if (st.ga === 6 && st.gb === 6) return null;   // a tiebreak, not a game
    var i = ptIndex(pts, servingIsA);
    if (i == null) return null;
    var h = dec(m.pts, (servingIsA ? 0 : NP) + i);
    var w = outcome(m, afterGame(st, servingIsA, true));
    var l = outcome(m, afterGame(st, servingIsA, false));
    if (w == null || l == null) return null;
    return h * w + (1 - h) * l;
  }

  // Rounded half-up, like render.american, so the page and the build never
  // disagree by one about the same probability.
  function american(p) {
    if (p == null || p <= 0 || p >= 1) return "—";
    return p > 0.5 ? "-" + Math.floor(100 * p / (1 - p) + 0.5)
                   : "+" + Math.floor(100 * (1 - p) / p + 0.5);
  }
  function pc(p) { return (100 * p).toFixed(0) + "%"; }

  // What the game in play is worth, to the player serving it: their chance of
  // winning the match if they hold, and if they are broken. Both are entries
  // the table already holds -- this is afterGame and outcome, the same two
  // numbers withPoints mixes, shown instead of mixed. At 6-6 the game is the
  // tiebreak, and the words say so.
  function swing(m, st, srvA) {
    if (srvA === null || st.done) return "";
    var hold = outcome(m, afterGame(st, srvA, true));
    var brk = outcome(m, afterGame(st, srvA, false));
    if (hold == null || brk == null) return "";
    if (!srvA) { hold = 1 - hold; brk = 1 - brk; }
    var tb = st.ga === 6 && st.gb === 6;
    var who = String(srvA ? m.p1 : m.p2).split(" ").pop();
    var lead = '<span class="sw-l" title="' + esc(who) + "'s chance of "
      + "winning the match once this " + (tb ? "tiebreak" : "game")
      + ' is decided">' + esc(who)
      + (tb ? " serves first in the tiebreak" : " to serve") + "</span>";
    // A deciding tiebreak ends the match either way, so 100% and 0% are
    // right and say nothing. Say what they mean instead.
    if (hold >= 1 && brk <= 0) {
      return lead + '<span class="sw-o"><b>winner takes the match</b></span>';
    }
    var cell = function (label, x) {
      if (x >= 1) return '<span class="sw-o">' + label + " <b>takes the "
        + "match</b></span>";
      if (x <= 0) return '<span class="sw-o">' + label + " <b>loses the "
        + "match</b></span>";
      return '<span class="sw-o">' + label + " <b>" + pc(x) + "</b>"
        + '<span class="px">' + american(x) + "</span></span>";
    };
    return lead + cell(tb ? "wins it" : "holds", hold)
      + cell(tb ? "loses it" : "broken", brk);
  }

  function paint(m, p, bug, sw) {
    var row = document.getElementById("m-" + m.id);
    if (!row) return;
    if (bug != null) row.querySelector(".js-sb").innerHTML = bug;
    var q = function (s) { return row.querySelector(s); };
    q(".js-p").textContent = p == null ? "—" : (100 * p).toFixed(0) + "%";
    q(".js-p2").textContent = p == null ? "—"
      : (100 * (1 - p)).toFixed(0) + "%";
    q(".js-p").className = "js-p" + (p != null && p >= 0.5 ? " hi" : "");
    q(".js-p2").className = "js-p2" + (p != null && p < 0.5 ? " hi" : "");
    var o = q(".js-o"), o2 = q(".js-o2"), w = q(".js-sw");
    if (o) o.textContent = p == null ? "—" : american(p);
    if (o2) o2.textContent = p == null ? "—" : american(1 - p);
    if (w && sw != null) w.innerHTML = sw;
    var bar = q(".js-bar > i");
    if (bar && p != null) bar.style.width = (100 * p).toFixed(0) + "%";
    var mv = q(".js-move");
    if (mv && p != null) {
      var d = p - m.p_pre;
      mv.textContent = (d >= 0 ? "+" : "") + (100 * d).toFixed(0);
      mv.className = "js-move num "
        + (Math.abs(d) < 0.05 ? "dim" : (d > 0 ? "good" : "bad"));
    }
  }

  // ESPN publishes the server intermittently. It was on the competitor as a
  // boolean `possession` one afternoon and absent from the same match an hour
  // later, situation object and all. The build sees one sample every two
  // hours and the refresh sees one every half minute, so without a memory the
  // ball blinks on and off and the probability jumps between the sharp number
  // and the blunt average -- at 5-4 in a decider that is 0.93 against the mean
  // of 0.93 and 0.66, moving for a reason the reader cannot see.
  //
  // One sighting is enough for the rest of the match. Serve alternates every
  // game and keeps alternating across set boundaries -- whoever received the
  // last game of a set serves the first of the next -- and a tiebreak is one
  // game like any other, so the parity of completed games since the sighting
  // says who is serving now. This is derivation, not a guess: it is the same
  // rotation model.py walks when it builds the table.
  var ANCHOR = {};

  function played(ls) {
    var n = 0;
    for (var r = 0; r < 2; r++) {
      for (var i = 0; i < ls[r].length; i++) n += ls[r][i] || 0;
    }
    return n;
  }

  // Every place fetch._espn_match looks, in the same order, so the page and
  // the build cannot disagree about who is serving the same match.
  function seenServer(c, ai, bi) {
    var A = c.competitors[ai], B = c.competitors[bi];
    if (A.possession === true || A.serving === true) return true;
    if (B.possession === true || B.serving === true) return false;
    var sit = c.situation || {};
    var who = sit.possession != null ? sit.possession : sit.server;
    if (who && typeof who === "object") who = who.id || who.athleteId;
    if (who != null && String(who) !== "") {
      if (String(who) === String(A.id)) return true;
      if (String(who) === String(B.id)) return false;
    }
    return null;
  }

  function serverNow(id, seen, games) {
    if (seen !== null) {
      ANCHOR[id] = {a: seen, games: games};
      return seen;
    }
    var k = ANCHOR[id];
    if (!k) return null;
    return (Math.abs(games - k.games) % 2) ? !k.a : k.a;
  }

  // The first paint comes from the scores the page was built with, so the
  // scorebug is drawn once here and again on every refresh -- one function,
  // not a server-rendered version and a client-rendered version that could
  // disagree about the same match.
  function initial() {
    D.matches.forEach(function (m) {
      var ls = [(m.sets && m.sets[0]) || [], (m.sets && m.sets[1]) || []];
      var st = derive(ls[0], ls[1], m.best_of);
      var srvA = serverNow(m.id, m.serving == null ? null : m.serving === 0,
                           played(ls));
      var serving = srvA === null ? null : (srvA ? 0 : 1);
      var p = ls[0].length
        ? (st.done ? (st.sa > st.sb ? 1 : 0)
           : (withPoints(m, st, srvA, m.points) !== null
              ? withPoints(m, st, srvA, m.points) : lookup(m, st, srvA)))
        : m.p_pre;
      paint(m, p, scorebug([m.p1, m.p2], ls, m.tb, serving, st, m.points,
                           m.avs), swing(m, st, srvA));
    });
  }

  function apply(events) {
    var byId = {};
    events.forEach(function (ev) {
      (ev.groupings || []).forEach(function (g) {
        (g.competitions || []).forEach(function (c) { byId[c.id] = c; });
      });
    });
    var seen = 0;
    D.matches.forEach(function (m) {
      var c = byId[m.id];
      if (!c || !c.competitors || c.competitors.length !== 2) return;
      // ESPN orders the two competitors independently of how this page stored
      // them, so the orientation is read off the names, not the position.
      var nm = c.competitors.map(function (x) {
        return (x.athlete && x.athlete.displayName) || x.name || "";
      });
      var ai = nm.indexOf(m.p1), bi = nm.indexOf(m.p2);
      if (ai < 0 || bi < 0 || ai === bi) return;
      var ls = [ai, bi].map(function (k) {
        return (c.competitors[k].linescores || []).map(function (s) {
          return s.value;
        });
      });
      var tbs = [ai, bi].map(function (k) {
        return (c.competitors[k].linescores || []).map(function (s) {
          return s.tiebreak;
        });
      });
      var st0 = derive(ls[0], ls[1], m.best_of), st = st0;
      var servingIsA = serverNow(m.id, seenServer(c, ai, bi), played(ls));
      // The score inside the game being played, if the scoreboard has one.
      // A tiebreak counts 1, 2, 3 instead of 15, 30, 40; those are shown but
      // not priced, because the model has no mid-tiebreak entry.
      var inTb = st0.ga === 6 && st0.gb === 6;
      var pts = [ai, bi].map(function (k) {
        var x = c.competitors[k], v = x.score;
        if (v && typeof v === "object") v = v.displayValue || v.value;
        for (var j = 0, alt = [v, x.points, x.gameScore]; j < 3; j++) {
          var t = alt[j] == null ? null : String(alt[j]).trim().toUpperCase();
          if (t === "A" || t === "ADV") t = "AD";
          if (t == null || t === "") continue;
          if (PVAL[t] != null) return t;
          if (inTb && /^\d{1,2}$/.test(t)) return t;
        }
        return null;
      });
      var fine = st.done ? null : withPoints(m, st, servingIsA, pts);
      var p = st.done ? (st.sa > st.sb ? 1 : 0)
        : (fine !== null ? fine : lookup(m, st, servingIsA));
      paint(m, p, scorebug([m.p1, m.p2], ls, tbs,
                           servingIsA === null ? null : (servingIsA ? 0 : 1),
                           st, pts, m.avs), swing(m, st, servingIsA));
      seen++;
    });
    return seen;
  }

  // How old the numbers on screen are. A live page that has quietly stopped
  // refreshing looks exactly like one that is up to date, which is the whole
  // reason to say so out loud.
  function ago(ms) {
    var s = Math.round(ms / 1000);
    if (s < 5) return "just now";
    if (s < 60) return s + "s ago";
    var mi = Math.round(s / 60);
    return mi < 60 ? mi + " min ago" : Math.round(mi / 60) + "h ago";
  }
  function clock() {
    var pill = document.getElementById("live-pill");
    var txt = document.getElementById("live-clock");
    if (!pill || !txt) return;
    if (lastOk === null) {
      pill.className = "pill warn";
      pill.innerHTML = '<span class="dot"></span>not live';
      txt.textContent = "showing the scores this page was built with"
        + (D.built ? ", " + new Date(D.built).toLocaleTimeString() : "");
      return;
    }
    var age = Date.now() - lastOk;
    var stale = failing || age > 120000;
    pill.className = "pill " + (stale ? "off" : "on");
    pill.innerHTML = '<span class="dot"></span>' + (stale ? "stale" : "live");
    txt.textContent = (stale ? "last successful update " : "updated ")
      + ago(age) + " · " + new Date(lastOk).toLocaleTimeString();
  }

  function refresh() {
    var tours = ["atp", "wta"];
    Promise.all(tours.map(function (t) {
      return fetch("https://site.api.espn.com/apis/site/v2/sports/tennis/"
        + t + "/scoreboard").then(function (r) { return r.json(); });
    })).then(function (docs) {
      var evs = [];
      docs.forEach(function (d) { evs = evs.concat(d.events || []); });
      apply(evs);
      lastOk = Date.now();
      failing = false;
      clock();
    }).catch(function () {
      // The scoreboard is not reachable from the browser. The page keeps what
      // it has and says how old it is rather than going stale in silence.
      failing = true;
      clock();
    });
  }

  initial();
  clock();
  refresh();
  setInterval(refresh, 30000);
  setInterval(clock, 1000);
})();
"""


def _live_card(m):
    """One match, drawn like a broadcast graphic: faces and sets on the left,
    each player's chance and fair price level with their row on the right,
    and underneath, what the game being played is worth."""
    p = m["p_pre"]
    when = ""
    if m.get("state") != "in" and m.get("start"):
        when = ('<span class="dim">starts '
                + V.clock(datetime.fromisoformat(m["start"])) + "</span>")
    who = m.get("who") or [{"name": m["p1"]}, {"name": m["p2"]}]
    rows = "".join(
        f'<div class="sb-r"><span class="sb-av">{V.avatar(w, 30)}</span>'
        f'<span class="sb-n">{V.esc(w.get("name", ""))}</span></div>'
        for w in who)
    return f"""<article class="mc" id="m-{V.esc(m["id"])}">
<div class="mc-top"><span>{V.esc(m["round"])}</span>
<span class="dim">best of {m["best_of"]}</span>{when}</div>
<div class="mc-main"><div class="js-sb sb">{rows}</div>
<div class="mc-p">
<div><b class="js-p{" hi" if p >= .5 else ""}">{V.pct(p, 0)}</b><span class="js-o px">{V.american(p)}</span></div>
<div><b class="js-p2{" hi" if p < .5 else ""}">{V.pct(1 - p, 0)}</b><span class="js-o2 px">{V.american(1 - p)}</span></div>
</div></div>
<span class="bar js-bar"><i style="width:{100 * p:.0f}%"></i></span>
<div class="mc-foot"><span class="js-sw"></span>
<span class="dim">pre-match {V.pct(p, 0)} · move <span class="js-move num dim">&mdash;</span></span></div>
<noscript><p class="note">{V.esc(_score_text(m))}</p></noscript>
</article>"""


def page_live(live, theme=None, event=None):
    """Matches on court now, priced from where they stand.

    The model is a forward walk from the first point, so a live number is not
    a different model -- it is the same walk started at the current score.
    That is the whole reason this page can exist without a second engine
    disagreeing with the first one about the same match.
    """
    if not live:
        body = ['<p class="note">Nothing is on court from the draws this '
                'build could see. This page fills in when a match is under '
                'way, or within three hours of starting.</p>']
        return V.page("Live", "In-match win probability, from the same model",
                      "\n".join(body), "live.html", theme=theme, event=event)

    # On court first, then by start time.
    live = sorted(live, key=lambda m: (m.get("state") != "in",
                                       m.get("start") or ""))
    for m in live:
        # Drawn once, here, and shipped as markup: the scorebug is redrawn on
        # every poll, and a second avatar renderer in JavaScript would be a
        # second thing to keep in step with this one.
        m["avs"] = [V.avatar(w, 30) for w in
                    (m.get("who") or [{"name": m["p1"]}, {"name": m["p2"]}])]
    sections = []
    for head, ms in event_groups(
            live, lambda m: (m["tourney"], m["tour"], m["surface"])):
        sections.append(f'<h2 class="evt">{head}</h2><div class="cards">'
                        + "".join(_live_card(m) for m in ms) + "</div>")

    payload = {
        "built": datetime.now(timezone.utc).isoformat(),
        "games": [f"{a}-{b}" for a, b in model.game_states()],
        "sets": {str(bo): [f"{a}-{b}" for a, b in model.set_states(bo)]
                 for bo in (3, 5)},
        "ptStates": [f"{a}-{b}" for a, b in model.point_states()],
        "matches": live,
    }
    # The payload now carries markup, so a "</" anywhere in it -- a name, a
    # URL -- must not be able to close the script element it sits in.
    blob = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    body = [
        '<div class="livebar"><span class="pill" id="live-pill">'
        '<span class="dot"></span>connecting</span>'
        '<span id="live-clock">checking the scoreboard…</span></div>',
        "".join(sections),
        '<p class="note">A live probability here is the same propagation that '
        'produces the pre-match number, entered at the current score instead '
        'of at the first point — at 0-0 it returns exactly the figure on '
        'the matches page. Beside each chance is its fair American price: '
        'break-even, not a recommendation. Under each match, when the server is '
        'known, is what the game in play is worth — the server\'s chance of '
        'winning the match if they hold, and if they are broken. The '
        'resolution is a game, not a point, because no free feed publishes '
        'the score inside a game.</p>',
        '<script>window.__LIVE__=' + blob + ';</script>',
        f"<script>{LIVE_JS}</script>",
    ]
    return V.page("Live", f"{len(live)} matches on court or about to be",
                  "\n".join(body), "live.html", theme=theme, event=event,
                  extra_css=LIVE_CSS)


def _score_text(m):
    """The plain fallback for a reader without JavaScript, who never sees the
    scorebug because the scorebug is drawn by the script."""
    a, b = m.get("sets") or ([], [])
    pairs = [f"{x}-{y}" for x, y in zip(a or [], b or [])
             if x is not None and y is not None]
    return " ".join(pairs) if pairs else "not started"


def page_conditions(rows, theme=None, event=None):
    cards = []
    seen = {}
    for r in rows:
        c = r["cond"]
        if c and c["venue"] not in seen:
            seen[c["venue"]] = c
    for key, c in sorted(seen.items()):
        # In ace-rate points, which is the unit the slope was measured in.
        ace_shift = P.ACE_RHO_SLOPE * (c["rho"] - C.RHO_REF)
        cards.append(f"""<div class="card">
<div class="lab">{V.esc(key)}</div>
<div class="stat">{c['rho']:.3f} <span class="lab">kg/m³</span></div>
<p class="note">{c['temp_c']:.0f}°C · {c['rh']:.0f}% RH ·
{c['wind_kmh']:.0f} km/h wind · {c['elevation_m']:.0f} m ·
rain {c['precip_pct']:.0f}%<br>
ace rate {100*ace_shift:+.2f} points versus ordinary sea-level air</p></div>""")

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

def _report_skipped(skipped, show=8):
    """Say which matches were dropped and why.

    Without this the answer to "why is that match not on the page?" is a
    silence that looks identical whether the model refused the players, the
    names failed to match the archive, or the match was never on the
    scoreboard at all.
    """
    if not skipped:
        return
    by_reason = defaultdict(list)
    for s in skipped:
        by_reason[s["reason"].split(":")[0]].append(s)
    for kind, group in sorted(by_reason.items(), key=lambda kv: len(kv[1])):
        print(f"      {len(group)} {kind}")
        # A draw position waiting on an earlier round has no name to print and
        # nothing to fix, so only the count is worth the line. The groups are
        # ordered smallest first for the same reason: the short ones are the
        # ones somebody can act on.
        if kind == "undetermined":
            continue
        for s in group[:show]:
            print(f"        {s['p1']} v {s['p2']}  ({s['reason']})")
        if len(group) > show:
            print(f"        ... and {len(group) - show} more")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    day = date.fromisoformat(a.date) if a.date else None
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    rows, live = [], []
    for tour in ("atp", "wta"):
        try:
            # "in" as well as "pre": the live page prices what is on court,
            # and one ratings fit serves both.
            _, _, rs, skipped = P.build(tour, day=day,
                                        states=("pre", "in"))
        except Exception as e:
            print(f"  {tour}: {type(e).__name__}: {e}")
            continue
        for r in rs:
            if P.wants_live(r["match"]):
                live.append(P.live_view(r))
        # Every other page is about matches that have not started.
        rs = [r for r in rs if r["match"]["state"] == "pre"]
        print(f"  {tour}: {len(rs)} projected, {len(skipped)} skipped")
        _report_skipped(skipped)
        rows += rs
    print(f"  live tables: {len(live)}")

    theme, event = slate_theme(rows)
    print(f"  theme: {theme} ({themes.label(theme)}) — {event}")

    # DraftKings prices: refreshed from The Odds API only when the cached ones
    # are due, because the free allowance is 500 requests a month -- see dk.py.
    prices = dk.load()
    print(f"  DraftKings prices on {attach_dk(rows, prices)} matches")
    dk_at = (prices or {}).get("fetched_at")

    for name, fn in (("index.html", page_conditions),
                     ("props.html", page_props),
                     ("edges.html", page_edges)):
        (out / name).write_text(fn(rows, theme, event), encoding="utf-8")
        print(f"  wrote {name}")
    (out / "matches.html").write_text(
        page_matches(rows, theme, event, dk_at), encoding="utf-8")
    print("  wrote matches.html")
    (out / "live.html").write_text(page_live(live, theme, event),
                                   encoding="utf-8")
    print("  wrote live.html")

    # Deployed alongside the site so the CORS question can be answered from
    # the origin that actually matters. Opening it from disk sends
    # "Origin: null", which some hosts refuse even when a real origin passes.
    # Not in the nav: it is a diagnostic, not a page.
    check = Path(__file__).resolve().parent / "livecheck.html"
    if check.exists():
        (out / "livecheck.html").write_text(check.read_text(encoding="utf-8"),
                                            encoding="utf-8")
        print("  wrote livecheck.html")

    # The accuracy page is built by ledger.py but must not look like a
    # different site, so the chosen theme is handed over on disk.
    (Path(__file__).resolve().parent / "data" / "theme.json").write_text(
        json.dumps({"theme": theme, "event": event}))


if __name__ == "__main__":
    main()
