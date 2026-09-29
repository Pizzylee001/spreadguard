"""Replay a recorded tape through the SpreadGuard controller.

Reads the recorder schema written by `record_bitget.mjs`:

    {"kind":"book","ts":<ms>,"bids":[[price,size],...],"asks":[[price,size],...]}
    {"kind":"trade","ts":<ms>,"price":<p>,"size":<s>,"side":"buy"|"sell"}

Fill model uses the REAL recorded book depth: a taker of size S sweeps the
book until S is exhausted, and our order fills only if the sweep reaches it.
Our share at that level is our size over the recorded level size, which is the
honest queue assumption (we sit behind the resting size at the price).

Commands:
  py backtest/replay.py --tape tape/btcusdt_perp.jsonl           # 5-config A/B
  py backtest/replay.py --tape tape.jsonl --journal state/       # one run, writes state
"""

import sys, os, json, argparse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from spreadguard.config import SpreadGuardConfig, QuoteMode
from spreadguard.market import BookSnapshot, Level, Trade, Fill
from spreadguard.controller import SpreadGuardController
from backtest.simulator import BacktestResult


def load_tape(path):
    """Group the tape into ticks: each book snapshot plus the trades that
    arrived at or before it."""
    ticks = []
    pending_trades = []
    last_book = None
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("kind") == "trade":
                pending_trades.append(Trade(
                    ts_ms=int(rec["ts"]), price=float(rec["price"]),
                    size=float(rec["size"]), side=str(rec["side"]),
                ))
            elif rec.get("kind") == "book":
                bids = [Level(float(p), float(s)) for p, s in rec["bids"]]
                asks = [Level(float(p), float(s)) for p, s in rec["asks"]]
                if not bids or not asks:
                    continue
                if last_book is not None:
                    ticks.append((last_book, pending_trades))
                last_book = BookSnapshot(ts_ms=int(rec["ts"]), bids=bids, asks=asks)
                pending_trades = []
    if last_book is not None:
        ticks.append((last_book, pending_trades))
    return ticks


def sweep_reach(book, side, trade_size):
    """Furthest price a taker of this size reaches, walking the real book."""
    levels = book.bids if side == "sell" else book.asks
    remaining = trade_size
    reach = levels[0].price
    for lv in levels:
        reach = lv.price
        remaining -= lv.size
        if remaining <= 0:
            break
    return reach


def level_size_at(book, side, price):
    """Recorded resting size at the level our order would sit at."""
    levels = book.bids if side == "bid" else book.asks
    best = None
    for lv in levels:
        if side == "bid":
            if lv.price >= price:
                if best is None or lv.price < best.price:
                    best = lv
            elif best is None:
                best = lv
        else:
            if lv.price <= price:
                if best is None or lv.price > best.price:
                    best = lv
            elif best is None:
                best = lv
    return best.size if best else 0.05


