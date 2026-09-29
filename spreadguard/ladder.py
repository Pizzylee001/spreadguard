"""Ladder Generator for SpreadGuard.

Calculates exact quote prices and sizes per level incorporating:
1. Microprice fair value anchor (size-weighted L1 pressure)
2. Inventory skew offset (pulling back toward zero delta)
3. Inventory recycling (boosting size on the de-risking side)
4. State-dependent spread expansions (WIDEN, STEP_BACK, PULL)
"""

from typing import List
from spreadguard.config import SpreadGuardConfig, QuoteMode
from spreadguard.market import BookSnapshot, Order, Position
from spreadguard.state_machine import StateDecision, QuoteState


class LadderGenerator:
    """Builds resting order intentions for both sides of the book."""

    def __init__(self, config: SpreadGuardConfig):
        self.config = config

    def generate(
        self,
        book: BookSnapshot,
        position: Position,
        decision: StateDecision,
        ts_ms: int
    ) -> List[Order]:
        cfg = self.config
        orders: List[Order] = []

        # 1. Fair Value Anchor: Microprice vs Mid
        fair_value = book.microprice() if cfg.use_microprice else book.mid

        # 2. Inventory Skew:
        # If long (net_btc > 0), skew prices down to encourage ask fills and deter bid fills
        # Skew fraction = net_btc / soft_band
        skew_ratio = max(-1.0, min(1.0, position.net_btc / max(1e-6, cfg.soft_band_btc)))
        skew_offset_bps = skew_ratio * cfg.skew_max_bps
        skew_price_shift = fair_value * (skew_offset_bps / 1e4)

        # Adjusted reference price:
        # positive net_btc -> positive skew_price_shift -> shift bids/asks downward by skew_price_shift
        center_price = fair_value - skew_price_shift

        # 3. Base half-spread and State Adjustments
        bid_extra_bps = 0.0
        ask_extra_bps = 0.0
        bid_size_mult = 1.0
        ask_size_mult = 1.0

        # 3a. SpreadGuard-only inventory defenses. The naive baseline keeps
        # classic mild skew only, so the comparison shows what the guard adds.
        if cfg.mode != QuoteMode.NAIVE:
            if position.net_btc >= cfg.soft_band_btc:
                bid_size_mult = 0.0                       # stop adding long
                ask_size_mult *= cfg.recycle_boost        # lean into selling down
            elif position.net_btc <= -cfg.soft_band_btc:
                ask_size_mult = 0.0                       # stop adding short
                bid_size_mult *= cfg.recycle_boost

        if decision.state == QuoteState.WIDEN:
            bid_extra_bps += cfg.widen_extra_bps
            ask_extra_bps += cfg.widen_extra_bps

        elif decision.state == QuoteState.STEP_BACK:
            if decision.fenced_side == "ask":
                ask_extra_bps += cfg.step_extra_bps
                ask_size_mult *= cfg.step_size_scale
                # slightly tighten safe bid to attract non-toxic maker fills
                bid_extra_bps = max(-0.20, -0.10)
            elif decision.fenced_side == "bid":
                bid_extra_bps += cfg.step_extra_bps
                bid_size_mult *= cfg.step_size_scale
                ask_extra_bps = max(-0.20, -0.10)

        elif decision.state == QuoteState.PULL:
            # Completely cancel the fenced side
            if decision.fenced_side == "ask":
                ask_size_mult = 0.0
            elif decision.fenced_side == "bid":
                bid_size_mult = 0.0

        # 4. Inventory Recycling:
        # If we have positive long inventory, boost ask order size to recycle capital faster
        if cfg.recycle:
            if position.net_btc > 0.005:
                ask_size_mult *= cfg.recycle_boost
            elif position.net_btc < -0.005:
                bid_size_mult *= cfg.recycle_boost

        # 5. Build Ladder Levels
        # Bids (Buy orders below center)
        if bid_size_mult > 0.0 and position.net_btc < cfg.max_abs_position_btc:
            for lvl in range(1, cfg.levels + 1):
                spread_bps = cfg.base_half_spread_bps + bid_extra_bps + (lvl - 1) * cfg.level_spacing_bps
                price = center_price * (1.0 - spread_bps / 1e4)
                # Ensure bid price is strictly below best ask (maker guarantee)
                price = min(price, book.best_bid)
                size = cfg.order_size_btc * bid_size_mult
                orders.append(Order(side="bid", price=round(price, 2), size=round(size, 4), level=lvl, ts_ms=ts_ms))

        # Asks (Sell orders above center)
        if ask_size_mult > 0.0 and position.net_btc > -cfg.max_abs_position_btc:
            for lvl in range(1, cfg.levels + 1):
                spread_bps = cfg.base_half_spread_bps + ask_extra_bps + (lvl - 1) * cfg.level_spacing_bps
                price = center_price * (1.0 + spread_bps / 1e4)
                # Ensure ask price is strictly above best bid (maker guarantee)
                price = max(price, book.best_ask)
                size = cfg.order_size_btc * ask_size_mult
                orders.append(Order(side="ask", price=round(price, 2), size=round(size, 4), level=lvl, ts_ms=ts_ms))

        return orders
