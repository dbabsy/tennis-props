"""Two measurements the props layer needs:

1. level_shift alone. sweep2 showed gap_mult trades match odds away to buy
   totals accuracy, so the gap is left at 1.0 and the symmetric level knob does
   the work -- it moves total games while leaving log loss flat.
2. the overdispersion of ace counts against a binomial, which decides how wide
   an ace line's distribution should be.
"""
import math, sys
from collections import defaultdict
from datetime import timedelta
import backtest as B, fetch, model, ratings as R

tour = sys.argv[1] if len(sys.argv) > 1 else "atp"

print("=== level_shift sweep (gap_mult fixed at 1.0) ===", flush=True)
print(f"{'lvl':>7} | {'24 LL':>7}{'24 MAE':>8}{'24 bias':>9} | "
      f"{'25 LL':>7}{'25 MAE':>8}{'25 bias':>9}", flush=True)
for lv in (0.0, -0.010, -0.020, -0.025, -0.030, -0.035, -0.040, -0.050):
    a = B.run(tour, 2024, quiet=True, level_shift=lv)
    b = B.run(tour, 2025, quiet=True, level_shift=lv)
    print(f"{lv:>+7.3f} | {a['logloss']:>7.4f}{a['games_mae']:>8.2f}{a['games_bias']:>+9.2f} | "
          f"{b['logloss']:>7.4f}{b['games_mae']:>8.2f}{b['games_bias']:>+9.2f}", flush=True)

print("\n=== ace / double-fault overdispersion vs binomial ===", flush=True)
for tr in ("atp", "wta"):
    obs = R.load(tr, 2021, 2026)
    ms = [m for m in fetch.archive_seasons(tr, 2025, 2025)
          if m["date"] and m.get("w_svpt") and m.get("l_svpt")]
    ms.sort(key=lambda m: m["date"])
    fitted = cur = None
    for field in ("ace", "df"):
        z = []
        fitted = cur = None
        for m in ms:
            wk = m["date"] - timedelta(days=m["date"].weekday())
            if wk != cur:
                cur = wk
                try: fitted = R.Ratings(tr, obs, wk)
                except ValueError: continue
            if fitted is None: continue
            for me, opp in (("w", "l"), ("l", "w")):
                pid = m[f"{'winner' if me=='w' else 'loser'}_id"]
                oid = m[f"{'winner' if opp=='w' else 'loser'}_id"]
                svpt = m[f"{me}_svpt"]; cnt = m.get(f"{me}_{field}")
                if not svpt or svpt < 40 or cnt is None: continue
                if fitted.seen(pid) < 400: continue
                rate = (fitted.ace_rate(pid, oid) if field == "ace"
                        else fitted.df_rate(pid))
                mu = svpt * rate
                sd = math.sqrt(svpt * rate * (1 - rate))
                if sd > 0: z.append((cnt - mu) / sd)
        if len(z) > 100:
            mz = sum(z) / len(z)
            sdz = math.sqrt(sum((x - mz) ** 2 for x in z) / len(z))
            print(f"  {tr.upper()} {field}: n={len(z)}  mean z {mz:+.3f}  "
                  f"sd z {sdz:.3f}  -> dispersion {sdz:.3f}", flush=True)
