"""
tests/test_stage_intent.py — Session 18: ICDL v1

Tests for compute_stage_intent(), apply_intelligence_signals(),
apply_intent_to_ev(), and compute_transfer_penalty().
"""
from __future__ import annotations

import pytest
from scoring.engine import Rider, Stage
from scoring.stage_intent import (
    StageIntent,
    compute_stage_intent,
    apply_intelligence_signals,
    SIGNAL_INTENT_DELTAS,
    SIGNAL_ALIASES,
)
from scoring.optimizer import apply_intent_to_ev, compute_transfer_penalty


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_stage(stage_type: str, number: int = 1) -> Stage:
    return Stage(
        number=number,
        race="giro_2026",
        stage_type=stage_type,
        distance_km=180.0,
        is_ttt=stage_type == "ttt",
        start_location="A",
        finish_location="B",
    )


def _make_rider(holdet_id: str, gc_position=None) -> Rider:
    return Rider(
        holdet_id=holdet_id,
        person_id=holdet_id,
        team_id="team1",
        name=f"Rider {holdet_id}",
        team="Test Team",
        team_abbr="TT",
        value=10_000_000,
        start_value=10_000_000,
        points=0,
        status="active",
        gc_position=gc_position,
        jerseys=[],
        in_my_team=False,
        is_captain=False,
    )


# ── TestComputeStageIntent ────────────────────────────────────────────────────

class TestComputeStageIntent:
    def test_flat_stage_high_win_priority(self):
        stage = _make_stage("flat")
        intent = compute_stage_intent(stage, {}, next_stage=None, riders=[])
        assert intent.win_priority >= 0.85

    def test_mountain_stage_high_survival(self):
        stage = _make_stage("mountain")
        intent = compute_stage_intent(stage, {}, next_stage=None, riders=[])
        assert intent.survival_priority >= 0.85

    def test_itt_low_breakaway(self):
        stage = _make_stage("itt")
        intent = compute_stage_intent(stage, {}, next_stage=None, riders=[])
        assert intent.breakaway_likelihood <= 0.10

    def test_ttt_max_team_bonus(self):
        stage = _make_stage("ttt")
        intent = compute_stage_intent(stage, {}, next_stage=None, riders=[])
        assert intent.team_bonus_value == 1.0

    def test_next_mountain_increases_transfer_pressure(self):
        flat_stage = _make_stage("flat", number=1)
        mtn_stage = _make_stage("mountain", number=2)
        without = compute_stage_intent(flat_stage, {}, next_stage=None, riders=[])
        with_ = compute_stage_intent(flat_stage, {}, next_stage=mtn_stage, riders=[])
        assert with_.transfer_pressure > without.transfer_pressure

    def test_next_flat_increases_transfer_pressure_on_mountain(self):
        mtn_stage = _make_stage("mountain", number=1)
        flat_next = _make_stage("flat", number=2)
        without = compute_stage_intent(mtn_stage, {}, next_stage=None, riders=[])
        with_ = compute_stage_intent(mtn_stage, {}, next_stage=flat_next, riders=[])
        assert with_.transfer_pressure > without.transfer_pressure

    def test_tight_gc_increases_survival_on_mountain(self):
        stage = _make_stage("mountain")
        # 3 riders with gc_position <= 5
        gc_positions = {"r1": 1, "r2": 3, "r3": 5}
        without = compute_stage_intent(stage, {}, next_stage=None, riders=[])
        with_ = compute_stage_intent(stage, gc_positions, next_stage=None, riders=[])
        assert with_.survival_priority > without.survival_priority

    def test_tight_gc_does_not_affect_flat_survival(self):
        stage = _make_stage("flat")
        gc_positions = {"r1": 1, "r2": 3, "r3": 5}
        without = compute_stage_intent(stage, {}, next_stage=None, riders=[])
        with_ = compute_stage_intent(stage, gc_positions, next_stage=None, riders=[])
        assert with_.survival_priority == without.survival_priority

    def test_all_fields_clamped_to_unit_interval(self):
        # Stack all modifiers: tight GC on mountain, next=flat, sprinter-dense pool
        stage = _make_stage("mountain")
        next_s = _make_stage("flat")
        gc_pos = {"r1": 1, "r2": 2, "r3": 3, "r4": 4, "r5": 5}
        # All riders have no gc_position (sprinter-dense)
        riders = [_make_rider(f"r{i}") for i in range(10)]
        intent = compute_stage_intent(stage, gc_pos, next_stage=next_s, riders=riders)
        for field_name in ("win_priority", "survival_priority", "transfer_pressure",
                           "team_bonus_value", "breakaway_likelihood"):
            v = getattr(intent, field_name)
            assert 0.0 <= v <= 1.0, f"{field_name}={v} out of [0,1]"

    def test_sprinter_dense_pool_raises_breakaway_on_flat(self):
        stage = _make_stage("flat")
        # >30% riders have gc_position=None
        riders = [_make_rider(f"r{i}") for i in range(10)]  # all None
        without = compute_stage_intent(stage, {}, next_stage=None, riders=[])
        with_ = compute_stage_intent(stage, {}, next_stage=None, riders=riders)
        assert with_.breakaway_likelihood > without.breakaway_likelihood

    def test_hilly_base_values(self):
        stage = _make_stage("hilly")
        intent = compute_stage_intent(stage, {}, next_stage=None, riders=[])
        assert abs(intent.win_priority - 0.75) < 1e-9
        assert abs(intent.survival_priority - 0.55) < 1e-9
        assert abs(intent.breakaway_likelihood - 0.45) < 1e-9


