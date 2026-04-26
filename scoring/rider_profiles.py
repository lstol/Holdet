"""
scoring/rider_profiles.py — Static rider identity profiles.

Rider profiles are structural bias signals, not learned parameters.
They do not update from outcomes, calibration, or odds.
They are static multipliers applied AFTER user adjustments, BEFORE simulation.
They MUST NOT affect ROLE_TOP15 or calibration outputs.
"""
from __future__ import annotations

from dataclasses import dataclass, field

MAX_BIAS = 1.15
MIN_BIAS = 0.85
MAX_CONSISTENCY = 1.20
MIN_CONSISTENCY = 0.80


@dataclass
class RiderProfile:
    rider_id: str
    sprint_bias: float = 1.0
    gc_bias: float = 1.0
    climb_bias: float = 1.0
    consistency: float = 1.0  # dampens or amplifies all probs uniformly

    def clamp(self) -> None:
        self.sprint_bias  = min(max(self.sprint_bias,  MIN_BIAS), MAX_BIAS)
        self.gc_bias      = min(max(self.gc_bias,      MIN_BIAS), MAX_BIAS)
        self.climb_bias   = min(max(self.climb_bias,   MIN_BIAS), MAX_BIAS)
        self.consistency  = min(max(self.consistency,  MIN_CONSISTENCY), MAX_CONSISTENCY)
