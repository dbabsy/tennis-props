"""How much day-to-day form variation to integrate over."""
import sys
import backtest as B
tour = sys.argv[1] if len(sys.argv) > 1 else "atp"
print(f"{'sigma':>7}{'nodes':>6} | {'24 LL':>7}{'24 MAE':>8}{'24 bias':>9} | "
      f"{'25 LL':>7}{'25 MAE':>8}{'25 bias':>9}", flush=True)
for sig in (0.0, 0.02, 0.035, 0.05, 0.065, 0.08):
    n = 1 if sig == 0 else 3
    a = B.run(tour, 2024, quiet=True, sigma=sig, nodes=n)
    b = B.run(tour, 2025, quiet=True, sigma=sig, nodes=n)
    print(f"{sig:>7.3f}{n:>6} | {a['logloss']:>7.4f}{a['games_mae']:>8.2f}{a['games_bias']:>+9.2f} | "
          f"{b['logloss']:>7.4f}{b['games_mae']:>8.2f}{b['games_bias']:>+9.2f}", flush=True)