def run_replay(config, ticks, maker_fee_bps=0.0, starting_cash=800.0, seed_label=0, journal_dir=None):
    controller = SpreadGuardController(config, journal_dir=journal_dir)
    controller.position.cash_usdt = starting_cash

    fee_mult = maker_fee_bps / 1e4
    states_count = {}
    gate_fires = 0
    pnl_curve = []
    resting = []
    remaining_by_key = {}

    for book, trades in ticks:
        for t in trades:
            remaining = t.size
            if t.side == "buy":
                reach = sweep_reach(book, "buy", t.size)
                candidates = [o for o in resting if o.side == "ask" and o.price <= reach]
                candidates.sort(key=lambda o: o.price)
            else:
                reach = sweep_reach(book, "sell", t.size)
                candidates = [o for o in resting if o.side == "bid" and o.price >= reach]
                candidates.sort(key=lambda o: -o.price)

            for o in candidates:
                if remaining <= 1e-8:
                    break
                live = remaining_by_key.get((o.side, o.level))
                if live is None or live["size"] <= 1e-6:
                    continue
                lvl_sz = level_size_at(book, o.side, o.price)
                queue_share = live["size"] / (live["size"] + lvl_sz) if lvl_sz > 0 else 0.5
                fill_sz = min(live["size"], remaining) * queue_share
                if fill_sz < 1e-5:
                    continue
                if o.side == "bid" and controller.position.net_btc + fill_sz > config.max_abs_position_btc:
                    continue
                if o.side == "ask" and controller.position.net_btc - fill_sz < -config.max_abs_position_btc:
                    continue
                controller.on_fill(Fill(
                    ts_ms=book.ts_ms, side=o.side, price=o.price,
                    size=fill_sz, fee_usdt=o.price * fill_sz * fee_mult,
                    is_maker=True, mid_at_fill=book.mid,
                ))
                live["size"] -= fill_sz
                remaining -= fill_sz

        for t in trades:
            controller.on_trade(t)
        orders = controller.on_tick(book)
        remaining_by_key = {(o.side, o.level): {"size": o.size} for o in orders}
        resting = orders

        dec = controller.last_decision
        if dec:
            states_count[dec.state.value] = states_count.get(dec.state.value, 0) + 1
            if abs(dec.ofi_z) >= config.step_enter_z:
                gate_fires += 1

        pnl_curve.append(controller.position.mark_to_market(book.mid) - starting_cash)

    n = len(ticks)
    duration_hours = n * config.tick_ms / 1000.0 / 3600.0
    peak, max_dd = -1e18, 0.0
    for pnl in pnl_curve:
        peak = max(peak, pnl)
        max_dd = max(max_dd, peak - pnl)

    return BacktestResult(
        config_name=config.mode.value,
        seed=seed_label,
        duration_hours=duration_hours,
        total_volume_usdt=controller.total_volume_usdt,
        net_pnl_usdt=pnl_curve[-1] if pnl_curve else 0.0,
        fees_paid_usdt=controller.position.fees_paid_usdt,
        fill_count=controller.fill_count,
        max_drawdown_usdt=max_dd,
        ofi_gate_fires=gate_fires,
        gate_fire_rate_per_hr=gate_fires / duration_hours if duration_hours else 0.0,
        states_fired=states_count,
    )


def comparison(tape_path, maker_fee_bps=0.0):
    ticks = load_tape(tape_path)
    if len(ticks) < 10:
        print(f"Tape too short: {len(ticks)} ticks.")
        return
    span = (ticks[-1][0].ts_ms - ticks[0][0].ts_ms) / 1000.0
    print("=" * 92)
    print(f"REPLAY: {os.path.basename(tape_path)}  {len(ticks)} book ticks, {span:.0f}s span")
    print("=" * 92)

    configs = [
        ("1. Naive baseline", SpreadGuardConfig(mode=QuoteMode.NAIVE, use_microprice=False, recycle=False)),
        ("2. Binary PULL", SpreadGuardConfig(mode=QuoteMode.PULL, use_microprice=False, recycle=False)),
        ("3. STEP_BACK", SpreadGuardConfig(mode=QuoteMode.STEP_BACK, use_microprice=False, recycle=False)),
        ("4. STEP_BACK + microprice", SpreadGuardConfig(mode=QuoteMode.STEP_BACK, use_microprice=True, recycle=False)),
        ("5. Full suite (+recycle)", SpreadGuardConfig(mode=QuoteMode.STEP_BACK, use_microprice=True, recycle=True)),
    ]
    print(f"\n{'Configuration':<28} {'Volume':>12} {'Net PnL':>10} {'Fills':>7} {'MaxDD':>8}  States")
    print("-" * 92)
    naive = None
    for name, cfg in configs:
        r = run_replay(cfg, ticks, maker_fee_bps=maker_fee_bps)
        if naive is None:
            naive = r
        print(f"{name:<28} {r.total_volume_usdt:>10,.0f} {r.net_pnl_usdt:>+10.2f} "
              f"{r.fill_count:>7.0f} {r.max_drawdown_usdt:>8.2f}  {r.states_fired}")
    print("-" * 92)
    print(f"Maker fee assumption: {maker_fee_bps:+.2f} bps\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", required=True)
    ap.add_argument("--fee-bps", type=float, default=0.0)
    ap.add_argument("--journal", default=None, help="directory to write state + journal for one run")
    ap.add_argument("--mode", default="stepback", choices=["naive", "pull", "stepback"])
    args = ap.parse_args()

    if args.journal:
        cfg = SpreadGuardConfig(mode=QuoteMode(args.mode))
        ticks = load_tape(args.tape)
        run_replay(cfg, ticks, maker_fee_bps=args.fee_bps, journal_dir=args.journal)
        print(f"Wrote journal to {args.journal}")
    else:
        comparison(args.tape, maker_fee_bps=args.fee_bps)


if __name__ == "__main__":
    main()
