"""
tests/test_probability_shaper.py — Session 21: Unified probability shaping layer.

5 tests covering stage-role multiplier, profile consistency, cross-role alignment,
and ordering invariant enforcement.
"""
from __future__ import annotations

import pytest

from scoring.engine import Stage
from scoring.probabilities import RiderProb, RiderRole
from scoring.rider_profiles import RiderProfile
from scoring.probability_shaper import (
    ProbabilityContext,
    apply_probability_shaping,
    STAGE_ROLE_MULTIPLIER,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _flat_stage() -> Stage:
    return Stage(
        number=1,
        race="test",
        stage_type="flat",
        distance_km=180.0,
        is_ttt=False,
        start_location="A",
        finish_location="B",
        sprint_points=[],
        kom_points=[],
        notes="",
    )


def _mountain_stage() -> Stage:
    return Stage(
        number=2,
        race="test",
        stage_type="mountain",
        distance_km=160.0,
        is_ttt=False,
        start_location="C",
        finish_location="D",
        sprint_points=[],
        kom_points=[],
        notes="",
    )


def _make_rp(
    rider_id: str,
    p_win: float = 0.10,
    p_top3: float = 0.20,
    p_top10: float = 0.40,
    p_top15: float = 0.50,
) -> RiderProb:
    return RiderProb(
        rider_id=rider_id,
        stage_number=1,
        p_win=p_win,
        p_top3=p_top3,
        p_top10=p_top10,
        p_top15=p_top15,
        p_dnf=0.02,
        source="model",
    )


def _empty_ctx(stage: Stage, role_map: dict) -> ProbabilityContext:
    return ProbabilityContext(
        stage=stage,
        rider_profiles={},
        rider_roles=role_map,
        rider_adjustments={},
        odds_signal=None,
        intelligence_signals=None,
        user_expertise_weights=None,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestProbabilityShaper:

    def test_probability_shaper_applies_role_multiplier_sprint(self):
        """Flat stage: sprinter p_win increases, climber p_win decreases."""
        stage = _flat_stage()
        probs = {
            "s1": _make_rp("s1"),   # sprinter
            "c1": _make_rp("c1"),   # climber
        }
        ctx = _empty_ctx(stage, {"s1": RiderRole.SPRINTER, "c1": RiderRole.CLIMBER})
        shaped, trace = apply_probability_shaping(probs, ctx)

        sprinter_mult = STAGE_ROLE_MULTIPLIER["sprinter"]["flat"]    # 1.25
        climber_mult  = STAGE_ROLE_MULTIPLIER["climber"]["flat"]     # 0.65

        assert shaped["s1"].p_win > probs["s1"].p_win, "Sprinter p_win should increase on flat stage"
        assert shaped["c1"].p_win < probs["c1"].p_win, "Climber p_win should decrease on flat stage"
        assert abs(shaped["s1"].p_win - probs["s1"].p_win * sprinter_mult) < 1e-6
        assert abs(shaped["c1"].p_win - probs["c1"].p_win * climber_mult) < 1e-6

    def test_probability_shaper_applies_profile_consistency(self):
        """consistency=0.80 reduces all four fields proportionally."""
        stage = _flat_stage()
        rp = _make_rp("r1", p_win=0.10, p_top3=0.20, p_top10=0.40, p_top15=0.50)
        probs = {"r1": rp}

        profile = RiderProfile(rider_id="r1", consistency=0.80)
        role_mult = STAGE_ROLE_MULTIPLIER[RiderRole.SPRINTER]["flat"]  # 1.25

        ctx = ProbabilityContext(
            stage=stage,
            rider_profiles={"r1": profile},
            rider_roles={"r1": RiderRole.SPRINTER},
            rider_adjustments={},
            odds_signal=None,
            intelligence_signals=None,
        )
        shaped, trace = apply_probability_shaping(probs, ctx)

        # Layer 1 (role): p_win *= 1.25 → 0.125
        # Layer 2 (profile): p_win *= sprint_bias(1.0) × consistency(0.80) → 0.100
        expected_p_win = rp.p_win * role_mult * 1.0 * 0.80
        assert abs(shaped["r1"].p_win - expected_p_win) < 1e-6
        assert "profile" in shaped["r1"].source

    def test_probability_shaper_stage_role_alignment_sprint(self):
        """Flat stage: after shaping, sprinter p_top15 > climber p_top15 when starting equal."""
        stage = _flat_stage()
        probs = {
            "s1": _make_rp("s1", p_win=0.05, p_top3=0.10, p_top10=0.20, p_top15=0.30),
            "c1": _make_rp("c1", p_win=0.05, p_top3=0.10, p_top10=0.20, p_top15=0.30),
        }
        ctx = _empty_ctx(stage, {"s1": RiderRole.SPRINTER, "c1": RiderRole.CLIMBER})
        shaped, _ = apply_probability_shaping(probs, ctx)

        assert shaped["s1"].p_top15 > shaped["c1"].p_top15, (
            "After flat-stage shaping, sprinter p_top15 must exceed climber p_top15"
        )

    def test_probability_shaper_ordering_invariant_enforced(self):
        """After shaping, p_win ≤ p_top3 ≤ p_top10 ≤ p_top15 for all riders."""
        stage = _mountain_stage()
        probs = {
            "r1": _make_rp("r1", p_win=0.35, p_top3=0.40, p_top10=0.60, p_top15=0.70),
            "r2": _make_rp("r2", p_win=0.01, p_top3=0.02, p_top10=0.05, p_top15=0.10),
        }
        role_map = {"r1": RiderRole.CLIMBER, "r2": RiderRole.SPRINTER}
        ctx = _empty_ctx(stage, role_map)
        shaped, _ = apply_probability_shaping(probs, ctx)

        for rid, rp in shaped.items():
            assert rp.p_win <= rp.p_top3, f"{rid}: p_win > p_top3"
            assert rp.p_top3 <= rp.p_top10, f"{rid}: p_top3 > p_top10"
            assert rp.p_top10 <= rp.p_top15, f"{rid}: p_top10 > p_top15"
            assert 0.0 <= rp.p_win <= 1.0, f"{rid}: p_win out of [0,1]"
            assert 0.0 <= rp.p_top15 <= 1.0, f"{rid}: p_top15 out of [0,1]"

    def test_probability_shaper_does_not_mutate_input(self):
        """apply_probability_shaping returns a new dict — original probs unchanged."""
        stage = _flat_stage()
        rp_orig = _make_rp("r1", p_win=0.10)
        probs = {"r1": rp_orig}
        ctx = _empty_ctx(stage, {"r1": RiderRole.SPRINTER})

        original_p_win = probs["r1"].p_win
        shaped, _ = apply_probability_shaping(probs, ctx)

        assert probs["r1"].p_win == original_p_win, "Input probs must not be mutated"
        assert shaped is not probs, "Returned dict must be a new object"
