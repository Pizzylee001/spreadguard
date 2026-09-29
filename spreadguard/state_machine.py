"""SpreadGuard State Machine: Deterministic Adverse Selection Mitigation.

Transitions between QUOTE, WIDEN, STEP_BACK, and PULL based on OFI and intensity z-scores.
Includes hysteretic exit bands to prevent rapid flapping around threshold boundaries.
"""

from enum import Enum
from dataclasses import dataclass
from typing import Optional
from spreadguard.config import SpreadGuardConfig, QuoteMode


class QuoteState(str, Enum):
    QUOTE = "QUOTE"          # Symmetrical quoting around fair value
    WIDEN = "WIDEN"          # High intensity burst: spreads widened on both sides
    STEP_BACK = "STEP_BACK"  # Informed flow: toxic side pushed back 3 bps, safe side tightened
    PULL = "PULL"            # Extreme flow: toxic side completely cancelled


@dataclass
class StateDecision:
    state: QuoteState
    fenced_side: Optional[str]  # "ask" = informed buying (lift asks), "bid" = informed selling (hit bids)
    reason: str
    ofi_z: float
    intensity_z: float


class StateMachine:
    """Evaluates flow signals and triggers quoting behavior updates."""

    def __init__(self, config: SpreadGuardConfig):
        self.config = config
        self.current_state: QuoteState = QuoteState.QUOTE
        self.fenced_side: Optional[str] = None
        self.ticks_in_state: int = 0

    def evaluate(self, ofi_z: float, intensity_z: float) -> StateDecision:
        self.ticks_in_state += 1
        cfg = self.config

        # Determine which side is facing adverse flow:
        # High positive OFI (>0) = heavy net buy flow -> Ask side is toxic
        # High negative OFI (<0) = heavy net sell flow -> Bid side is toxic
        candidate_fenced_side = "ask" if ofi_z >= 0 else "bid"
        abs_ofi = abs(ofi_z)

        # Baseline NAIVE mode never adjusts quotes
        if cfg.mode == QuoteMode.NAIVE:
            self.current_state = QuoteState.QUOTE
            self.fenced_side = None
            return StateDecision(QuoteState.QUOTE, None, "Naive baseline: unhedged symmetric quoting", ofi_z, intensity_z)

        # 1. State: PULL (Emergency complete cancellation)
        if abs_ofi >= cfg.pull_enter_z:
            if self.current_state != QuoteState.PULL or self.fenced_side != candidate_fenced_side:
                self.current_state = QuoteState.PULL
                self.fenced_side = candidate_fenced_side
                self.ticks_in_state = 1
                return StateDecision(
                    QuoteState.PULL,
                    self.fenced_side,
                    f"OFI {ofi_z:+.2f}z crossed emergency threshold ({cfg.pull_enter_z:.1f}z): pulled {self.fenced_side} side",
                    ofi_z,
                    intensity_z
                )

        # In PULL, check if ready to de-escalate with hysteresis
        if self.current_state == QuoteState.PULL:
            if abs_ofi <= cfg.pull_exit_z:
                # De-escalate to STEP_BACK or QUOTE
                self.current_state = QuoteState.STEP_BACK if cfg.mode == QuoteMode.STEP_BACK else QuoteState.QUOTE
                self.ticks_in_state = 1
                return StateDecision(
                    self.current_state,
                    self.fenced_side if self.current_state == QuoteState.STEP_BACK else None,
                    f"OFI {ofi_z:+.2f}z normalized below pull exit threshold ({cfg.pull_exit_z:.1f}z)",
                    ofi_z,
                    intensity_z
                )
            else:
                return StateDecision(QuoteState.PULL, self.fenced_side, "Maintaining emergency pull", ofi_z, intensity_z)

        # 2. State: STEP_BACK (Push toxic side out, stay in market)
        if cfg.mode == QuoteMode.STEP_BACK:
            if abs_ofi >= cfg.step_enter_z:
                self.current_state = QuoteState.STEP_BACK
                self.fenced_side = candidate_fenced_side
                return StateDecision(
                    QuoteState.STEP_BACK,
                    self.fenced_side,
                    f"OFI {ofi_z:+.2f}z exceeded step-back gate ({cfg.step_enter_z:.1f}z): stepped back {self.fenced_side} quotes +{cfg.step_extra_bps}bps",
                    ofi_z,
                    intensity_z
                )
            elif self.current_state == QuoteState.STEP_BACK:
                if abs_ofi <= cfg.step_exit_z:
                    self.current_state = QuoteState.QUOTE
                    self.fenced_side = None
                    self.ticks_in_state = 1
                    return StateDecision(QuoteState.QUOTE, None, f"OFI {ofi_z:+.2f}z dropped below exit gate ({cfg.step_exit_z:.1f}z)", ofi_z, intensity_z)
                else:
                    return StateDecision(QuoteState.STEP_BACK, self.fenced_side, "Maintaining step-back defense", ofi_z, intensity_z)

        elif cfg.mode == QuoteMode.PULL and abs_ofi >= cfg.step_enter_z:
            # If configured as legacy PULL mode, step_enter_z acts as full pull trigger
            self.current_state = QuoteState.PULL
            self.fenced_side = candidate_fenced_side
            return StateDecision(
                QuoteState.PULL,
                self.fenced_side,
                f"OFI {ofi_z:+.2f}z exceeded pull gate: cancelled {self.fenced_side} quotes",
                ofi_z,
                intensity_z
            )

        # 3. State: WIDEN (Trade velocity spike without strong directional OFI)
        if intensity_z >= cfg.widen_enter_z:
            self.current_state = QuoteState.WIDEN
            self.fenced_side = None
            return StateDecision(
                QuoteState.WIDEN,
                None,
                f"Trade intensity {intensity_z:+.2f}z elevated: widened both bands +{cfg.widen_extra_bps}bps",
                ofi_z,
                intensity_z
            )

        if self.current_state == QuoteState.WIDEN:
            if intensity_z <= cfg.widen_exit_z:
                self.current_state = QuoteState.QUOTE
                self.fenced_side = None
                self.ticks_in_state = 1
                return StateDecision(QuoteState.QUOTE, None, f"Trade intensity normalized to {intensity_z:+.2f}z", ofi_z, intensity_z)
            else:
                return StateDecision(QuoteState.WIDEN, None, "Maintaining widened spreads during velocity burst", ofi_z, intensity_z)

        # 4. Default: QUOTE (Balanced order flow)
        self.current_state = QuoteState.QUOTE
        self.fenced_side = None
        return StateDecision(QuoteState.QUOTE, None, "Flow balanced: posting symmetric ladder", ofi_z, intensity_z)
