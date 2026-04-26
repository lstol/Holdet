"""
tests/test_decision_trace.py — Session 22.5: Decision traceability layer.

7 tests covering ablation correctness, reproducibility, captain exclusion,
intent invariant, flip threshold formula, captain trace structure,
and contributor label validation.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import pytest

from scoring.engine import Stage
from scoring.probabilities import RiderProb, RiderRole
from scoring.probability_shaper import ProbabilityContext
from scoring.decision_trace import (
    DecisionTrace,
    ablation_run,
    build_decision_traces,
    build_contributors,
    validate_contributor_label,
    ALLOWED_EFFECT_ENUMS,
)
from scoring.captain_selector import select_captain, LAMBDA
from scoring.simulator import simulate_all_riders


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _flat_stage() -> Stage:
    return Stage(
        number=1, race="test", stage_type="flat", distance_km=180.0,
        is_ttt=False, start_location="A", finish_location="B",
        sprint_points=[], kom_points=[], notes="",
    )


def _make_rp(rider_id: str, p_win: float = 0.05) -> RiderProb:
    return RiderProb(
        rider_id=rider_id, stage_number=1,
        p_win=p_win, p_top3=p_win * 2, p_top10=p_win * 4, p_top15=p_win * 5,
        p_dnf=0.02, source="model",
    )


def _make_rider(rider_id: str):
    from scoring.engine import Rider
    return Rider(
        holdet_id=rider_id, person_id=rider_id, team_id="ta",
        name=f"Rider {rider_id}", team="TeamA", team_abbr="TA",
        value=5_000_000, start_value=5_000_000, points=0,
        status="active", gc_position=None, jerseys=[],
        in_my_team=False, is_captain=False,
    )


def _make_ctx(stage: Stage, variance_mode: str = "balanced") -> ProbabilityContext:
    return ProbabilityContext(
        stage=stage, rider_profiles={},
        rider_roles={"r1": "domestique", "r2": "domestique"},
        rider_adjustments={}, odds_signal=None,
        intelligence_signals=None, user_expertise_weights=None,
        variance_mode=variance_mode,
    )


@dataclass
class _SimResult:
    rider_id: str
    expected_value: float
    std_dev: float = 0.0
    percentile_10: float = 0.0
    percentile_50: float = 0.0
    percentile_80: float = 0.0
    percentile_90: float = 0.0
    percentile_95: float = 0.0
    p_positive: float = 0.0


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestDecisionTrace:

    def test_ablation_variance_produces_nonzero_delta(self):
        """With aggressive mode, at least one rider has variance_adjustment != 0.0."""
        stage = _flat_stage()
        riders = [_make_rider("r1"), _make_rider("r2")]
        raw_probs = {"r1": _make_rp("r1", 0.10), "r2": _make_rp("r2", 0.05)}

        ctx_aggressive = _make_ctx(stage, variance_mode="aggressive")
        from scoring.probability_shaper import apply_probability_shaping
        probs_full, _ = apply_probability_shaping(raw_probs, ctx_aggressive)
        ev_full_results = simulate_all_riders(riders, stage, probs_full, my_team=[], captain="", seed=42)
        ev_full = {rid: sr.expected_value for rid, sr in ev_full_results.items()}

        traces = build_decision_traces(riders, stage, raw_probs, ctx_aggressive, ev_full, seed=42)

        nonzero = [rid for rid, dt in traces.items() if abs(dt.variance_adjustment) > 1e-6]
        assert nonzero, "aggressive mode must produce at least one nonzero variance_adjustment"

    def test_ablation_reproducibility_same_seed(self):
        """ablation_run() with seed=42 returns identical results on two calls."""
        stage = _flat_stage()
        riders = [_make_rider("r1"), _make_rider("r2")]
        raw_probs = {"r1": _make_rp("r1"), "r2": _make_rp("r2")}
        ctx = _make_ctx(stage)

        run1 = ablation_run(riders, stage, raw_probs, ctx, {"variance": True}, seed=42)
        run2 = ablation_run(riders, stage, raw_probs, ctx, {"variance": True}, seed=42)

        assert run1.keys() == run2.keys()
        for rid in run1:
            assert run1[rid] == run2[rid], (
                f"ablation_run not deterministic for {rid}: {run1[rid]} != {run2[rid]}"
            )

    def test_ablation_captain_excluded(self):
        """ablation_run() matches simulate_all_riders with my_team=[], captain=""."""
        stage = _flat_stage()
        riders = [_make_rider("r1"), _make_rider("r2")]
        raw_probs = {"r1": _make_rp("r1"), "r2": _make_rp("r2")}
        ctx = _make_ctx(stage)

        ablation_ev = ablation_run(riders, stage, raw_probs, ctx, {}, seed=42)

        from scoring.probability_shaper import apply_probability_shaping
        probs_shaped, _ = apply_probability_shaping(raw_probs, ctx)
        direct_results = simulate_all_riders(
            riders, stage, probs_shaped, my_team=[], captain="", seed=42,
        )
        direct_ev = {rid: sr.expected_value for rid, sr in direct_results.items()}

        for rid in ablation_ev:
            assert ablation_ev[rid] == direct_ev[rid], (
                f"ablation_run must match simulate_all_riders(captain='') for {rid}"
            )

    def test_intent_adjustment_is_always_zero(self):
        """DecisionTrace.intent_adjustment is always 0.0 (reserved for Session 23)."""
        stage = _flat_stage()
        riders = [_make_rider("r1")]
        raw_probs = {"r1": _make_rp("r1")}
        ctx = _make_ctx(stage)

        from scoring.probability_shaper import apply_probability_shaping
        probs_shaped, _ = apply_probability_shaping(raw_probs, ctx)
        ev_full_results = simulate_all_riders(riders, stage, probs_shaped, my_team=[], captain="", seed=42)
        ev_full = {rid: sr.expected_value for rid, sr in ev_full_results.items()}

        traces = build_decision_traces(riders, stage, raw_probs, ctx, ev_full, seed=42)

        for rid, dt in traces.items():
            assert dt.intent_adjustment == 0.0, (
                f"{rid}: intent_adjustment must be 0.0 in Session 22.5, got {dt.intent_adjustment}"
            )

    def test_flip_threshold_matches_analytic_solution(self):
        """score_gap == (EV_A - EV_B) + λ * (P_A - P_B) exactly."""
        team = ["a", "b"]
        ev_a, ev_b = 100.0, 90.0
        p_win_a, p_win_b = 0.30, 0.10
        lam = LAMBDA["balanced"]  # 0.5

        probs = {
            "a": _make_rp("a", p_win=p_win_a),
            "b": _make_rp("b", p_win=p_win_b),
        }
        sim_results = {
            "a": _SimResult(rider_id="a", expected_value=ev_a),
            "b": _SimResult(rider_id="b", expected_value=ev_b),
        }

        _, _, _, flip_threshold = select_captain(team, probs, sim_results, mode="balanced")

        expected_D = (ev_a - ev_b) + lam * (p_win_a - p_win_b)
        assert flip_threshold is not None
        assert abs(flip_threshold["score_gap"] - expected_D) < 1e-9, (
            f"score_gap {flip_threshold['score_gap']} != expected {expected_D}"
        )
        assert flip_threshold["interpretation"] == "A wins if score_gap > 0"

    def test_captain_trace_has_exactly_three_numeric_components(self):
        """captain_trace has exactly ev_component, p_win_component, final_score (plus mode + lambda)."""
        team = ["r1", "r2"]
        probs = {"r1": _make_rp("r1", 0.10), "r2": _make_rp("r2", 0.05)}
        sim_results = {
            "r1": _SimResult(rider_id="r1", expected_value=50_000.0),
            "r2": _SimResult(rider_id="r2", expected_value=30_000.0),
        }

        _, _, captain_trace, _ = select_captain(team, probs, sim_results, mode="balanced")

        numeric_keys = {"ev_component", "p_win_component", "final_score"}
        all_keys = set(captain_trace.keys())
        assert numeric_keys <= all_keys, f"Missing keys: {numeric_keys - all_keys}"
        # Only these 3 numeric fields (plus mode and lambda)
        extra_numeric = {k for k in all_keys if k not in {"mode", "lambda"} and k not in numeric_keys}
        assert not extra_numeric, f"Unexpected numeric fields: {extra_numeric}"
        # Exact equality: final_score == ev_component + p_win_component
        assert captain_trace["final_score"] == captain_trace["ev_component"] + captain_trace["p_win_component"]

    def test_no_free_text_in_api_contributors(self):
        """All contributor labels are valid (rider name, effect enum, or scenario key)."""
        rider_names = {"Vingegaard", "Pogacar", "Evenepoel"}
        scenario_keys = {"bunch_sprint", "breakaway", "gc_day"}

        # Valid scenario key label — should not raise
        validate_contributor_label("bunch_sprint", rider_names, scenario_keys)

        # Valid rider name — should not raise
        validate_contributor_label("Vingegaard", rider_names, scenario_keys)

        # Valid effect enum — should not raise
        validate_contributor_label("team_bonus", rider_names, scenario_keys)

        # Invalid label (sentence-like, > 40 chars) — must raise
        with pytest.raises(ValueError, match="Invalid contributor label"):
            validate_contributor_label(
                "This is a long narrative explanation of team performance",
                rider_names, scenario_keys,
            )

        # build_contributors with scenario_priors=None omits scenario_contributions
        @dataclass
        class _SR:
            expected_value: float
        team = ["r1", "r2"]
        sim_results = {"r1": _SR(50_000.0), "r2": _SR(30_000.0)}
        rider_name_map = {"r1": "Vingegaard", "r2": "Pogacar"}
        result = build_contributors(
            my_team=team, sim_results=sim_results,
            rider_names=rider_name_map,
            scenario_stats={"bunch_sprint": 0.6, "breakaway": 0.4},
            scenario_priors=None,  # must omit scenario_contributions
        )
        assert "scenario_contributions" not in result, (
            "scenario_contributions must be omitted when scenario_priors is None"
        )
