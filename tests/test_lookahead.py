"""
tests/test_lookahead.py — Session 20: Identity-aware lookahead EV projection

11 tests covering immutability, profile effects, stage-specific adjustments,
captain exclusion, volatility/consistency risk, and EV accumulation.
"""
from __future__ import annotations

import pytest

from scoring.engine import Rider, Stage
from scoring.lookahead import LookaheadResult, simulate_lookahead
from scoring.probabilities import RiderProb
from scoring.rider_profiles import RiderProfile


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_rider(holdet_id: str, value: int = 12_000_000) -> Rider:
    return Rider(
        holdet_id=holdet_id,
        person_id=holdet_id,
        team_id="team1",
        name=f"Rider {holdet_id}",
        team="Test Team",
        team_abbr="TT",
        value=value,
        start_value=value,
        points=0,
        status="active",
        gc_position=None,
        jerseys=[],
        in_my_team=False,
        is_captain=False,
    )


def _make_stage(number: int = 1, stage_type: str = "flat") -> Stage:
    return Stage(
        number=number,
        race="giro_2026",
        stage_type=stage_type,
        distance_km=180.0,
        is_ttt=False,
        start_location="A",
        finish_location="B",
    )


def _make_rp(
    rider_id: str,
    p_win: float = 0.10,
    p_top3: float = 0.20,
    p_top10: float = 0.40,
    p_top15: float = 0.50,
    source: str = "model",
) -> RiderProb:
    return RiderProb(
        rider_id=rider_id,
        stage_number=1,
        p_win=p_win,
        p_top3=p_top3,
        p_top10=p_top10,
        p_top15=p_top15,
        p_dnf=0.02,
        source=source,
    )


def _run(
    riders=None,
    stages=None,
    probs=None,
    profiles=None,
    adjustments_by_stage=None,
    horizon=1,
    n_sim=50,
):
    riders = riders or [_make_rider("r1")]
    stages = stages or [_make_stage(1)]
    probs = probs or {"r1": _make_rp("r1")}
    profiles = profiles or {}
    adjustments_by_stage = adjustments_by_stage or {}
    return simulate_lookahead(
        riders=riders,
        stages=stages,
        base_probs=probs,
        profiles=profiles,
        adjustments_by_stage=adjustments_by_stage,
        horizon=horizon,
        n_sim=n_sim,
    )


# ── TestLookaheadDoesNotMutateInputs ─────────────────────────────────────────

class TestLookaheadDoesNotMutateInputs:
    def test_base_probs_not_mutated(self):
        probs = {"r1": _make_rp("r1", p_win=0.10)}
        original_p_win = probs["r1"].p_win
        _run(probs=probs)
        assert probs["r1"].p_win == original_p_win

    def test_adjustments_dict_not_mutated(self):
        adjustments = {1: {"r1": 0.20}}
        original = {1: {"r1": 0.20}}
        _run(adjustments_by_stage=adjustments)
        assert adjustments == original


# ── TestProfilesAffectEVVariance ─────────────────────────────────────────────

class TestProfilesAffectEVVariance:
    def test_high_consistency_gives_higher_ev_than_low_consistency(self):
        # consistency=1.10 amplifies all probs → higher EV than consistency=0.85
        riders = [_make_rider("r1", value=14_000_000)]
        stages = [_make_stage(1, "flat")]
        probs = {"r1": _make_rp("r1", p_win=0.15, p_top3=0.30, p_top10=0.50, p_top15=0.60)}

        high = {"r1": RiderProfile(rider_id="r1", consistency=1.10)}
        low  = {"r1": RiderProfile(rider_id="r1", consistency=0.85)}

        high_results = simulate_lookahead(
            riders=riders, stages=stages, base_probs=probs,
            profiles=high, adjustments_by_stage={}, horizon=1, n_sim=500,
        )
        low_results = simulate_lookahead(
            riders=riders, stages=stages, base_probs=probs,
            profiles=low, adjustments_by_stage={}, horizon=1, n_sim=500,
        )
        assert high_results["r1"].ev_total > low_results["r1"].ev_total


# ── TestAdjustmentsAreStageSpecific ──────────────────────────────────────────

