"""SpreadGuard unit tests. Run: py -m tests.test_strategy"""

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from spreadguard.config import SpreadGuardConfig, QuoteMode
from spreadguard.market import BookSnapshot, Level, Trade, Position
from spreadguard.signals import SignalEngine, RunningStats
from spreadguard.state_machine import StateMachine, QuoteState
from spreadguard.ladder import LadderGenerator


def make_book(ts=1000, bid_px=100.0, ask_px=100.2, bsz=1.0, asz=1.0):
    return BookSnapshot(
        ts_ms=ts,
        bids=[Level(bid_px - 0.1 * i, bsz) for i in range(5)],
        asks=[Level(ask_px + 0.1 * i, asz) for i in range(5)],
    )


def test_microprice_weights_by_opposite_size():
    # thin ask (0.5) vs fat bid (1.5): microprice must sit above the mid
    book = make_book(bsz=1.5, asz=0.5)
    mid = book.mid
    mp = book.microprice()
    assert mp > mid, "thin ask should pull fair value up"
    # exact formula check
    expected = (100.0 * 0.5 + 100.2 * 1.5) / 2.0
    assert abs(mp - expected) < 1e-9


def test_running_stats_zscore_bounds():
    rs = RunningStats(maxlen=20)
    for v in [1.0] * 15:
        rs.push(v)
    assert rs.z_score(1.0) == 0.0, "zero variance should clamp to 0"
    for v in [1.0] * 18 + [50.0]:
        rs.push(v)
    z = rs.z_score(50.0)
    assert z > 2.0, "outlier should produce a high z-score"
    assert z <= 8.0, "z-score must stay clamped"


def test_state_machine_pull_on_extreme_ofi():
    cfg = SpreadGuardConfig(mode=QuoteMode.STEP_BACK)
    sm = StateMachine(cfg)
    d = sm.evaluate(ofi_z=4.0, intensity_z=0.5)
    assert d.state == QuoteState.PULL, "extreme OFI must pull"
    assert d.fenced_side == "ask", "positive OFI fences the ask side"

    d2 = sm.evaluate(ofi_z=-4.0, intensity_z=0.5)
    assert d2.state == QuoteState.PULL
    assert d2.fenced_side == "bid", "negative OFI fences the bid side"


def test_state_machine_stepback_band():
    cfg = SpreadGuardConfig(mode=QuoteMode.STEP_BACK)
    sm = StateMachine(cfg)
    d = sm.evaluate(ofi_z=2.5, intensity_z=0.2)
    assert d.state == QuoteState.STEP_BACK
    # inside the hold band: stays defensive
    d = sm.evaluate(ofi_z=1.5, intensity_z=0.2)
    assert d.state == QuoteState.STEP_BACK, "hysteresis must hold the state"
    # below the exit gate: back to quoting
    d = sm.evaluate(ofi_z=0.3, intensity_z=0.2)
    assert d.state == QuoteState.QUOTE


def test_state_machine_naive_never_defends():
    cfg = SpreadGuardConfig(mode=QuoteMode.NAIVE)
    sm = StateMachine(cfg)
    d = sm.evaluate(ofi_z=7.9, intensity_z=7.9)
    assert d.state == QuoteState.QUOTE and d.fenced_side is None


def test_state_machine_widen_on_intensity():
    cfg = SpreadGuardConfig(mode=QuoteMode.STEP_BACK)
    sm = StateMachine(cfg)
    d = sm.evaluate(ofi_z=0.4, intensity_z=2.2)
    assert d.state == QuoteState.WIDEN


def test_ladder_pull_cancels_fenced_side():
    cfg = SpreadGuardConfig(mode=QuoteMode.PULL, levels=6)
    gen = LadderGenerator(cfg)
    book = make_book()
    from spreadguard.state_machine import StateDecision
    dec = StateDecision(QuoteState.PULL, "ask", "test", 4.0, 0.5)
    orders = gen.generate(book, Position(), dec, 1000)
    assert all(o.side == "bid" for o in orders), "pulled ask side must produce no asks"


def test_ladder_stepback_pushes_fence():
    cfg = SpreadGuardConfig(mode=QuoteMode.STEP_BACK, levels=3, base_half_spread_bps=1.0)
    gen = LadderGenerator(cfg)
    book = make_book(bid_px=100.0, ask_px=100.05)  # touch inside the fence distance
    from spreadguard.state_machine import StateDecision
    dec = StateDecision(QuoteState.STEP_BACK, "ask", "test", 2.5, 0.5)
    orders = gen.generate(book, Position(), dec, 1000)
    asks = [o for o in orders if o.side == "ask"]
    bids = [o for o in orders if o.side == "bid"]
    assert asks and bids, "step-back keeps both sides live"
    first_ask = min(a.price for a in asks)
    expected = book.mid * (1 + (1.0 + cfg.step_extra_bps) / 1e4)
    assert abs(first_ask - expected) < 0.02, f"fenced ask must sit at base+{cfg.step_extra_bps}bps, got {first_ask} vs {expected}"


def test_ladder_flattening_stops_accumulation():
    cfg = SpreadGuardConfig(mode=QuoteMode.STEP_BACK, levels=3, soft_band_btc=0.02)
    gen = LadderGenerator(cfg)
    book = make_book()
    from spreadguard.state_machine import StateDecision
    pos = Position(net_btc=0.03)  # long past the band
    dec = StateDecision(QuoteState.QUOTE, None, "test", 0.1, 0.1)
    orders = gen.generate(book, pos, dec, 1000)
    assert all(o.side == "ask" for o in orders), "long past band must only quote asks"


def test_ladder_respects_position_cap():
    cfg = SpreadGuardConfig(mode=QuoteMode.STEP_BACK, levels=3, max_abs_position_btc=0.05)
    gen = LadderGenerator(cfg)
    book = make_book()
    from spreadguard.state_machine import StateDecision
    pos = Position(net_btc=0.049)  # at the cap
    dec = StateDecision(QuoteState.QUOTE, None, "test", 0.1, 0.1)
    orders = gen.generate(book, pos, dec, 1000)
    assert all(o.side == "ask" for o in orders), "at the cap no new bids may rest"


def test_signal_engine_detects_burst():
    eng = SignalEngine(ofi_window=20, intensity_window=20)
    # calm baseline
    for i in range(30):
        eng.on_tick(make_book(ts=i * 250, bsz=1.0, asz=1.0))
        eng.on_trade(Trade(i * 250, 100.2, 0.01, "buy" if i % 2 else "sell"))
    # burst: big bid-side book piling + heavy buy flow
    ofis, ints = [], []
    for i in range(30, 40):
        eng.on_trade(Trade(i * 250, 100.2, 0.2, "buy"))
        ofi_z, int_z = eng.on_tick(make_book(ts=i * 250, bsz=3.0, asz=0.3))
        ofis.append(ofi_z)
        ints.append(int_z)
    assert max(ofis) > 2.0 or max(ints) > 1.5, f"burst must register: ofi={ofis} int={ints}"


def test_config_validation():
    cfg = SpreadGuardConfig()
    assert cfg.validate() == []
    bad = SpreadGuardConfig(widen_enter_z=5.0, step_enter_z=2.0)
    assert any("gates" in i for i in bad.validate())


def run_all():
    tests = [fn for name, fn in globals().items() if name.startswith("test_") and callable(fn)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if run_all() else 0)
