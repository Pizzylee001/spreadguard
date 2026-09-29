"""SpreadGuard Decision Journal and State Persistence.

Outputs:
1. `journal.jsonl` - Real-time append-only stream of trade/defense decisions
2. `agent_state.json` - Current operational snapshot read by Next.js Control Room
"""

import json
import os
import time
from typing import Any, Dict, List, Optional
from spreadguard.market import Position, BookSnapshot
from spreadguard.state_machine import StateDecision, QuoteState


class JournalLogger:
    """Writes auditable logs for judges and control-room monitors."""

    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        self.journal_path = os.path.join(output_dir, "journal.jsonl")
        self.state_path = os.path.join(output_dir, "agent_state.json")
        self.recent_entries: List[Dict[str, Any]] = []
        self._last_snapshot_ms: int = -10_000  # force first write

    def _atomic_write(self, payload: str) -> None:
        """Atomic replace with retry: cloud-synced folders (OneDrive) can hold
        the target file briefly while syncing."""
        tmp_path = self.state_path + ".tmp"
        for attempt in range(5):
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write(payload)
                os.replace(tmp_path, self.state_path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))

    def record_decision(
        self,
        ts_ms: int,
        decision: StateDecision,
        book: BookSnapshot,
        position: Position,
        action_detail: str
    ) -> Dict[str, Any]:
        iso_time = time.strftime('%H:%M:%S', time.gmtime(ts_ms / 1000.0))
        msec = ts_ms % 1000
        stamp = f"{iso_time}.{msec:03d}"

        entry = {
            "ts_ms": ts_ms,
            "timestamp": stamp,
            "state": decision.state.value,
            "fenced_side": decision.fenced_side,
            "ofi_z": round(decision.ofi_z, 2),
            "intensity_z": round(decision.intensity_z, 2),
            "mid_price": round(book.mid, 2),
            "spread_bps": round(book.spread_bps, 2),
            "position_btc": round(position.net_btc, 4),
            "realized_pnl_usdt": round(position.realized_pnl_usdt, 2),
            "reason": decision.reason,
            "action": action_detail
        }

        # Append to journal.jsonl
        with open(self.journal_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

        self.recent_entries.insert(0, entry)
        if len(self.recent_entries) > 50:
            self.recent_entries.pop()

        return entry

    def update_state_snapshot(
        self,
        ts_ms: int,
        decision: StateDecision,
        book: BookSnapshot,
        position: Position,
        volume_usdt: float,
        fill_count: int,
        recent_orders: list,
        force: bool = False
    ) -> None:
        # throttle: one snapshot per simulated second, plus forced writes on
        # state changes. A 4Hz write storm chokes cloud-synced folders.
        if not force and (ts_ms - self._last_snapshot_ms) < 1000:
            return
        self._last_snapshot_ms = ts_ms

        snapshot = {
            "version": "0.9.0",
            "updated_ts_ms": ts_ms,
            "status": "RUNNING",
            "state": decision.state.value,
            "fenced_side": decision.fenced_side,
            "reason": decision.reason,
            "market": {
                "mid": round(book.mid, 2),
                "microprice": round(book.microprice(), 2),
                "spread_bps": round(book.spread_bps, 2),
                "best_bid": round(book.best_bid, 2),
                "best_ask": round(book.best_ask, 2)
            },
            "signals": {
                "ofi_z": round(decision.ofi_z, 2),
                "intensity_z": round(decision.intensity_z, 2)
            },
            "account": {
                "net_position_btc": round(position.net_btc, 4),
                "position_value_usdt": round(position.net_btc * book.mid, 2),
                "realized_pnl_usdt": round(position.realized_pnl_usdt, 2),
                "unrealized_pnl_usdt": round(position.mark_to_market(book.mid), 2),
                "total_volume_usdt": round(volume_usdt, 2),
                "fill_count": fill_count
            },
            "active_orders": [
                {
                    "side": o.side,
                    "price": o.price,
                    "size": o.size,
                    "level": o.level
                } for o in recent_orders
            ],
            "recent_journal": self.recent_entries[:10]
        }

        # atomic write
        self._atomic_write(json.dumps(snapshot, indent=2))
