"""
scoring/stage_intent.py — Intelligence-Conditioned Decision Layer (ICDL v1).

Deterministic stage intent computation. No randomness. Fully testable.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from scoring.engine import Stage

logger = logging.getLogger(__name__)


# Canonical field order — use this everywhere fields are iterated.
# Import and reference this constant; never hardcode a different order.
INTENT_FIELDS: list[str] = [
    "win_priority",
    "survival_priority",
    "transfer_pressure",
    "team_bonus_value",
    "breakaway_likelihood",
]


@dataclass(frozen=True)
class StageIntent:
    win_priority: float         # 0–1: how much does winning matter today?
    survival_priority: float    # 0–1: how bad is a DNF / gruppo risk?
    transfer_pressure: float    # 0–1: how urgently should we rotate?
    team_bonus_value: float     # 0–1: is holding a full team worth it today?
    breakaway_likelihood: float # 0–1: chance of a small-group finish


# Base intent values by stage type
_BASE_INTENT: dict[str, dict[str, float]] = {
    "flat":     {"win_priority": 0.90, "survival_priority": 0.25, "transfer_pressure": 0.40, "team_bonus_value": 0.80, "breakaway_likelihood": 0.20},
    "hilly":    {"win_priority": 0.75, "survival_priority": 0.55, "transfer_pressure": 0.55, "team_bonus_value": 0.60, "breakaway_likelihood": 0.45},
    "mountain": {"win_priority": 0.70, "survival_priority": 0.90, "transfer_pressure": 0.70, "team_bonus_value": 0.20, "breakaway_likelihood": 0.55},
    "itt":      {"win_priority": 0.85, "survival_priority": 0.20, "transfer_pressure": 0.30, "team_bonus_value": 0.10, "breakaway_likelihood": 0.05},
    "ttt":      {"win_priority": 0.60, "survival_priority": 0.50, "transfer_pressure": 0.20, "team_bonus_value": 1.00, "breakaway_likelihood": 0.00},
}


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def compute_stage_intent(
    stage: Stage,
    gc_positions: dict,      # holdet_id → gc_position (int), may be empty
    next_stage: Optional[Stage],
    riders: list,            # full rider list for pool structure analysis
) -> StageIntent:
    """
    Compute a deterministic StageIntent from stage context.

    Modifiers applied additively on top of base values, all clamped to [0.0, 1.0].
    """
    stage_type = stage.stage_type if stage.stage_type in _BASE_INTENT else "flat"
    base = dict(_BASE_INTENT[stage_type])

    # gc_spread_tight: ≥3 riders with gc_position <= 5 → contested GC
    tight_gc_count = sum(1 for pos in gc_positions.values() if pos is not None and pos <= 5)
    if tight_gc_count >= 3 and stage_type == "mountain":
        base["survival_priority"] += 0.10
        base["win_priority"] += 0.05

    # next_stage_is_mountain: current is flat/hilly → upgrade window
    if next_stage is not None and next_stage.stage_type == "mountain":
        if stage_type in ("flat", "hilly"):
            base["transfer_pressure"] += 0.20

    # next_stage_is_flat: current is mountain → sprinters coming back
    if next_stage is not None and next_stage.stage_type == "flat":
        if stage_type == "mountain":
            base["transfer_pressure"] += 0.15

    # sprinter_dense_pool: >30% of riders have gc_position is None → sprinter/dom heavy
    if riders:
        none_gc = sum(1 for r in riders if r.gc_position is None)
        if none_gc / len(riders) > 0.30 and stage_type == "flat":
            base["breakaway_likelihood"] += 0.05

    return StageIntent(
        win_priority=_clamp(base["win_priority"]),
        survival_priority=_clamp(base["survival_priority"]),
        transfer_pressure=_clamp(base["transfer_pressure"]),
        team_bonus_value=_clamp(base["team_bonus_value"]),
        breakaway_likelihood=_clamp(base["breakaway_likelihood"]),
    )


# ── Signal → intent delta map ─────────────────────────────────────────────────

SIGNAL_INTENT_DELTAS: dict[str, dict[str, float]] = {
    "crosswind_risk:high": {
        "breakaway_likelihood": +0.25,
        "survival_priority": +0.15,
    },
    "sprint_train_disruption:likely": {
        "breakaway_likelihood": +0.20,
        "win_priority": -0.15,
    },
    "gc_rider_illness:confirmed": {
        "survival_priority": +0.20,
        "transfer_pressure": +0.25,
    },
    "stage_shortened:confirmed": {
        "team_bonus_value": -0.30,
    },
}

# Alias map — short user-facing keys → canonical SIGNAL_INTENT_DELTAS keys.
SIGNAL_ALIASES: dict[str, str] = {
    "sprint_disruption": "sprint_train_disruption",
    "gc_illness": "gc_rider_illness",
}


def apply_intelligence_signals(
    intent: StageIntent,
    signals: dict,
) -> StageIntent:
    """
    Apply event signals to a StageIntent.

    `signals` format:
    {
        "crosswind_risk": "high",
        "sprint_train_disruption": "likely"
    }

    Each key:value pair is looked up in SIGNAL_INTENT_DELTAS as "key:value".
    Keys are resolved through SIGNAL_ALIASES before lookup.
    Values are lowercased before lookup (case-insensitive).
    Unknown signal keys (after alias resolution) are ignored (logged at WARNING).
    All resulting field values are clamped to [0.0, 1.0].

    Returns a NEW StageIntent (original is not mutated).
    """
    fields = {f: getattr(intent, f) for f in INTENT_FIELDS}

    for key, value in signals.items():
        k_norm = SIGNAL_ALIASES.get(key, key)       # resolve alias
        v_norm = str(value).lower()                  # normalize value casing
        canonical_key = f"{k_norm}:{v_norm}"
        if canonical_key not in SIGNAL_INTENT_DELTAS:
            logger.warning("Unknown intelligence signal '%s' — ignored", canonical_key)
            continue
        for field_name, delta in SIGNAL_INTENT_DELTAS[canonical_key].items():
            fields[field_name] = fields[field_name] + delta

    return StageIntent(
        win_priority=_clamp(fields["win_priority"]),
        survival_priority=_clamp(fields["survival_priority"]),
        transfer_pressure=_clamp(fields["transfer_pressure"]),
        team_bonus_value=_clamp(fields["team_bonus_value"]),
        breakaway_likelihood=_clamp(fields["breakaway_likelihood"]),
    )
