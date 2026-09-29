"""Event-Driven Market Simulation & Adverse Selection Backtester.

Microstructure model:
1. Stochastic mid with regime-switching toxic bursts (momentum drift + depth skew).
2. Taker trades sweep the book to a regime-dependent depth, so resting quotes
   within the swept band get filled. Bursts sweep deep AND price runs after:
   that combination is adverse selection.
3. Fill sizes respect queue position (a random fraction of the swept level).
4. Orders placed this tick rest first; trades match against orders from the
   previous tick, so there is no same-tick look-ahead.
"""

import math
import random
from dataclasses import dataclass, field
from typing import List, Optional
from spreadguard.config import SpreadGuardConfig
from spreadguard.market import BookSnapshot, Level, Trade, Fill
from spreadguard.controller import SpreadGuardController


@dataclass
class BacktestResult:
    config_name: str
    seed: int
    duration_hours: float
    total_volume_usdt: float
    net_pnl_usdt: float
    fees_paid_usdt: float
    fill_count: int
    max_drawdown_usdt: float
    ofi_gate_fires: int
    gate_fire_rate_per_hr: float
    states_fired: dict = field(default_factory=dict)


class MarketSimulator:
    """Generates synthetic L2 snapshots and aggressive taker flow with adverse regimes."""

    def __init__(self, initial_mid: float = 67000.0, seed: int = 42,
                 burst_prob: float = 0.008, burst_ticks: tuple = (12, 36)):
        # two independent streams: the market path must be identical across
        # strategy modes, only our fills consume the queue stream.
        self.rng = random.Random(seed)          # market: mid, books, trades, sweeps
        self.queue_rng = random.Random(seed + 7)  # our fill queue positions
        self.mid = initial_mid
        self.cur_ts_ms = 1727700000000

        self.in_burst = False
        self.burst_direction = "buy"
        self.burst_ticks_left = 0
        self.burst_prob = burst_prob
        self.burst_ticks = burst_ticks

    def step(self, dt_ms: int = 250):
        self.cur_ts_ms += dt_ms

        # regime switching
        if not self.in_burst:
            if self.rng.random() < self.burst_prob:
                self.in_burst = True
                self.burst_direction = "buy" if self.rng.random() > 0.5 else "sell"
                self.burst_ticks_left = self.rng.randint(*self.burst_ticks)
        else:
            self.burst_ticks_left -= 1
            if self.burst_ticks_left <= 0:
                self.in_burst = False

        # mid: noise + momentum drift during bursts (this is the adverse move).
        # Drift ~0.8bps/tick models a real BTC vol expansion, not a crash.
        vol = 0.00012 * math.sqrt(dt_ms / 1000.0)
        noise = self.rng.gauss(0, vol) * self.mid
        drift = 0.0
        if self.in_burst:
            drift = (0.00015 * self.mid) if self.burst_direction == "buy" else (-0.00008 * self.mid)
        self.mid = max(1000.0, self.mid + noise + drift)

        # spread widens under stress
        spread_bps = self.rng.uniform(1.2, 2.2) if not self.in_burst else self.rng.uniform(2.5, 4.5)
        half = (self.mid * spread_bps / 1e4) / 2.0
        best_bid = self.mid - half
        best_ask = self.mid + half

        bids, asks = [], []
        for i in range(10):
            step_px = (i + 1) * 2.5
            b_sz = self.rng.uniform(0.04, 0.25)
            a_sz = self.rng.uniform(0.04, 0.25)
            if self.in_burst:
                # flow piles on one side, depletes the other
                if self.burst_direction == "buy":
                    b_sz *= 2.5
                    a_sz *= 0.4
                else:
                    a_sz *= 2.5
                    b_sz *= 0.4
            bids.append(Level(price=round(best_bid - step_px, 2), size=round(b_sz, 4)))
            asks.append(Level(price=round(best_ask + step_px, 2), size=round(a_sz, 4)))

        book = BookSnapshot(ts_ms=self.cur_ts_ms, bids=bids, asks=asks)

        # taker trades
        trades: List[Trade] = []
        n = self.rng.randint(0, 2)
        if self.in_burst:
            n += self.rng.randint(2, 6)
        for _ in range(n):
            if self.in_burst:
                side = self.burst_direction
                sz = self.rng.uniform(0.02, 0.15)
            else:
                side = "buy" if self.rng.random() > 0.5 else "sell"
                sz = self.rng.uniform(0.005, 0.05)
            px = best_ask if side == "buy" else best_bid
            trades.append(Trade(ts_ms=self.cur_ts_ms, price=px, size=round(sz, 4), side=side))

        return book, trades

    def sweep_depth_bps(self, direction: str) -> float:
        """How deep past the touch a taker sweeps, in bps from the touch."""
        if self.in_burst and direction == self.burst_direction:
            # aggressive informed flow walks deep into the book
            return self.rng.uniform(1.2, 3.2)
        # passive flow mostly lifts the touch only
        return self.rng.uniform(0.1, 0.7)

    def queue_share(self, direction: str) -> float:
        """Our share of a swept level. At the touch we compete with the whole
        resting book (our size is small); deep in the book we are often alone."""
        if self.in_burst and direction == self.burst_direction:
            return self.queue_rng.uniform(0.15, 0.50)
        return self.queue_rng.uniform(0.02, 0.10)


