"""Serve and return ratings per player, per surface.

The observed serve percentage is a joint product of who served and who
returned, so a raw career average is contaminated by schedule. These ratings
are opponent-adjusted by iteration: each pass credits a player for the quality
of returner faced, then recomputes returner quality, until the two stop moving.

Three shrinkages are applied, and they are deliberately separate:

  time      exponential decay, so last month outweighs three years ago
  surface   a surface rate is shrunk toward the player's own all-surface rate
  sample    the result is shrunk toward the tour mean by that rate's own
            stabilisation point -- serve settles far faster than return, and a
            single blanket constant trusts return rates far too early

Two baselines sit under the player ratings rather than in them, because they
move everybody at once: the surface, and the format. Best-of-five is worth
about a point of serve percentage to nobody -- everyone holds less at a slam
than the same pair would over three sets -- and a model blind to that predicts
slam matches long.

Ace and double-fault rates ride along on the same machinery, because they are
also serve-vs-return quantities and are also schedule-contaminated. They are
fitted as a proportion of the surface's own rate rather than as an absolute
number of aces, because that is the shape the data has: see _fit_counts.
"""

import math
from collections import defaultdict
from datetime import date

import fetch

# Stabilisation constants, in points. Serve outcomes are close to a coin the
# server controls and settle quickly; return outcomes depend on the other
# player and take much longer. These are starting values -- backtest.py tunes
# them out of sample.
K_SERVE = 700
K_RETURN = 1400
K_SURFACE = 900        # shrinking a surface rate toward the player's overall
K_ACE = 900
K_DF = 700
K_FORMAT = 20000       # the format shift is a population effect, not a player's

# Calibration applied on top of the fitted ratings, tuned out of sample.
# The opponent-adjusted fit reproduces the mean serve percentage well but not
# its spread, and two evenly-matched players play a longer match than two
# mismatched ones, so an under-dispersed model over-predicts total games. These
# stretch the gap and nudge the level; see sweep2.py for how they were chosen.
#
# GAP_MULT was once left at 1.0 on the strength of a grid that started at 1.2
# and only looked at match log loss. Re-measured on a finer grid, with the form
# integration switched on and the games distribution scored at the lines the
# site publishes, the picture is the opposite: every step from 1.00 to 1.15
# improves match log loss, games MAE, games bias and totals log loss in all six
# of ATP/WTA x 2023/2024/2025 -- 2023 having been used to choose nothing. Past
# about 1.2 the ATP years turn back up, which is the effect the original grid
# found; it simply started beyond the useful range. See sweep2.py.
LEVEL_SHIFT = 0.0
GAP_MULT = 1.15

HALFLIFE_DAYS = 400    # weight halves after this much time
MIN_POINTS = 30        # ignore fragments (walkovers, first-game retirements)

SURFACES = ("Hard", "Clay", "Grass", "Carpet")


def _rate(won, played):
    return won / played if played else None


def observations(matches):
    """Flatten matches into one row per player-per-match serving performance."""
    out = []
    for m in matches:
        for me, opp in (("w", "l"), ("l", "w")):
            svpt = m.get(f"{me}_svpt")
            if not svpt or svpt < MIN_POINTS:
                continue
            won = (m.get(f"{me}_1stWon") or 0) + (m.get(f"{me}_2ndWon") or 0)
            out.append({
                "pid": m[f"{'winner' if me=='w' else 'loser'}_id"],
                "name": m[f"{'winner' if me=='w' else 'loser'}_name"],
                "oid": m[f"{'winner' if opp=='w' else 'loser'}_id"],
                "date": m["date"],
                "surface": m["surface"],
                "tour": m["tour"],
                "svpt": svpt,
                "spw": won / svpt,
                "ace": (m.get(f"{me}_ace") or 0) / svpt,
                "df": (m.get(f"{me}_df") or 0) / svpt,
                "best_of": m.get("best_of") or 3,
                "level": m.get("tourney_level", ""),
            })
    return out


