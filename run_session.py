"""Run one simulated SpreadGuard session against the real controller,
writing journal.jsonl and agent_state.json for the control room.

Usage: py run_session.py [--ticks 2400] [--mode stepback]
"""

import sys, os, argparse, time

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from spreadguard.config import SpreadGuardConfig, QuoteMode
from backtest.simulator import MarketSimulator
from spreadguard.controller import SpreadGuardController
from spreadguard.market import Fill, Trade


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", type=int, default=2400)
    ap.add_argument("--mode", type=str, default="stepback",
                    choices=["naive", "pull", "stepback"])
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")
    os.makedirs(out_dir, exist_ok=True)

    cfg = SpreadGuardConfig(mode=QuoteMode(args.mode))
    issues = cfg.validate()
    if issues:
        for i in issues:
            print("CONFIG:", i)
        sys.exit(1)

    sim = MarketSimulator(seed=args.seed)
    controller = SpreadGuardController(cfg, journal_dir=out_dir)
    controller.position.cash_usdt = 800.0

    resting = []
    remaining_by_key = {}

    start_wall = time.time()
    for tick in range(args.ticks):
        book, trades = sim.step()

        for t in trades:
            remaining = t.size
            if t.side == "buy":
                band = t.price * sim.sweep_depth_bps("buy") / 1e4
                cand = sorted([o for o in resting if o.side == "ask" and o.price <= t.price + band], key=lambda o: o.price)
            else:
                band = t.price * sim.sweep_depth_bps("sell") / 1e4
                cand = sorted([o for o in resting if o.side == "bid" and o.price >= t.price - band], key=lambda o: -o.price)
            for o in cand:
                if remaining <= 1e-8:
                    break
                live = remaining_by_key.get((o.side, o.level))
                if not live or live["size"] <= 1e-6:
                    continue
                fs = min(live["size"], remaining) * sim.queue_share(t.side)
                if fs < 1e-5:
                    continue
                if o.side == "bid" and controller.position.net_btc + fs > cfg.max_abs_position_btc:
                    continue
                if o.side == "ask" and controller.position.net_btc - fs < -cfg.max_abs_position_btc:
                    continue
                controller.on_fill(Fill(ts_ms=book.ts_ms, side=o.side, price=o.price,
                                        size=fs, fee_usdt=0.0, is_maker=True, mid_at_fill=book.mid))
                live["size"] -= fs
                remaining -= fs

        for t in trades:
            controller.on_trade(t)
        orders = controller.on_tick(book)
        remaining_by_key = {(o.side, o.level): {"size": o.size} for o in orders}
        resting = orders

    wall = time.time() - start_wall
    pnl = controller.position.mark_to_market(sim.mid) - 800.0

    print(f"Session complete: {args.ticks} ticks in {wall:.2f}s wall time")
    print(f"  mode         {cfg.mode.value}")
    print(f"  fills        {controller.fill_count}")
    print(f"  volume       ${controller.total_volume_usdt:,.2f}")
    print(f"  net PnL      ${pnl:+.2f}")
    print(f"  final state  {controller.last_decision.state.value}")
    print(f"  halted       {controller.is_halted} {controller.halt_reason}")
    print(f"  journal      {os.path.join(out_dir, 'journal.jsonl')}")
    print(f"  state        {os.path.join(out_dir, 'agent_state.json')}")


if __name__ == "__main__":
    main()
