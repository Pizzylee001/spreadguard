"""SpreadGuard Orchestrator Controller.

Integrates SignalEngine, StateMachine, LadderGenerator, RiskWatchdog, and JournalLogger.
Can run against live Bitget WebSocket feed or deterministic Backtest Simulator.
"""

from typing import List, Optional
from spreadguard.config import SpreadGuardConfig
from spreadguard.market import BookSnapshot, Trade, Position, Order, Fill
from spreadguard.signals import SignalEngine
from spreadguard.state_machine import StateMachine, StateDecision, QuoteState
from spreadguard.ladder import LadderGenerator
from spreadguard.journal import JournalLogger


class SpreadGuardController:
    """Core autonomous agent controller."""

    def __init__(self, config: SpreadGuardConfig, journal_dir: Optional[str] = None):
        self.config = config
        self.signals = SignalEngine(config.ofi_window, config.intensity_window)
        self.state_machine = StateMachine(config)
        self.ladder = LadderGenerator(config)
        self.journal = JournalLogger(journal_dir) if journal_dir else None

        self.position = Position()
        self.total_volume_usdt: float = 0.0
        self.fill_count: int = 0
        self.active_orders: List[Order] = []

        self.last_decision: Optional[StateDecision] = None
        self.last_tick_ms: int = 0
        self.is_halted: bool = False
        self.halt_reason: str = ""

    def on_trade(self, trade: Trade) -> None:
        """Feed incoming market trades into signal engine."""
        self.signals.on_trade(trade)

    def on_fill(self, fill: Fill) -> None:
        """Process internal execution fill, fees included."""
        self.position.apply(fill.side, fill.price, fill.size, fill.fee_usdt)
        fill_val_usdt = fill.price * fill.size
        self.total_volume_usdt += fill_val_usdt
        self.fill_count += 1

    def on_tick(self, book: BookSnapshot) -> List[Order]:
        """Execute one 250ms strategy control loop iteration."""
        ts_ms = book.ts_ms
        self.last_tick_ms = ts_ms

        # 1. Hard Watchdog Checks
        if abs(self.position.net_btc) > self.config.max_abs_position_btc:
            self.is_halted = True
            self.halt_reason = f"Max position breached: {self.position.net_btc:.4f} BTC > {self.config.max_abs_position_btc} BTC"
            self.active_orders.clear()
            return []

        # 2. Update Signals
        ofi_z, int_z = self.signals.on_tick(book)

        # 3. Evaluate State Machine
        decision = self.state_machine.evaluate(ofi_z, int_z)
        state_changed = (self.last_decision is None or self.last_decision.state != decision.state)

        # 4. Generate Ladder
        orders = self.ladder.generate(book, self.position, decision, ts_ms)
        self.active_orders = orders

        # 5. Record Journal on State Change or Significant Action
        if self.journal and (state_changed or decision.state in (QuoteState.PULL, QuoteState.STEP_BACK)):
            action_desc = f"Quotes updated: {len(orders)} active orders ({decision.state.value})"
            self.journal.record_decision(ts_ms, decision, book, self.position, action_desc)

        # 6. Periodic State Snapshot (forced on state change, else 1/sec)
        if self.journal:
            self.journal.update_state_snapshot(
                ts_ms,
                decision,
                book,
                self.position,
                self.total_volume_usdt,
                self.fill_count,
                self.active_orders,
                force=state_changed
            )

        self.last_decision = decision
        return orders
