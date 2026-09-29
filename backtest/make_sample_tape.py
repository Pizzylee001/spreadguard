"""Emit a schema-exact sample tape from the synthetic simulator.

Purpose: prove the recorder schema (record_bitget.mjs) and the replay loader
(replay.py) agree end to end, so that a real Bitget recording drops straight in.

    py backtest/make_sample_tape.py --out tape/sample.jsonl --seconds 90
"""

import sys, os, json, argparse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from backtest.simulator import MarketSimulator


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="tape/sample.jsonl")
    ap.add_argument("--seconds", type=int, default=90)
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    sim = MarketSimulator(initial_mid=67200.0, seed=args.seed)
    ticks = int(args.seconds * 1000 / 250)

    books = 0
    trades = 0
    with open(args.out, "w", encoding="utf-8") as f:
        for _ in range(ticks):
            book, trs = sim.step()
            for t in trs:
                f.write(json.dumps({
                    "kind": "trade", "ts": t.ts_ms,
                    "price": round(t.price, 2), "size": round(t.size, 4), "side": t.side,
                }) + "\n")
                trades += 1
            f.write(json.dumps({
                "kind": "book", "ts": book.ts_ms,
                "bids": [[round(l.price, 2), round(l.size, 4)] for l in book.bids],
                "asks": [[round(l.price, 2), round(l.size, 4)] for l in book.asks],
            }) + "\n")
            books += 1

    print(f"Wrote {books} book ticks, {trades} trades, {args.seconds}s -> {args.out}")


if __name__ == "__main__":
    main()
