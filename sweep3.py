"""Two measurements the props layer needs:

1. level_shift alone. sweep2 showed the level knob moves total games while
   leaving match log loss nearly flat, which is the trade the games bias needs
   -- gap_mult is swept there.
2. the overdispersion of ace and double-fault counts against a binomial, which
   decides how wide a count distribution should be. This used to be a hand
   rolled walk-forward loop; backtest.collect does the same walk now and
   reports the z-score spread, so the numbers here and the numbers on the
   accuracy page come from one implementation rather than two.
"""
import sys
import backtest as B

tour = sys.argv[1] if len(sys.argv) > 1 else "atp"

print("=== level_shift sweep (gap_mult at its fitted value) ===", flush=True)
print(f"{'lvl':>7} | {'24 LL':>7}{'24 MAE':>8}{'24 bias':>9} | "
      f"{'25 LL':>7}{'25 MAE':>8}{'25 bias':>9}", flush=True)
for lv in (0.0, -0.010, -0.020, -0.025, -0.030, -0.035, -0.040, -0.050):
    a = B.run(tour, 2024, quiet=True, sigma=0.05, level_shift=lv)
    b = B.run(tour, 2025, quiet=True, sigma=0.05, level_shift=lv)
    print(f"{lv:>+7.3f} | {a['logloss']:>7.4f}{a['games_mae']:>8.2f}{a['games_bias']:>+9.2f} | "
          f"{b['logloss']:>7.4f}{b['games_mae']:>8.2f}{b['games_bias']:>+9.2f}", flush=True)

print("\n=== ace / double-fault overdispersion vs binomial ===", flush=True)
print("(the z-score spread against the fitted rate; 1.00 would be binomial)",
      flush=True)
for tr in ("atp", "wta"):
    for yr in (2024, 2025):
        r = B.summarize(B.collect(tr, yr, sigma=0.05), tr, yr, quiet=True)
        print(f"  {tr.upper()} {yr}: n={r['n_props']}  ace {r['ace_disp']:.3f}"
              f"  df {r['df_disp']:.3f}", flush=True)
