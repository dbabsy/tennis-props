"""Tune the level/gap calibration. Tuned on 2024, confirmed on 2023 and 2025.

The gap grid starts at 1.0 and steps finely, which the first version of this
did not -- it began at 1.2 and so only ever saw the far side of the optimum,
and gap_mult was left at 1.0 on the strength of that. The sweep also runs with
the form integration on, because that is the configuration that ships; with
sigma at 0 the gap knob is partly doing the form knob's job and the answer
comes out somewhere else.

Read the games columns alongside the log loss. A gap that buys a game at the
cost of the match odds is not worth having, and the point of a joint grid is
to see which of those is happening.
"""
import itertools, sys
import backtest as B
import project as P

tour = sys.argv[1] if len(sys.argv) > 1 else "atp"
YEARS = (2023, 2024, 2025)
print(f"{'lvl':>7}{'gap':>6} | " + " | ".join(
    f"{y%100:>2d} LL   {y%100:>2d} MAE {y%100:>2d} bias" for y in YEARS))
for lv, gm in itertools.product((0.0, -0.005, -0.010, -0.015),
                                (1.0, 1.05, 1.10, 1.15, 1.20, 1.30)):
    cells = []
    for y in YEARS:
        r = B.run(tour, y, quiet=True, sigma=P.FORM_SIGMA, nodes=P.FORM_NODES,
                  level_shift=lv, gap_mult=gm)
        cells.append(f"{r['logloss']:>6.4f}{r['games_mae']:>8.2f}"
                     f"{r['games_bias']:>+9.2f}")
    print(f"{lv:>+7.3f}{gm:>6.2f} | " + " | ".join(cells), flush=True)
