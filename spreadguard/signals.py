"""Real-time signal estimators for SpreadGuard: OFI and Trade Intensity.

Both maintain rolling statistics and emit z-scores so the state machine
gates on normalized units rather than raw volumes that drift across sessions.
"""

from collections import deque
import math
from typing import Deque, Optional
from spreadguard.market import BookSnapshot, Trade


class RunningStats:
    """Welford-style rolling window mean and variance for fast z-scores."""

    def __init__(self, maxlen: int = 40):
        self.maxlen = maxlen
        self.samples: Deque[float] = deque(maxlen=maxlen)

    def push(self, val: float) -> None:
        self.samples.append(float(val))

    @property
    def count(self) -> int:
        return len(self.samples)

    @property
    def mean(self) -> float:
        if not self.samples:
            return 0.0
        return sum(self.samples) / len(self.samples)

    @property
    def std(self) -> float:
        n = len(self.samples)
        if n < 2:
            return 1e-6
        m = self.mean
        var = sum((x - m) ** 2 for x in self.samples) / (n - 1)
        return math.sqrt(max(1e-12, var))

    def z_score(self, current_val: Optional[float] = None) -> float:
        if self.count < 3:
            return 0.0
        val = current_val if current_val is not None else (self.samples[-1] if self.samples else 0.0)
        s = self.std
        if s <= 1e-9:
            return 0.0
        z = (val - self.mean) / s
        # clamp extreme outliers so one spike does not blow out state transitions
        return max(-8.0, min(8.0, z))


class SignalEngine:
    """Computes Cont-Kukanov-Stoikov Order Flow Imbalance (OFI) and arrival velocity."""

    def __init__(self, ofi_window: int = 40, intensity_window: int = 40):
        self.prev_book: Optional[BookSnapshot] = None
        self.ofi_stats = RunningStats(maxlen=ofi_window)
        self.intensity_stats = RunningStats(maxlen=intensity_window)

        self.last_raw_ofi: float = 0.0
        self.last_raw_intensity: float = 0.0
        self.trades_since_last_tick: list[Trade] = []

    def on_trade(self, trade: Trade) -> None:
        self.trades_since_last_tick.append(trade)

    def on_tick(self, book: BookSnapshot) -> tuple[float, float]:
        """Compute OFI and Trade Intensity z-scores for the current tick."""
        if self.prev_book is None:
            self.prev_book = book
            self.trades_since_last_tick.clear()
            return 0.0, 0.0

        pb = self.prev_book
        cb = book

        # 1. Classical L1 Order Flow Imbalance delta (Cont et al.)
        # Bid delta:
        if cb.best_bid > pb.best_bid:
            delta_bid = cb.bids[0].size
        elif cb.best_bid == pb.best_bid:
            delta_bid = cb.bids[0].size - pb.bids[0].size
        else:
            delta_bid = -pb.bids[0].size

        # Ask delta:
        if cb.best_ask < pb.best_ask:
            delta_ask = cb.asks[0].size
        elif cb.best_ask == pb.best_ask:
            delta_ask = cb.asks[0].size - pb.asks[0].size
        else:
            delta_ask = -pb.asks[0].size

        # Raw OFI: net change in buying interest vs selling interest,
        # book pressure plus signed taker flow. The trade term is what makes
        # bursts visible: pure L1 deltas understate informed flow.
        signed_trade_vol = sum(
            t.size if t.side == "buy" else -t.size
            for t in self.trades_since_last_tick
        )
        raw_ofi = (delta_bid - delta_ask) + signed_trade_vol
        self.ofi_stats.push(raw_ofi)
        self.last_raw_ofi = raw_ofi

        # 2. Trade Arrival Intensity: taker volume arrived in this tick window
        trade_vol = sum(t.size for t in self.trades_since_last_tick)
        self.intensity_stats.push(trade_vol)
        self.last_raw_intensity = trade_vol

        self.prev_book = cb
        self.trades_since_last_tick.clear()

        ofi_z = self.ofi_stats.z_score(raw_ofi)
        int_z = self.intensity_stats.z_score(trade_vol)

        return ofi_z, int_z