# ── TestApplyIntelligenceOverrides ────────────────────────────────────────────

class TestApplyIntelligenceOverrides:
    def _base_intent(self) -> StageIntent:
        return compute_stage_intent(_make_stage("flat"), {}, next_stage=None, riders=[])

    def test_crosswind_raises_breakaway_and_survival(self):
        intent = self._base_intent()
        updated = apply_intelligence_signals(intent, {"crosswind_risk": "high"})
        assert updated.breakaway_likelihood > intent.breakaway_likelihood
        assert updated.survival_priority > intent.survival_priority

    def test_sprint_disruption_lowers_win_priority(self):
        intent = self._base_intent()
        updated = apply_intelligence_signals(intent, {"sprint_train_disruption": "likely"})
        assert updated.win_priority < intent.win_priority

    def test_gc_illness_raises_survival_and_transfer_pressure(self):
        intent = self._base_intent()
        updated = apply_intelligence_signals(intent, {"gc_rider_illness": "confirmed"})
        assert updated.survival_priority > intent.survival_priority
        assert updated.transfer_pressure > intent.transfer_pressure

    def test_stage_shortened_lowers_team_bonus(self):
        intent = self._base_intent()
        updated = apply_intelligence_signals(intent, {"stage_shortened": "confirmed"})
        assert updated.team_bonus_value < intent.team_bonus_value

    def test_unknown_signal_ignored_not_raised(self):
        intent = self._base_intent()
        # Should not raise; unknown key is ignored
        updated = apply_intelligence_signals(intent, {"totally_unknown": "signal"})
        # Fields unchanged
        assert updated.win_priority == intent.win_priority
        assert updated.breakaway_likelihood == intent.breakaway_likelihood

    def test_returns_new_intent_does_not_mutate_original(self):
        intent = self._base_intent()
        original_breakaway = intent.breakaway_likelihood
        apply_intelligence_signals(intent, {"crosswind_risk": "high"})
        # Original unchanged (frozen dataclass, but let's verify)
        assert intent.breakaway_likelihood == original_breakaway

    def test_fields_clamped_after_override(self):
        # Stack multiple high signals — cannot push above 1.0
        intent = _make_stage("mountain")
        base = compute_stage_intent(intent, {}, next_stage=None, riders=[])
        # Apply all 4 signals at once
        all_signals = {
            "crosswind_risk": "high",
            "sprint_train_disruption": "likely",
            "gc_rider_illness": "confirmed",
            "stage_shortened": "confirmed",
        }
        updated = apply_intelligence_signals(base, all_signals)
        for field_name in ("win_priority", "survival_priority", "transfer_pressure",
                           "team_bonus_value", "breakaway_likelihood"):
            v = getattr(updated, field_name)
            assert 0.0 <= v <= 1.0, f"{field_name}={v} out of [0,1] after stacked signals"

    def test_all_four_signals_covered_in_delta_map(self):
        expected = {
            "crosswind_risk:high",
            "sprint_train_disruption:likely",
            "gc_rider_illness:confirmed",
            "stage_shortened:confirmed",
        }
        assert expected.issubset(set(SIGNAL_INTENT_DELTAS.keys()))


# ── TestIntentEVFunctions ─────────────────────────────────────────────────────

