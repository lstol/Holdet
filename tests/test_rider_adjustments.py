"""
tests/test_rider_adjustments.py — Session 19.5: Rider confidence adjustments

8 tests covering apply_rider_adjustments() correctness, bounds, immutability,
source deduplication, calibration firewall, simulation safety, and the warning.
"""
from __future__ import annotations

import io
import sys

import pytest

from scoring.probabilities import (
    RiderProb,
    apply_rider_adjustments,
    MAX_RIDER_ADJUSTMENT,
    MAX_ADJUSTED_RIDERS,
)
from scoring.engine import Rider, Stage
from scoring.simulator import simulate_all_riders
from scripts.calibrate import compute_brier_scores
from scoring.probabilities import ROLE_TOP15


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_rp(rider_id: str, p_win: float = 0.28, source: str = "model") -> RiderProb:
    return RiderProb(
        rider_id=rider_id,
        stage_number=1,
        p_win=p_win,
        p_top3=p_win * 2,
        p_top10=p_win * 4,
        p_top15=p_win * 5,
        p_dnf=0.05,
        source=source,
    )


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


def _make_stage(stage_type: str = "flat") -> Stage:
    return Stage(
        number=1, race="giro_2026", stage_type=stage_type,
        distance_km=180.0, is_ttt=False,
        start_location="A", finish_location="B",
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestApplyRiderAdjustments:
    def test_rider_adjustment_applies_multiplier_correctly(self):
        # +20% on p_win=0.28 → 0.28 * 1.20 = 0.336
        probs = {"r1": _make_rp("r1", p_win=0.28)}
        result = apply_rider_adjustments(probs, {"r1": 0.20})
        assert abs(result["r1"].p_win - round(0.28 * 1.20, 4)) < 1e-6

    def test_rider_adjustment_clamped_to_bounds(self):
        # mult=0.50 → clamped to MAX_RIDER_ADJUSTMENT=0.30 before applying
        probs = {"r1": _make_rp("r1", p_win=0.28)}
        result = apply_rider_adjustments(probs, {"r1": 0.50})
        expected = round(0.28 * (1 + MAX_RIDER_ADJUSTMENT), 4)
        assert abs(result["r1"].p_win - expected) < 1e-6

    def test_zero_adjustment_no_change(self):
        probs = {"r1": _make_rp("r1", p_win=0.28)}
        result = apply_rider_adjustments(probs, {"r1": 0.0})
        assert result["r1"].p_win == probs["r1"].p_win

    def test_adjustments_do_not_mutate_base_probs(self):
        probs = {"r1": _make_rp("r1", p_win=0.28)}
        original_p_win = probs["r1"].p_win
        apply_rider_adjustments(probs, {"r1": 0.20})
        # Input dict must be unchanged
        assert probs["r1"].p_win == original_p_win
        assert "rca_p_win" not in probs["r1"].manual_overrides

    def test_source_string_no_duplicates(self):
        # Applying user adjustment twice must not produce "model+user+user"
        probs = {"r1": _make_rp("r1", source="model")}
        result1 = apply_rider_adjustments(probs, {"r1": 0.10})
        # Simulate a second call on already-adjusted probs
        result2 = apply_rider_adjustments(result1, {"r1": 0.10})
        assert result2["r1"].source == "model+user"
        assert result2["r1"].source.count("user") == 1

    def test_calibration_ignores_adjusted_probs(self):
        # compute_brier_scores() uses ROLE_TOP15 constant, not adjusted p_win.
        # Building entries with role=sprinter, stage_type=flat, outcome=1:
        # Brier = (ROLE_TOP15["sprinter"]["flat"] - 1)^2 regardless of adjusted p_win.
        entries = [
            {"stage": 1, "role": "sprinter", "stage_type": "flat", "outcome": 1},
        ]
        scores = compute_brier_scores(entries)
        expected_p = ROLE_TOP15["sprinter"]["flat"]
        expected_brier = (expected_p - 1) ** 2
        assert abs(scores["overall"][("sprinter", "flat")] - expected_brier) < 1e-9

    def test_extreme_adjustment_does_not_break_simulation(self):
        # +30% on all team riders: simulate_all_riders() runs cleanly, all probs ∈ [0, 1]
        rider_ids = [f"r{i}" for i in range(3)]
        riders = [_make_rider(rid) for rid in rider_ids]
        stage = _make_stage("flat")

        from scoring.probabilities import generate_priors
        probs = generate_priors(riders, stage)
        adjustments = {rid: MAX_RIDER_ADJUSTMENT for rid in rider_ids}
        probs = apply_rider_adjustments(probs, adjustments)

        # All adjusted probs must be in [0, 1]
        for rp in probs.values():
            for field in ("p_win", "p_top3", "p_top10", "p_top15", "p_dnf"):
                v = getattr(rp, field)
                assert 0.0 <= v <= 1.0, f"{field}={v} out of [0, 1]"

        # Simulation must complete without error
        sim_results = simulate_all_riders(
            riders=riders,
            stage=stage,
            probs=probs,
            my_team=rider_ids,
            captain=rider_ids[0],
            stages_remaining=1,
            seed=42,
        )
        assert len(sim_results) == len(riders)

    def test_warning_printed_when_too_many_riders_adjusted(self, capsys):
        # 4 riders adjusted → triggers [WARNING] message (does not block)
        probs = {f"r{i}": _make_rp(f"r{i}") for i in range(4)}
        adjustments = {f"r{i}": 0.10 for i in range(4)}
        apply_rider_adjustments(probs, adjustments)
        captured = capsys.readouterr()
        assert "[WARNING]" in captured.out
        assert str(MAX_ADJUSTED_RIDERS) in captured.out