def _weight(obs_date, asof, halflife):
    if not obs_date or not asof:
        return 1.0
    age = (asof - obs_date).days
    if age < 0:
        return 0.0
    return 0.5 ** (age / halflife)


class Ratings:
    """Fitted serve/return ratings for one tour, as of one date."""

    def __init__(self, tour, obs, asof, halflife=HALFLIFE_DAYS, passes=6,
                 k_serve=K_SERVE, k_return=K_RETURN, k_surface=K_SURFACE,
                 k_ace=K_ACE, k_df=K_DF,
                 level_shift=LEVEL_SHIFT, gap_mult=GAP_MULT):
        self.tour = tour
        self.asof = asof
        self.obs = [o for o in obs if o["date"] and o["date"] < asof]
        self.halflife = halflife
        self.k_serve, self.k_return = k_serve, k_return
        self.k_surface, self.k_ace, self.k_df = k_surface, k_ace, k_df
        self.level_shift, self.gap_mult = level_shift, gap_mult
        self._fit(passes)

    # -- fitting ----------------------------------------------------------

    def _fit(self, passes):
        w_tot = sum(_weight(o["date"], self.asof, self.halflife) * o["svpt"]
                    for o in self.obs)
        if not w_tot:
            raise ValueError("no observations before " + str(self.asof))

        self.lg_spw = sum(_weight(o["date"], self.asof, self.halflife)
                          * o["svpt"] * o["spw"] for o in self.obs) / w_tot
        self.lg_rpw = 1.0 - self.lg_spw
        self.lg_ace = sum(_weight(o["date"], self.asof, self.halflife)
                          * o["svpt"] * o["ace"] for o in self.obs) / w_tot
        self.lg_df = sum(_weight(o["date"], self.asof, self.halflife)
                         * o["svpt"] * o["df"] for o in self.obs) / w_tot

        # Surface baselines: grass and clay are genuinely different games.
        surf_num, surf_den = defaultdict(float), defaultdict(float)
        for o in self.obs:
            w = _weight(o["date"], self.asof, self.halflife) * o["svpt"]
            surf_num[o["surface"]] += w * o["spw"]
            surf_den[o["surface"]] += w
        self.surf_spw = {s: surf_num[s] / surf_den[s]
                         for s in surf_den if surf_den[s] > 0}

        # Ace and double-fault baselines are per surface too, and by a much
        # bigger margin than serve percentage: on the ATP the clay ace rate is
        # two thirds of the hard-court rate and the grass rate is a fifth
        # above it. A single blended number is wrong on every surface at once.
        self.surf_ace = self._surface_mean("ace")
        self.surf_df = self._surface_mean("df")

        # Iterate serve against return until they agree.
        serve = defaultdict(lambda: 0.0)
        ret = defaultdict(lambda: 0.0)
        for _ in range(passes):
            s_num, s_den = defaultdict(float), defaultdict(float)
            r_num, r_den = defaultdict(float), defaultdict(float)
            for o in self.obs:
                w = _weight(o["date"], self.asof, self.halflife) * o["svpt"]
                if w <= 0:
                    continue
                base = self.surf_spw.get(o["surface"], self.lg_spw)
                # credit the server for the returner faced, and vice versa
                s_num[o["pid"]] += w * (o["spw"] - base + ret[o["oid"]])
                s_den[o["pid"]] += w
                r_num[o["oid"]] += w * ((1 - o["spw"]) - (1 - base) - serve[o["pid"]])
                r_den[o["oid"]] += w
            serve = defaultdict(float, {
                p: s_num[p] / (s_den[p] + self.k_serve) for p in s_num})
            ret = defaultdict(float, {
                p: r_num[p] / (r_den[p] + self.k_return) for p in r_num})

        self.serve, self.ret = serve, ret
        self.points = {p: s_den[p] for p in s_den}
        self.ret_points = {p: r_den[p] for p in r_den}

        self._fit_format()
        self._fit_surface()
        self._fit_counts()

    def _surface_mean(self, field):
        num, den = defaultdict(float), defaultdict(float)
        for o in self.obs:
            w = _weight(o["date"], self.asof, self.halflife) * o["svpt"]
            num[o["surface"]] += w * o[field]
            den[o["surface"]] += w
        return {s: num[s] / den[s] for s in den if den[s] > 0}

    def _fit_format(self):
        """How much worse everybody serves in a best-of-five match.

        Both players hold less often at a slam than the same pair would in a
        best-of-three, and it survives the opponent adjustment: pooled over
        2024-2025 the ATP residual is about -0.010 of serve points won in
        best-of-five against best-of-three, consistently signed on hard, clay
        and grass. Left out, the model gives slam matches more holds than they
        get and so predicts them long -- best-of-five carried a +1.6 games bias
        against +0.5 for best-of-three, which is the whole of the gap.

        Fitted as a deviation from the overall residual so it re-weights the
        formats against each other without moving the level.
        """
        num, den = defaultdict(float), defaultdict(float)
        for o in self.obs:
            w = _weight(o["date"], self.asof, self.halflife) * o["svpt"]
            base = self.surf_spw.get(o["surface"], self.lg_spw)
            resid = o["spw"] - base - self.serve[o["pid"]] + self.ret[o["oid"]]
            num[o["best_of"]] += w * resid
            den[o["best_of"]] += w
        tot_n, tot_d = sum(num.values()), sum(den.values())
        mean = tot_n / tot_d if tot_d else 0.0
        self.bo_shift = {
            b: (num[b] / den[b] - mean) * den[b] / (den[b] + K_FORMAT)
            for b in den if den[b] > 0}

    def _fit_surface(self):
        """Per-surface deltas on top of the all-surface rating, shrunk toward
        zero so a player with four grass matches is not called a grass expert."""
        num, den = defaultdict(float), defaultdict(float)
        rnum, rden = defaultdict(float), defaultdict(float)
        for o in self.obs:
            w = _weight(o["date"], self.asof, self.halflife) * o["svpt"]
            base = self._base(o["surface"], o["best_of"])
            resid = o["spw"] - base - self.serve[o["pid"]] + self.ret[o["oid"]]
            num[(o["pid"], o["surface"])] += w * resid
            den[(o["pid"], o["surface"])] += w
            rnum[(o["oid"], o["surface"])] += w * (-resid)
            rden[(o["oid"], o["surface"])] += w
        self.serve_surf = {k: num[k] / (den[k] + self.k_surface) for k in num}
        self.ret_surf = {k: rnum[k] / (rden[k] + self.k_surface) for k in rnum}

    def _fit_counts(self, passes=4):
        """Ace and double-fault rates, opponent-adjusted the way serve and
        return are, and expressed as a proportion of the surface's own rate.

        Two things used to be wrong here. The server term and the returner
        term were both regressed on the same raw residual in a single pass,
        neither told about the other, so a returner who happened to face big
        servers was credited with allowing aces that were really the servers'
        doing -- and then ace_rate added the two back together, counting the
        server twice. And there was no surface anywhere, though it is the
        largest single influence on how many aces get hit.

        The player effect is multiplicative rather than additive because that
        is the shape the data has. Among ATP players with a real sample on
        both surfaces, the spread of ace rates on clay is 0.78 of the spread
        on hard while the mean is 0.67 of it -- far closer to scaling with the
        surface than to surviving it unchanged. So the fit runs on the
        relative residual, (rate - base) / base, and a player carries a
        percentage rather than a count from one surface to the next.
        """
        ace, allow = defaultdict(float), defaultdict(float)
        for _ in range(passes):
            a_num, a_den = defaultdict(float), defaultdict(float)
            r_num, r_den = defaultdict(float), defaultdict(float)
            for o in self.obs:
                w = _weight(o["date"], self.asof, self.halflife) * o["svpt"]
                if w <= 0:
                    continue
                base = self.surf_ace.get(o["surface"], self.lg_ace)
                if base <= 0:
                    continue
                rel = (o["ace"] - base) / base
                a_num[o["pid"]] += w * (rel - allow[o["oid"]])
                a_den[o["pid"]] += w
                r_num[o["oid"]] += w * (rel - ace[o["pid"]])
                r_den[o["oid"]] += w
            ace = defaultdict(float, {
                p: a_num[p] / (a_den[p] + self.k_ace) for p in a_num})
            allow = defaultdict(float, {
                p: r_num[p] / (r_den[p] + self.k_ace) for p in r_num})
        self.ace, self.ace_allowed = dict(ace), dict(allow)

        # Double faults are the server's alone -- the returner cannot cause
        # one -- so there is nothing to iterate against, only a surface to
        # divide out.
        d_num, d_den = defaultdict(float), defaultdict(float)
        for o in self.obs:
            w = _weight(o["date"], self.asof, self.halflife) * o["svpt"]
            base = self.surf_df.get(o["surface"], self.lg_df)
            if base <= 0:
                continue
            d_num[o["pid"]] += w * (o["df"] - base) / base
            d_den[o["pid"]] += w
        self.df = {p: d_num[p] / (d_den[p] + self.k_df) for p in d_num}

    # -- querying ---------------------------------------------------------

    def _base(self, surface, best_of=3):
        """The serve percentage an average player would post here: the
        surface's own level, plus what the format does to everybody on it."""
        return (self.surf_spw.get(surface, self.lg_spw)
                + self.bo_shift.get(best_of or 3, 0.0))

    def serve_rating(self, pid, surface):
        return self.serve.get(pid, 0.0) + self.serve_surf.get((pid, surface), 0.0)

    def return_rating(self, pid, surface):
        return self.ret.get(pid, 0.0) + self.ret_surf.get((pid, surface), 0.0)

    def matchup(self, pid_a, pid_b, surface, best_of=3):
        """P(A wins a point on serve), P(B wins a point on serve).

        The fitted model is  spw = surface_base + serve_i - return_j, so the
        returner's rating is SUBTRACTED. Both ratings are signed so that
        positive is good at their own job -- getting this backwards makes elite
        returners inflate their opponent's serve and the model predicts noise.

        Additive on the percentage scale rather than multiplicative: serve
        points won is already a probability bounded well away from 0 and 1, and
        the additive form is what the opponent adjustment above was fitted on.

        `best_of` is not decoration: everybody serves about a point worse in a
        best-of-five, and a model that does not know which format it is looking
        at gives slam matches more holds than they get.
        """
        base = self._base(surface, best_of)
        pa = base + self.serve_rating(pid_a, surface) - self.return_rating(pid_b, surface)
        pb = base + self.serve_rating(pid_b, surface) - self.return_rating(pid_a, surface)
        mid = (pa + pb) / 2.0 + self.level_shift
        half = (pa - pb) / 2.0 * self.gap_mult
        return _clip(mid + half), _clip(mid - half)

    def ace_rate(self, pid, opp, surface=None, cond_shift=0.0):
        """Aces per serve point. The player and the returner move the surface's
        own rate by a percentage each; conditions move it by an absolute amount,
        because that is the unit the density slope was measured in."""
        base = self.surf_ace.get(surface, self.lg_ace)
        r = base * (1.0 + self.ace.get(pid, 0.0)
                    + self.ace_allowed.get(opp, 0.0))
        return max(0.005, min(0.45, r + cond_shift))

    def df_rate(self, pid, surface=None):
        base = self.surf_df.get(surface, self.lg_df)
        return max(0.005, min(0.25, base * (1.0 + self.df.get(pid, 0.0))))

    def seen(self, pid):
        return self.points.get(pid, 0.0)


def _clip(p):
    return max(0.30, min(0.85, p))


def load(tour, start_year, end_year):
    return observations(fetch.archive_seasons(tour, start_year, end_year))
