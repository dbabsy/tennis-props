"""Tune the shrinkage constants out of sample. Tuned on one season, confirmed
on another -- a constant that only helps the season it was chosen on is noise."""
import itertools, sys
import backtest as B

def one(tour, year, **kw):
    r = B.run(tour, year, quiet=True, **kw)
    return r

if __name__ == "__main__":
    tour = sys.argv[1] if len(sys.argv) > 1 else "atp"
    tune, hold = 2024, 2025
    print(f"{'k_serve':>8}{'k_ret':>7}{'k_surf':>8}{'half':>6} | "
          f"{'tune LL':>8}{'tune MAE':>9}{'tune bias':>10} | "
          f"{'hold LL':>8}{'hold MAE':>9}{'hold bias':>10}")
    grid = itertools.product((150, 300, 600, 1000), (300, 600, 1200), (400, 900))
    best = None
    for ks, kr, ksf in grid:
        a = one(tour, tune, k_serve=ks, k_return=kr, k_surface=ksf)
        b = one(tour, hold, k_serve=ks, k_return=kr, k_surface=ksf)
        print(f"{ks:>8}{kr:>7}{ksf:>8}{400:>6} | "
              f"{a['logloss']:>8.4f}{a['games_mae']:>9.2f}{a['games_bias']:>+10.2f} | "
              f"{b['logloss']:>8.4f}{b['games_mae']:>9.2f}{b['games_bias']:>+10.2f}")
        score = a['logloss'] + b['logloss']
        if best is None or score < best[0]:
            best = (score, ks, kr, ksf)
    print("\nbest by summed log loss:", best)
