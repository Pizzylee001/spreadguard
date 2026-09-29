# SpreadGuard

An autonomous market-making agent for Bitget BTC-USDT perpetuals, built for the
Botcamp Agent Builders Cup. It quotes both sides of the mid as a Hummingbot V2
controller and defends against adverse selection with a four-state quote machine.
No LLM in the execution loop. Every fill is deterministic and auditable.

## How it decides

Two signals sampled every 250 ms:

- **Order flow imbalance (OFI):** signed taker flow plus L1 book pressure,
  normalized to a z-score over a 20-tick window.
- **Trade intensity:** the z-score of taker volume arrival.

Four states:

| State | Gate | Action |
|---|---|---|
| QUOTE | flow below 0.8 z | six symmetric maker quotes around the microprice |
| WIDEN | intensity above 1.5 z | both bands widen 0.8 bps |
| STEP_BACK | OFI above 2.0 z | fenced side steps out 4 bps at half size, desk stays live |
| PULL | OFI above 3.5 z | fenced side cancels, emergency brake |

Quotes center on the microprice, the touch weighted by opposite size. Inventory
recycling sizes up the de-risking side past the soft band, and hard flattening
stops the desk adding exposure past it. The watchdog halts on max position,
daily stop, stale feed, or order rate. All orders are post-only maker.

## Evidence: two tape sources

Two harnesses run the identical five-config A/B through the same controller.

### Synthetic tape (stress test)

A deliberately adverse-heavy generator with a toxic burst about every two
minutes, 8 matched 10-minute sessions, same paths for every configuration.
Use this to watch the guard work under stress, not as a market estimate.

| Configuration | Volume | Net PnL | Fills | MaxDD | Desk time |
|---|---|---|---|---|---|
| Naive, no guard | $94,676 | -$116.60 | 3689 | $187.92 | quoting 100% |
| Binary pull | $71,699 | -$67.04 | 2981 | $105.92 | quoting 85%, widened 9%, pulled 6% |
| Step-back | $70,082 | -$64.24 | 2977 | $105.92 | quoting 84%, widened 9%, stepped 7% |
| Step-back plus microprice | $70,227 | -$63.01 | 2968 | $93.72 | quoting 84%, widened 9%, stepped 7% |
| Full suite, plus recycling | $78,070 | -$62.74 | 2911 | $112.05 | quoting 84%, widened 9%, stepped 7% |

Under this stress tape the guard cuts losses 46 percent at 18 percent less
volume, which is the mechanism working as designed.

### Recorded tape (built and run here, out of sample)

Real recorded Binance USDT-M perpetual depth20 from the Hugging Face dataset
`predict-quant/binance-future-orderbook` (20 levels a side, ~100 ms cadence,
Apache-2.0), read straight from parquet with a pure-JS reader. Two contiguous
5-hour windows from 2026-03-07, $800 bankroll, pooled net PnL:

| Maker fee | Naive | Pull | StepBack | StepBack+Micro | FullSuite |
|---|---|---|---|---|---|
| -1.0 bps (rebate) | +$4.46 | +$3.04 | **+$4.67** | +$2.68 | +$1.36 |
| 0.0 bps (promo) | -$1.82 | -$2.02 | **-$0.38** | -$2.51 | -$5.32 |
| +2.0 bps (VIP0) | -$14.37 | -$12.13 | **-$10.48** | -$12.88 | -$18.68 |

The plain STEP_BACK configuration is the most robust out of sample and wins both
windows at zero fee and at +2 bps. Microprice and recycling looked better on a
single short window but did not hold up on the longer out-of-sample test, so both
now default off. The guard beats the naive baseline at every fee level, which is
the one claim that survives out of sample. Full table in
`design-system/REAL-TAPE-RESULTS.md`.

The honest fee picture: the desk is slightly positive with a maker rebate,
roughly break-even at zero fee, and loses about $10 over 10 hours at the standard
VIP0 maker fee of 2 bps, because a thin 1.5 bps half spread cannot cover 2 bps.
Profit needs a rebate tier or a wider edge.

Caveat, stated plainly: the source dataset has no trades column, so aggressive
flow is inferred from touch-size changes and labeled inferred. This is a real
recorded book with inferred flow, not a fully recorded tape with prints.

The `record_bitget.mjs` recorder still stands for a true Bitget capture where the
network reaches Bitget, which the machine that built this could not.

Honest status: the operating headline is the real recorded tape above. The
synthetic figures higher up are a stress test and are labeled as such.

## Layout

```
SpreadGuard/
  spreadguard/            strategy package (config, market, signals, state machine,
                          ladder, controller, journal)
  backtest/
    simulator.py          synthetic market with toxic bursts, matched paths
    sweep.py              5-config A/B on synthetic tape
    replay.py             5-config A/B on a recorded tape (real book fill model)
    multiwindow.py        pooled out-of-sample A/B across tapes and fee levels
    hf_record.py          pull real depth via the HF datasets-server API
    pq_to_tape.mjs        read a full depth parquet day into tapes (pure JS)
    pq_inspect.mjs        inspect a parquet file schema and rows
    record_bitget.mjs     Bitget public-stream recorder (Node 22+, no deps)
    make_sample_tape.py   schema-exact sample tape for testing the replay path
    validate_tape.py      tape schema and sanity gate
  tests/                  12 unit tests
  run_session.py          scripted session -> state/journal.jsonl + agent_state.json
  state/                  generated session output, read by the control room
  tape/                   real recorded and sample tapes
  DESIGN.md               approved control-room design document
  design-system/          project ledger entry and real-tape results
```

The control room preview lives in `../SpreadGuard-preview/` (desk, journal, limits,
strategy pages plus shared stylesheet). It is read-only and never trades.

## Run it

```
cd SpreadGuard
py -m tests.test_strategy                        # 12 unit tests
py backtest/sweep.py                             # synthetic comparative benchmark
py run_session.py --mode stepback --ticks 2400   # one session, writes state/

# Real tape path, straight from a full parquet day (no rate limits)
# requires: npm install hyparquet hyparquet-compressors in a node_modules dir
node backtest/pq_to_tape.mjs --file 2026-03-07_BTCUSDT_depth20.parquet --out-a tape/real_a.jsonl --out-b tape/real_b.jsonl
py backtest/validate_tape.py --tape tape/real_a.jsonl
py backtest/validate_tape.py --tape tape/real_b.jsonl
py backtest/multiwindow.py --tapes tape/real_a.jsonl tape/real_b.jsonl --fees -1.0 0.0 2.0
```

Requirements: Python 3.10+ (standard library only) for the strategy and harness.
Node 22+ and the two listed packages only for reading parquet: hyparquet and
hyparquet-compressors. The live Bitget recorder needs no packages at all.
