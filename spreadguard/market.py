"""Market data primitives shared by the live controller and the backtester.

A single book shape is used everywhere so the strategy code cannot tell
whether it is running on recorded data or on the simulator.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Level:
    price: float
    size: float


@dataclass(frozen=True)
class BookSnapshot:
    """One top-of-book and depth state, timestamped in milliseconds."""

    ts_ms: int
    bids: list[Level]          # best first, descending price
    asks: list[Level]          # best first, ascending price

    @property
    def best_bid(self) -> float:
        return self.bids[0].price

    @property
    def best_ask(self) -> float:
        return self.asks[0].price

    @property
    def mid(self) -> float:
        return (self.best_bid + self.best_ask) / 2.0

    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid

    @property
    def spread_bps(self) -> float:
        return (self.spread / self.mid) * 1e4

    def microprice(self) -> float:
        """Size-weighted fair value.

        Weights the touch by the opposite size, so a thin ask pulls fair
        value up before the mid moves. Anticipates pressure the mid ignores.
        """
        bb, ba = self.best_bid, self.best_ask
        bsz = self.bids[0].size
        asz = self.asks[0].size
        denom = bsz + asz
        if denom <= 0:
            return self.mid
        return (bb * asz + ba * bsz) / denom

    def depth_within_bps(self, side: str, bps: float) -> float:
        """Total resting size on one side within a price band of the mid."""
        ref = self.mid
        band = ref * bps / 1e4
        levels = self.bids if side == "bid" else self.asks
        total = 0.0
        for lv in levels:
            if abs(lv.price - ref) <= band:
                total += lv.size
        return total


@dataclass(frozen=True)
class Trade:
    """One aggressive (taker) print."""

    ts_ms: int
    price: float
    size: float        # BTC
    side: str          # "buy" = taker lifted the ask, "sell" = taker hit the bid


@dataclass
class Fill:
    ts_ms: int
    side: str          # our side that got filled: "bid" or "ask"
    price: float
    size: float
    fee_usdt: float    # negative = maker rebate credited
    is_maker: bool
    mid_at_fill: float


@dataclass
class Position:
    net_btc: float = 0.0
    cash_usdt: float = 0.0
    fees_paid_usdt: float = 0.0
    realized_pnl_usdt: float = 0.0
    avg_entry_price: float = 0.0

    def mark_to_market(self, mid: float) -> float:
        return self.cash_usdt + self.net_btc * mid

    def apply(self, side: str, price: float, size: float, fee_usdt: float) -> None:
        """Update position on a fill. Buying on the bid adds BTC and spends cash.

        fee_usdt is a signed cost: positive = fee paid, negative = rebate credited.
        Realized PnL uses the average-entry method on reducing fills.
        """
        signed = size if side == "bid" else -size
        before = self.net_btc
        after = before + signed

        if before == 0.0 or (before > 0) == (after > 0) or after == 0.0:
            # same-direction fill (or flat-to-flat): blend the entry price over
            # the portion that extends the position
            extending = min(abs(signed), abs(after)) if after != 0.0 else 0.0
            if before == 0.0:
                self.avg_entry_price = price
            elif extending > 0:
                old_abs = abs(before)
                self.avg_entry_price = (
                    self.avg_entry_price * old_abs + price * extending
                ) / (old_abs + extending)
        else:
            # reducing fill: realize against the average entry
            closing = min(abs(signed), abs(before))
            if before > 0:
                self.realized_pnl_usdt += (price - self.avg_entry_price) * closing
            else:
                self.realized_pnl_usdt += (self.avg_entry_price - price) * closing
            if after == 0.0:
                self.avg_entry_price = 0.0
            elif (before > 0) != (after > 0):
                # flipped through zero: remainder opens a new position
                self.avg_entry_price = price

        self.net_btc = after
        if side == "bid":
            self.cash_usdt -= price * size
        else:
            self.cash_usdt += price * size
        self.cash_usdt -= fee_usdt
        self.fees_paid_usdt += fee_usdt


@dataclass
class Order:
    side: str
    price: float
    size: float
    level: int
    ts_ms: int
    oid: int = 0

    def key(self) -> tuple[str, int]:
        return (self.side, self.level)
