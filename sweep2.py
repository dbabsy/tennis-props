"""Tune the level/gap calibration. Tuned on 2024, confirmed on 2025."""
import itertools, sys
import backtest as B

tour = sys.argv[1] if len(sys.argv) > 1 else "atp"
print(f"{'lvl':>7}{'gap':>6} | {'24 LL':>7}{'24 MAE':>8}{'24 bias':>9} | "
      f"{'25 LL':>7}{'25 MAE':>8}{'25 bias':>9}")
for lv, gm in itertools.product((0.0, -0.005, -0.010, -0.015), (1.0, 1.2, 1.4, 1.6)):
    a = B.run(tour, 2024, quiet=True, level_shift=lv, gap_mult=gm)
    b = B.run(tour, 2025, quiet=True, level_shift=lv, gap_mult=gm)
    print(f"{lv:>+7.3f}{gm:>6.1f} | {a['logloss']:>7.4f}{a['games_mae']:>8.2f}"
          f"{a['games_bias']:>+9.2f} | {b['logloss']:>7.4f}{b['games_mae']:>8.2f}"
          f"{b['games_bias']:>+9.2f}", flush=True)