class TestAdjustmentsAreStageSpecific:
    def test_stage1_adjustment_does_not_affect_stage2_ev(self):
        riders = [_make_rider("r1")]
        stages = [_make_stage(1, "flat"), _make_stage(2, "flat")]
        probs = {"r1": _make_rp("r1", p_win=0.10, p_top3=0.20, p_top10=0.40, p_top15=0.50)}

        # Baseline: no adjustments
        base = simulate_lookahead(
            riders=riders, stages=stages, base_probs=probs,
            profiles={}, adjustments_by_stage={}, horizon=2, n_sim=200,
        )

        # Stage 1 boosted +30%, stage 2 untouched
        adj = simulate_lookahead(
            riders=riders, stages=stages, base_probs=probs,
            profiles={}, adjustments_by_stage={1: {"r1": 0.30}}, horizon=2, n_sim=200,
        )

        # Stage 1 EV must be higher in adjusted run
        assert adj["r1"].ev_by_stage[0] > base["r1"].ev_by_stage[0]
        # Stage 2 EV must be identical — no bleed
        assert abs(adj["r1"].ev_by_stage[1] - base["r1"].ev_by_stage[1]) < 1e-6


# ── TestCaptainSelectionNotInLookahead ────────────────────────────────────────

class TestCaptainSelectionNotInLookahead:
    def test_lookahead_result_has_no_captain_field(self):
        result = LookaheadResult(
            rider_id="r1", ev_total=0.0, ev_by_stage=[0.0],
            volatility=0.0, consistency_risk=1.0,
        )
        assert not hasattr(result, "captain_id")
        assert not hasattr(result, "captain")

    def test_lookahead_result_has_correct_fields(self):
        result = LookaheadResult(
            rider_id="r1", ev_total=100.0, ev_by_stage=[50.0, 50.0],
            volatility=0.0, consistency_risk=1.05,
        )
        assert result.ev_total == 100.0
        assert result.ev_by_stage == [50.0, 50.0]
        assert result.volatility == 0.0
        assert result.consistency_risk == 1.05
        assert result.stages_simulated == 2
        assert abs(result.ev_per_stage - 50.0) < 1e-9


# ── TestVolatilityIncreasesWithLowConsistencyProfile ─────────────────────────

class TestVolatilityIncreasesWithLowConsistencyProfile:
    def test_consistency_risk_formula(self):
        # consistency=0.85 → risk = 1 / 0.85 ≈ 1.176
        profile = RiderProfile(rider_id="r1", consistency=0.85)
        expected_risk = 1.0 / 0.85
        assert abs(expected_risk - (1.0 / profile.consistency)) < 1e-9

        results = simulate_lookahead(
            riders=[_make_rider("r1")],
            stages=[_make_stage(1)],
            base_probs={"r1": _make_rp("r1")},
            profiles={"r1": profile},
            adjustments_by_stage={},
            horizon=1,
            n_sim=50,
        )
        assert abs(results["r1"].consistency_risk - expected_risk) < 1e-9

    def test_no_profile_gives_neutral_risk(self):
        # Missing profile → consistency_risk = 1.0 (neutral)
        results = _run()
        assert results["r1"].consistency_risk == 1.0


# ── TestEVAccumulatesAcrossHorizon ────────────────────────────────────────────

class TestEVAccumulatesAcrossHorizon:
    def test_ev_total_equals_sum_of_ev_by_stage(self):
        riders = [_make_rider("r1")]
        stages = [_make_stage(1, "flat"), _make_stage(2, "flat"), _make_stage(3, "flat")]
        probs = {"r1": _make_rp("r1")}

        results = simulate_lookahead(
            riders=riders, stages=stages, base_probs=probs,
            profiles={}, adjustments_by_stage={}, horizon=3, n_sim=100,
        )
        lr = results["r1"]
        assert abs(lr.ev_total - sum(lr.ev_by_stage)) < 1e-6

    def test_horizon_1_volatility_is_zero(self):
        results = _run(horizon=1)
        assert results["r1"].volatility == 0.0

    def test_horizon_2_ev_geq_horizon_1_ev(self):
        # A high-value sprinter on flat stages should have positive EV per stage
        riders = [_make_rider("r1", value=14_000_000)]
        stages = [_make_stage(1, "flat"), _make_stage(2, "flat")]
        probs = {"r1": _make_rp("r1", p_win=0.20, p_top3=0.40, p_top10=0.60, p_top15=0.70)}

        h1 = simulate_lookahead(
            riders=riders, stages=stages, base_probs=probs,
            profiles={}, adjustments_by_stage={}, horizon=1, n_sim=200,
        )
        h2 = simulate_lookahead(
            riders=riders, stages=stages, base_probs=probs,
            profiles={}, adjustments_by_stage={}, horizon=2, n_sim=200,
        )
        assert h2["r1"].ev_total >= h1["r1"].ev_total - 1000  # positive-EV stages accumulate
