"""Aggregate real-tape replay across multiple windows and fee levels.

Runs the five configurations on every tape in a list, at several maker fees,
and reports per-window and pooled results plus a win count. This is the honest
way to check whether a configuration's edge survives out of sample.

    py backtest/multiwindow.py --tapes tape/real_a.jsonl tape/real_b.jsonl
"""

import sys, os, argparse, itertools

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from spreadguard.config import SpreadGuardConfig, QuoteMode
from backtest.replay import load_tape, run_replay


CONFIGS = [
    ("Naive", SpreadGuardConfig(mode=QuoteMode.NAIVE, use_microprice=False, recycle=False)),
    ("Pull", SpreadGuardConfig(mode=QuoteMode.PULL, use_microprice=False, recycle=False)),
    ("StepBack", SpreadGuardConfig(mode=QuoteMode.STEP_BACK, use_microprice=False, recycle=False)),
    ("StepBack+Micro", SpreadGuardConfig(mode=QuoteMode.STEP_BACK, use_microprice=True, recycle=False)),
    ("FullSuite", SpreadGuardConfig(mode=QuoteMode.STEP_BACK, use_microprice=True, recycle=True)),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tapes", nargs="+", required=True)
    ap.add_argument("--fees", nargs="+", type=float, default=[-1.0, 0.0, 2.0])
    args = ap.parse_args()

    loaded = []
    for t in args.tapes:
        ticks = load_tape(t)
        span = (ticks[-1][0].ts_ms - ticks[0][0].ts_ms) / 1000.0 if ticks else 0
        loaded.append((os.path.basename(t), ticks, span))
        print(f"loaded {os.path.basename(t)}: {len(ticks)} ticks, {span/3600:.2f}h")

    for fee in args.fees:
        print("\n" + "=" * 84)
        print(f"MAKER FEE {fee:+.2f} bps")
        print("=" * 84)
        header = f"{'Configuration':<16}" + "".join(f"{n[:12]:>14}" for n, _, _ in loaded) + f"{'POOLED':>12}{'WINS':>7}"
        print(header)
        print("-" * len(header))

        pooled = {name: 0.0 for name, _ in CONFIGS}
        pooled_vol = {name: 0.0 for name, _ in CONFIGS}
        wins = {name: 0 for name, _ in CONFIGS}

        per_window = []
        for name, cfg in CONFIGS:
            col = []
            for _, ticks, _ in loaded:
                r = run_replay(cfg, ticks, maker_fee_bps=fee)
                col.append(r.net_pnl_usdt)
                pooled[name] += r.net_pnl_usdt
                pooled_vol[name] += r.total_volume_usdt
            per_window.append((name, col))

        # winner per window
        for wi in range(len(loaded)):
            best = max(per_window, key=lambda nv: nv[1][wi])
            wins[best[0]] += 1

        for name, col in per_window:
            line = f"{name:<16}" + "".join(f"{v:>+14.2f}" for v in col) + f"{pooled[name]:>+12.2f}{wins[name]:>7}"
            print(line)

        print("-" * len(header))
        vol_line = f"{'Volume':<16}" + "".join(f"{'':>14}" for _ in loaded) + f"{'':>12}{'':>7}"
        for name, _ in CONFIGS:
            print(f"  {name:<14} pooled volume ${pooled_vol[name]:,.0f}")


if __name__ == "__main__":
    main()
