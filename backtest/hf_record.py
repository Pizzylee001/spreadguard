"""Build a SpreadGuard tape from real recorded Binance USDT-M futures depth.

Source: Hugging Face dataset `predict-quant/binance-future-orderbook`
(recorded Binance USDT-M perpetual depth20, ~100 ms cadence, 20 levels a side,
Apache-2.0). Real recorded market data, not synthetic.

No trades column exists in the source, so aggressive flow is inferred from
touch-size changes between consecutive snapshots (standard order-flow
inference). This is labeled inferred wherever results are reported.

Resamples to the controller tick (default 250 ms): each output book is the last
snapshot within the tick window, and inferred trades carry their own timestamps.

    py backtest/hf_record.py --pages 60 --out tape/btcusdt_real.jsonl
"""

import sys, os, json, argparse, time
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor


BASE = "https://datasets-server.huggingface.co/rows"
DATASET = "predict-quant/binance-future-orderbook"


def fetch_page(offset, length, retries=6):
    qs = urllib.parse.urlencode({
        "dataset": DATASET, "config": "default", "split": "train",
        "offset": offset, "length": length,
    })
    url = f"{BASE}?{qs}"
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "spreadguard-recorder"})
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
            # 429 needs a longer cool-off
            time.sleep(2.0 + 3.0 * attempt)
    raise RuntimeError(f"page {offset} failed: {last}")


def levels_from(raw):
    arr = json.loads(raw)
    out = []
    for p, s in arr:
        fp, fs = float(p), float(s)
        if fs > 0:
            out.append((fp, fs))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=60, help="pages of 100 rows")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--tick-ms", type=int, default=250)
    ap.add_argument("--out", default="tape/btcusdt_real.jsonl")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--delay", type=float, default=0.4)
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    offsets = [args.start + i * 100 for i in range(args.pages)]
    print(f"Fetching {args.pages} pages ({args.pages * 100} rows) from {DATASET}")

    rows = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for i, page in enumerate(ex.map(fetch_page, offsets, [100] * len(offsets))):
            rows.extend(page["rows"])
            if args.delay:
                time.sleep(args.delay)
            if (i + 1) % 10 == 0:
                print(f"  fetched {(i + 1) * 100} rows")

    rows.sort(key=lambda x: x["row_idx"])

    # Walk snapshots, infer aggressive flow from touch-size drops, resample to tick.
    out_books = 0
    out_trades = 0
    prev_bid_px = prev_bid_sz = prev_ask_px = prev_ask_sz = None
    tick_bucket = None
    pending_book = None

    with open(args.out, "w", encoding="utf-8") as f:
        for rec in rows:
            row = rec["row"]
            ts = int(row["T"])
            bids = levels_from(row["bids"])
            asks = levels_from(row["asks"])
            if not bids or not asks:
                continue
            bids.sort(key=lambda x: -x[0])
            asks.sort(key=lambda x: x[0])
            bb, bs = bids[0]
            ba, as_ = asks[0]

            # inferred taker flow: bid size falling = sellers hitting (sell),
            # ask size falling = buyers lifting (buy). Only at same price level.
            trades = []
            if prev_bid_px is not None:
                if bb == prev_bid_px and bs < prev_bid_sz:
                    trades.append((ts, bb, prev_bid_sz - bs, "sell"))
                if ba == prev_ask_px and as_ < prev_ask_sz:
                    trades.append((ts, ba, prev_ask_sz - as_, "buy"))
            prev_bid_px, prev_bid_sz, prev_ask_px, prev_ask_sz = bb, bs, ba, as_

            bucket = ts // args.tick_ms
            if tick_bucket is None:
                tick_bucket = bucket
            if bucket != tick_bucket:
                if pending_book is not None:
                    f.write(json.dumps(pending_book) + "\n")
                    out_books += 1
                tick_bucket = bucket
            pending_book = {
                "kind": "book", "ts": ts,
                "bids": [[round(p, 2), round(s, 4)] for p, s in bids[:10]],
                "asks": [[round(p, 2), round(s, 4)] for p, s in asks[:10]],
            }
            # trades keep their own finer timestamps
            for tts, px, sz, side in trades:
                f.write(json.dumps({"kind": "trade", "ts": tts, "price": round(px, 2),
                                    "size": round(sz, 4), "side": side}) + "\n")
                out_trades += 1

        if pending_book is not None:
            f.write(json.dumps(pending_book) + "\n")
            out_books += 1

    span = (rows[-1]["row"]["T"] - rows[0]["row"]["T"]) / 1000.0 if len(rows) > 1 else 0
    print(f"Wrote {out_books} book ticks, {out_trades} inferred trades, span~{span:.0f}s -> {args.out}")
    print("NOTE: trades are inferred from touch deltas, not recorded prints.")


if __name__ == "__main__":
    main()