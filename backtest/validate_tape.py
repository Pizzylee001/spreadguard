"""Validate a recorded tape before trusting a replay run.

Checks: schema, monotonic timestamps, sane prices, both sides of the book,
trade side values, and basic coverage. Exits non-zero on a hard failure.

    py backtest/validate_tape.py --tape tape/btcusdt_perp.jsonl
"""

import sys, os, json, argparse


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", required=True)
    args = ap.parse_args()

    if not os.path.exists(args.tape):
        print(f"FAIL: file not found: {args.tape}")
        return 1

    books = trades = bad = 0
    last_book_ts = -1
    last_trade_ts = -1
    first_ts = None
    last_ts = None
    price_lo = float("inf")
    price_hi = 0.0

    with open(args.tape, "r", encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                print(f"  line {ln}: not JSON")
                bad += 1
                continue
            kind = rec.get("kind")
            ts = rec.get("ts")
            if not isinstance(ts, (int, float)):
                print(f"  line {ln}: bad ts")
                bad += 1
                continue
            first_ts = ts if first_ts is None else first_ts
            last_ts = ts

            if kind == "book":
                b, a = rec.get("bids"), rec.get("asks")
                if not b or not a:
                    print(f"  line {ln}: book missing a side")
                    bad += 1
                    continue
                if ts < last_book_ts:
                    print(f"  line {ln}: book ts went backwards")
                    bad += 1
                last_book_ts = ts
                for p, s in b + a:
                    price_lo = min(price_lo, p)
                    price_hi = max(price_hi, p)
                books += 1
            elif kind == "trade":
                side = rec.get("side")
                if side not in ("buy", "sell"):
                    print(f"  line {ln}: trade side '{side}'")
                    bad += 1
                if ts < last_trade_ts:
                    print(f"  line {ln}: trade ts went backwards")
                    bad += 1
                last_trade_ts = ts
                price_lo = min(price_lo, rec.get("price", price_lo))
                price_hi = max(price_hi, rec.get("price", price_hi))
                trades += 1
            else:
                print(f"  line {ln}: unknown kind '{kind}'")
                bad += 1

    span = (last_ts - first_ts) / 1000.0 if first_ts is not None and last_ts is not None else 0
    print(f"books={books} trades={trades} span={span:.0f}s price[{price_lo:.1f}..{price_hi:.1f}] bad={bad}")

    ok = True
    if bad:
        print("FAIL: malformed records present")
        ok = False
    if books < 20:
        print("FAIL: fewer than 20 book ticks, tape too short to test a strategy")
        ok = False
    if trades < 5:
        print("WARN: fewer than 5 trades; the guard has little flow to react to")
    if not (1000 < price_lo and price_hi < 1_000_000):
        print("FAIL: prices look wrong for BTC")
        ok = False

    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