class TestIntentEVFunctions:
    def _intent(self, win_priority: float = 0.5, transfer_pressure: float = 0.5) -> StageIntent:
        return StageIntent(
            win_priority=win_priority,
            survival_priority=0.5,
            transfer_pressure=transfer_pressure,
            team_bonus_value=0.5,
            breakaway_likelihood=0.5,
        )

    def test_apply_intent_to_ev_scales_with_win_priority_max(self):
        intent = self._intent(win_priority=1.0)
        base_ev = 100_000.0
        adjusted = apply_intent_to_ev(base_ev, intent)
        assert abs(adjusted - base_ev * 1.3) < 1.0

    def test_apply_intent_to_ev_win_priority_zero_unchanged(self):
        intent = self._intent(win_priority=0.0)
        base_ev = 100_000.0
        adjusted = apply_intent_to_ev(base_ev, intent)
        assert abs(adjusted - base_ev) < 1.0

    def test_compute_transfer_penalty_scales_with_pressure_max(self):
        intent = self._intent(transfer_pressure=1.0)
        fee = 50_000
        penalty = compute_transfer_penalty(fee, intent)
        assert abs(penalty - 2.0 * fee) < 1.0

    def test_compute_transfer_penalty_zero_pressure(self):
        intent = self._intent(transfer_pressure=0.0)
        fee = 50_000
        penalty = compute_transfer_penalty(fee, intent)
        assert abs(penalty - fee) < 1.0

    def test_lambda_zero_means_no_next_stage_contribution(self):
        # net_ev = adjusted_ev - penalty + 0.0 * next_ev == adjusted_ev - penalty
        intent = self._intent(win_priority=0.0, transfer_pressure=0.0)
        base_ev = 80_000.0
        fee = 10_000
        next_ev = 999_999.0  # irrelevant when lambda=0
        lambda_val = 0.0
        adjusted_ev = apply_intent_to_ev(base_ev, intent)   # = base_ev (win_priority=0)
        penalty = compute_transfer_penalty(fee, intent)       # = fee (pressure=0)
        net_ev = adjusted_ev - penalty + lambda_val * next_ev
        assert abs(net_ev - (base_ev - fee)) < 1.0


# ── TestSignalAliasesAndNormalization (18F) ───────────────────────────────────

class TestSignalAliasesAndNormalization:
    def _flat_intent(self) -> StageIntent:
        return compute_stage_intent(_make_stage("flat"), {}, next_stage=None, riders=[])

    def test_sprint_disruption_alias_resolves_correctly(self):
        intent = self._flat_intent()
        via_alias = apply_intelligence_signals(intent, {"sprint_disruption": "likely"})
        via_canonical = apply_intelligence_signals(intent, {"sprint_train_disruption": "likely"})
        assert via_alias == via_canonical

    def test_gc_illness_alias_resolves_correctly(self):
        intent = self._flat_intent()
        via_alias = apply_intelligence_signals(intent, {"gc_illness": "confirmed"})
        via_canonical = apply_intelligence_signals(intent, {"gc_rider_illness": "confirmed"})
        assert via_alias == via_canonical

    def test_unknown_alias_still_warns_not_raises(self):
        intent = self._flat_intent()
        # Must not raise; unknown key after alias resolution → WARNING
        updated = apply_intelligence_signals(intent, {"completely_unknown": "value"})
        assert updated == intent  # no changes applied

    def test_value_casing_normalized(self):
        intent = self._flat_intent()
        lower = apply_intelligence_signals(intent, {"crosswind_risk": "high"})
        upper = apply_intelligence_signals(intent, {"crosswind_risk": "HIGH"})
        mixed = apply_intelligence_signals(intent, {"crosswind_risk": "High"})
        assert lower == upper == mixed


# ── TestIntentImmutability (18L) ──────────────────────────────────────────────

class TestIntentImmutability:
    def test_apply_intelligence_signals_does_not_mutate_original(self):
        """
        apply_intelligence_signals() must return a new StageIntent.
        The original must be identical to a freshly computed intent
        (no shared mutable state, no caching side effects).
        """
        stage = Stage(
            number=3, race="giro_2026", stage_type="flat",
            distance_km=180, is_ttt=False,
            start_location="A", finish_location="B",
        )
        base = compute_stage_intent(stage, gc_positions={}, next_stage=None, riders=[])
        signals = {"crosswind_risk": "high"}

        modified = apply_intelligence_signals(base, signals)

        assert base is not modified
        assert base.breakaway_likelihood != modified.breakaway_likelihood
        recomputed = compute_stage_intent(stage, gc_positions={}, next_stage=None, riders=[])
        assert base == recomputed        # no side effects on base
        assert base is not recomputed   # no shared instance / caching
