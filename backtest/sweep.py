"""SpreadGuard Parameter Sweep & Comparative Strategy Benchmark.

Five configurations on identical synthetic market paths (matched seeds):
1. NAIVE baseline: classic MM quoting, mild skew only, no flow defense
2. PULL: cancel the fenced side on toxic flow (previous design)
3. STEP_BACK: push the fenced side out 4 bps, stay in the market (new)
4. STEP_BACK + microprice fair value
5. Full suite: STEP_BACK + microprice + inventory recycling

Also runs a maker-fee sensitivity pass on the full suite.
"""

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from spreadguard.config import SpreadGuardConfig, QuoteMode
from backtest.simulator import run_backtest

SEEDS = [42, 101, 888, 777, 555, 314, 271, 999]
TICKS = 2400  # 10 minutes per session


def bench(name, cfg, maker_fee_bps=0.0):
    rs = [run_backtest(cfg, num_ticks=TICKS, seed=s, maker_fee_bps=maker_fee_bps) for s in SEEDS]
    n = len(rs)
    vol = sum(r.total_volume_usdt for r in rs) / n
    pnl = sum(r.net_pnl_usdt for r in rs) / n
    fills = sum(r.fill_count for r in rs) / n
    dd = max(r.max_drawdown_usdt for r in rs)
    st = {}
    for r in rs:
        for k, v in r.states_fired.items():
            st[k] = st.get(k, 0) + v
    total = sum(st.values()) or 1
    occ = {k: round(100.0 * v / total, 1) for k, v in sorted(st.items())}
    return vol, pnl, fills, dd, occ


def main():
    print("=" * 96)
    print("SPREADGUARD COMPARATIVE BENCHMARK")
    print(f"{len(SEEDS)} matched seeds x {TICKS} ticks (10 min each), zero maker fees, $800 bankroll")
    print("=" * 96)

    configs = [
        ("1. Naive baseline (no guard)", SpreadGuardConfig(mode=QuoteMode.NAIVE, use_microprice=False, recycle=False)),
        ("2. Binary PULL (prev design)", SpreadGuardConfig(mode=QuoteMode.PULL, use_microprice=False, recycle=False)),
        ("3. STEP_BACK (new)", SpreadGuardConfig(mode=QuoteMode.STEP_BACK, use_microprice=False, recycle=False)),
        ("4. STEP_BACK + microprice", SpreadGuardConfig(mode=QuoteMode.STEP_BACK, use_microprice=True, recycle=False)),
        ("5. Full suite (+recycle)", SpreadGuardConfig(mode=QuoteMode.STEP_BACK, use_microprice=True, recycle=True)),
    ]

    print(f"\n{'Configuration':<30} {'Volume':>12} {'Net PnL':>10} {'Fills':>7} {'MaxDD':>8}  State occupancy")
    print("-" * 96)
    results = {}
    for name, cfg in configs:
        vol, pnl, fills, dd, occ = bench(name, cfg)
        results[name] = (vol, pnl, fills, dd, occ)
        print(f"{name:<30} {vol:>10,.0f} {pnl:>+10.2f} {fills:>7.0f} {dd:>8.2f}  {occ}")

    # deltas vs naive
    nv = results[configs[0][0]]
    print("-" * 96)
    for name, _ in configs[1:]:
        vol, pnl, fills, dd, occ = results[name]
        dvol = 100.0 * (vol - nv[0]) / nv[0]
        dpnl = pnl - nv[1]
        ddd = 100.0 * (dd - nv[3]) / nv[3]
        print(f"  vs naive: {name:<30} volume {dvol:+6.1f}%   PnL {dpnl:+8.2f}   MaxDD {ddd:+6.1f}%")

    # fire-rate statistic (mode-independent: identical market paths)
    r = run_backtest(configs[4][1], num_ticks=TICKS, seed=42)
    print(f"\nOFI gate breaches: {r.ofi_gate_fires} per {TICKS}-tick session "
          f"({r.gate_fire_rate_per_hr:.0f}/hr). Hysteresis converts them into "
          f"{sum(v for k, v in r.states_fired.items() if k != 'QUOTE')} defensive ticks.")

    # fee sensitivity on the full suite
    print("\nMaker-fee sensitivity (full suite):")
    print(f"{'fee scenario':<28} {'Volume':>12} {'Net PnL':>10}")
    print("-" * 56)
    for label, fee in [
        ("maker rebate -1.0 bps", -1.0),
        ("maker fee 0.0 bps (promo)", 0.0),
        ("maker fee +1.0 bps", 1.0),
        ("maker fee +2.0 bps (VIP0)", 2.0),
    ]:
        vol, pnl, fills, dd, occ = bench("full", configs[4][1], maker_fee_bps=fee)
        print(f"{label:<28} {vol:>10,.0f} {pnl:>+10.2f}")

    print("\nBenchmark complete.")


if __name__ == "__main__":
    main()
