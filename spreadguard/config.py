"""SpreadGuard strategy configuration.

All thresholds are expressed in the units the signals actually produce
(z-scores, basis points, BTC) so a parameter change maps to a real cause.
"""

from dataclasses import dataclass, field
from enum import Enum


class QuoteMode(str, Enum):
    """How the desk reacts to toxic flow."""

    NAIVE = "naive"        # never defends, always quotes (baseline to beat)
    PULL = "pull"          # cancel the fenced side
    STEP_BACK = "stepback"  # push the fenced side out, keep it live


@dataclass
class SpreadGuardConfig:
    # --- market shape ---
    tick_ms: int = 250
    levels: int = 6
    order_size_btc: float = 0.004
    base_half_spread_bps: float = 0.75      # 1.5 bps round trip at rest
    level_spacing_bps: float = 0.50

    # --- signal windows ---
    ofi_window: int = 20
    intensity_window: int = 20
    min_window: int = 8                      # do not gate before this many ticks

    # --- state machine gates (z-score units) ---
    widen_enter_z: float = 1.50
    widen_exit_z: float = 0.80
    step_enter_z: float = 2.00
    step_exit_z: float = 0.80
    pull_enter_z: float = 3.50               # only the worst flow forces a full pull
    pull_exit_z: float = 1.00

    # --- defensive geometry ---
    widen_extra_bps: float = 0.80            # added to both sides when widening
    step_extra_bps: float = 4.00             # fenced side pushed out this far
    step_size_scale: float = 0.50            # and quoted at half size

    # --- inventory ---
    target_net_btc: float = 0.0
    soft_band_btc: float = 0.020             # start skewing once beyond half band
    max_abs_position_btc: float = 0.050
    skew_max_bps: float = 1.20               # most we will lean to mean-revert

    # --- inventory recycling ---
    # Default off for the same reason as microprice: no out-of-sample edge yet.
    recycle: bool = False
    recycle_boost: float = 1.60              # size up the side that flattens us

    # --- risk ---
    daily_stop_pct: float = 2.5
    stale_feed_ms: int = 800
    max_orders_per_sec: int = 4

    # --- microstructure ---
    # Microprice and recycling looked strong on a single short window but did not
    # hold up out of sample on two 5-hour windows (see REAL-TAPE-RESULTS.md), so
    # both default off. Enable only with fresh out-of-sample evidence.
    use_microprice: bool = False

    # --- mode ---
    mode: QuoteMode = QuoteMode.STEP_BACK

    def validate(self) -> list[str]:
        issues = []
        if self.base_half_spread_bps <= 0:
            issues.append("base_half_spread_bps must be positive")
        if not (self.widen_enter_z < self.step_enter_z <= self.pull_enter_z):
            issues.append("gates must increase: widen < step <= pull")
        if self.max_abs_position_btc <= 0:
            issues.append("max_abs_position_btc must be positive")
        if self.levels < 1:
            issues.append("levels must be at least 1")
        return issues