def run_backtest(config: SpreadGuardConfig, num_ticks: int = 2400, seed: int = 42,
                 maker_fee_bps: float = 0.0, starting_cash: float = 800.0) -> BacktestResult:
    """Run one simulated session. 2400 ticks at 250ms = 10 minutes."""
    sim = MarketSimulator(initial_mid=67200.0, seed=seed)
    controller = SpreadGuardController(config, journal_dir=None)
    controller.position.cash_usdt = starting_cash

    fee_mult = maker_fee_bps / 1e4
    states_count: dict = {}
    gate_fires = 0
    pnl_curve: List[float] = []
    resting: List = []  # orders placed on the previous tick
    remaining_by_key: dict = {}  # (side, level) -> {"size": live size} for this tick

    for _ in range(num_ticks):
        book, trades = sim.step()

        # 1. match taker flow against orders rested on the previous tick.
        # Each resting order can only fill once per tick: consumed size is tracked.
        for t in trades:
            remaining = t.size
            if t.side == "buy":
                depth_bps = sim.sweep_depth_bps("buy")
                band = t.price * depth_bps / 1e4
                candidates = [o for o in resting if o.side == "ask" and o.price <= t.price + band]
                candidates.sort(key=lambda o: o.price)
            else:
                depth_bps = sim.sweep_depth_bps("sell")
                band = t.price * depth_bps / 1e4
                candidates = [o for o in resting if o.side == "bid" and o.price >= t.price - band]
                candidates.sort(key=lambda o: -o.price)

            for o in candidates:
                if remaining <= 1e-8:
                    break
                live = remaining_by_key.get((o.side, o.level))
                if live is None or live["size"] <= 1e-6:
                    continue
                fill_sz = min(live["size"], remaining)
                # queue position: our share of the swept level
                fill_sz *= sim.queue_share(t.side)
                if fill_sz < 1e-5:
                    continue
                # the watchdog cancels rests that would breach the cap
                if o.side == "bid" and controller.position.net_btc + fill_sz > config.max_abs_position_btc:
                    continue
                if o.side == "ask" and controller.position.net_btc - fill_sz < -config.max_abs_position_btc:
                    continue
                fee_usdt = o.price * fill_sz * fee_mult
                controller.on_fill(Fill(
                    ts_ms=book.ts_ms, side=o.side, price=o.price,
                    size=fill_sz, fee_usdt=fee_usdt, is_maker=True, mid_at_fill=book.mid
                ))
                live["size"] -= fill_sz
                remaining -= fill_sz

        # 2. feed trades to signals, run strategy tick
        for t in trades:
            controller.on_trade(t)
        orders = controller.on_tick(book)

        # fresh live-size ledger for the new resting orders
        remaining_by_key = {(o.side, o.level): {"size": o.size} for o in orders}
        resting = orders

        dec = controller.last_decision
        if dec:
            key = dec.state.value
            states_count[key] = states_count.get(key, 0) + 1
            if abs(dec.ofi_z) >= config.step_enter_z:
                gate_fires += 1

        # 3. mark equity
        pnl_curve.append(controller.position.mark_to_market(book.mid) - starting_cash)

    duration_hours = num_ticks * config.tick_ms / 1000.0 / 3600.0

    peak = -1e18
    max_dd = 0.0
    for pnl in pnl_curve:
        peak = max(peak, pnl)
        max_dd = max(max_dd, peak - pnl)

    return BacktestResult(
        config_name=config.mode.value,
        seed=seed,
        duration_hours=duration_hours,
        total_volume_usdt=controller.total_volume_usdt,
        net_pnl_usdt=pnl_curve[-1] if pnl_curve else 0.0,
        fees_paid_usdt=controller.position.fees_paid_usdt,
        fill_count=controller.fill_count,
        max_drawdown_usdt=max_dd,
        ofi_gate_fires=gate_fires,
        gate_fire_rate_per_hr=gate_fires / duration_hours if duration_hours > 0 else 0.0,
        states_fired=states_count,
    )
