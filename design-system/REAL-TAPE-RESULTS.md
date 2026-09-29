# Real-tape results

Source: `predict-quant/binance-future-orderbook` on Hugging Face, recorded
Binance USDT-M perpetual depth20, 20 levels a side, ~100 ms cadence, Apache-2.0.
Read directly from the parquet file with a pure-JS reader (no rate limits), day
2026-03-07, 327,000 rows, split into two contiguous halves. Fills use the real
recorded book sweep in `backtest/replay.py`.

Caveat, stated plainly: the source has no trades column, so aggressive flow is
inferred from touch-size changes between snapshots, not recorded prints. This is
a real recorded book with inferred flow, not a fully recorded tape with prints.

## Out-of-sample result (the honest one)

Two contiguous 5-hour windows, BTC around 67.4k to 68.5k, $800 bankroll.
Pooled net PnL across both windows:

| Maker fee | Naive | Pull | StepBack | StepBack+Micro | FullSuite |
|---|---|---|---|---|---|
| -1.0 bps (rebate) | +$4.46 | +$3.04 | **+$4.67** | +$2.68 | +$1.36 |
| 0.0 bps (promo) | -$1.82 | -$2.02 | **-$0.38** | -$2.51 | -$5.32 |
| +2.0 bps (VIP0) | -$14.37 | -$12.13 | **-$10.48** | -$12.88 | -$18.68 |

Win count (best config per window): StepBack wins both windows at zero fee and
both at +2 bps.

## What the out-of-sample test changed

The single 618-second window had said microprice and recycling were best. They
do not hold up. On two 5-hour windows the plain STEP_BACK configuration is the
most robust: it is the best at zero fee and at +2 bps, and second best with a
rebate. Microprice and recycling both made results worse, so both now default
off in `config.py` with a note explaining why.

The guard beats the naive baseline at every fee level on the pooled result. That
is the one claim that survives out of sample.

## Fee reality

At a rebate the desk is slightly positive across 10 hours. At zero maker fee it
is roughly break-even to slightly negative. At the standard VIP0 maker fee of
2 bps it loses about $10 over 10 hours on $800, because a thin 1.5 bps half
spread cannot cover a 2 bps fee. The honest conclusion: this desk needs a maker
rebate tier or a wider edge to profit, and the fee tier is a business decision,
not a code decision.

## Reproduce

```
# read a full day straight from parquet, no rate limits
node backtest/pq_to_tape.mjs --file 2026-03-07_BTCUSDT_depth20.parquet \
     --out-a tape/real_a.jsonl --out-b tape/real_b.jsonl

py backtest/validate_tape.py --tape tape/real_a.jsonl
py backtest/validate_tape.py --tape tape/real_b.jsonl
py backtest/multiwindow.py --tapes tape/real_a.jsonl tape/real_b.jsonl --fees -1.0 0.0 2.0
```
