"""
tests/test_probabilities.py — Tests for scoring/probabilities.py

Covers: DNS handling, probability clamping, monotonicity,
completeness, adjustments, persistence, jersey/sprint/mountain logic.
"""
import sys
import os
import json
import tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from scoring.engine import Rider, Stage, SprintPoint, KOMPoint
from scoring.probabilities import (
    RiderProb, generate_priors, interactive_adjust, save_probs, load_probs,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_rider(
    holdet_id="r1",
    status="active",
    value=5_000_000,
    jerseys=None,
    name="Test Rider",
    team_abbr="TST",
) -> Rider:
    return Rider(
        holdet_id=holdet_id,
        person_id="p1",
        team_id="t1",
        name=name,
        team="Test Team",
        team_abbr=team_abbr,
        value=value,
        start_value=value,
        points=0,
        status=status,
        gc_position=None,
        jerseys=jerseys or [],
        in_my_team=False,
        is_captain=False,
    )


def make_stage(
    stage_type="flat",
    number=1,
    sprint_points=None,
    kom_points=None,
) -> Stage:
    return Stage(
        number=number,
        race="giro_2026",
        stage_type=stage_type,
        distance_km=180.0,
        is_ttt=(stage_type == "ttt"),
        start_location="A",
        finish_location="B",
        sprint_points=sprint_points or [],
        kom_points=kom_points or [],
    )


def make_sprint_point() -> SprintPoint:
    return SprintPoint(
        location="Midpoint",
        km_from_start=90.0,
        points_available=[20, 17, 15],
        is_finish=False,
    )


def make_kom_point() -> KOMPoint:
    return KOMPoint(
        location="Col du Test",
        km_from_start=120.0,
        category="2",
        points_available=[5, 3, 2, 1],
    )


# ── DNS tests ─────────────────────────────────────────────────────────────────

class TestDNSRider:
    def test_dns_p_dnf_is_one(self):
        rider = make_rider(status="dns")
        stage = make_stage()
        probs = generate_priors([rider], stage)
        assert probs["r1"].p_dnf == 1.0

    def test_dns_all_other_probs_zero(self):
        rider = make_rider(status="dns")
        stage = make_stage()
        rp = generate_priors([rider], stage)["r1"]
        assert rp.p_win == 0.0
        assert rp.p_top3 == 0.0
        assert rp.p_top10 == 0.0
        assert rp.p_top15 == 0.0
        assert rp.expected_sprint_points == 0.0
        assert rp.expected_kom_points == 0.0


# ── Clamping tests ────────────────────────────────────────────────────────────

class TestProbabilityClamping:
    def test_all_probs_in_range(self):
        riders = [make_rider(holdet_id=f"r{i}", value=i * 1_000_000) for i in range(1, 6)]
        for stage_type in ("flat", "hilly", "mountain", "itt", "ttt"):
            stage = make_stage(stage_type=stage_type)
            probs = generate_priors(riders, stage)
            for rp in probs.values():
                assert 0.0 <= rp.p_win <= 1.0, f"p_win out of range: {rp.p_win}"
                assert 0.0 <= rp.p_top3 <= 1.0
                assert 0.0 <= rp.p_top10 <= 1.0
                assert 0.0 <= rp.p_top15 <= 1.0
                assert 0.0 <= rp.p_dnf <= 1.0

    def test_no_negative_probs(self):
        rider = make_rider(value=1_000)
        stage = make_stage("mountain")
        rp = generate_priors([rider], stage)["r1"]
        assert rp.p_win >= 0.0
        assert rp.p_top3 >= 0.0
        assert rp.expected_sprint_points >= 0.0
        assert rp.expected_kom_points >= 0.0


# ── Monotonicity tests ────────────────────────────────────────────────────────

class TestMonotonicity:
    def test_win_lte_top3_lte_top10_lte_top15(self):
        rider = make_rider()
        for stage_type in ("flat", "hilly", "mountain", "itt"):
            stage = make_stage(stage_type=stage_type)
            rp = generate_priors([rider], stage)["r1"]
            assert rp.p_win <= rp.p_top3, f"{stage_type}: p_win > p_top3"
            assert rp.p_top3 <= rp.p_top10, f"{stage_type}: p_top3 > p_top10"
            assert rp.p_top10 <= rp.p_top15, f"{stage_type}: p_top10 > p_top15"

    def test_monotonicity_multiple_riders(self):
        riders = [make_rider(holdet_id=f"r{i}") for i in range(5)]
        stage = make_stage("mountain")
        probs = generate_priors(riders, stage)
        for rp in probs.values():
            assert rp.p_win <= rp.p_top3 <= rp.p_top10 <= rp.p_top15


# ── Completeness test ─────────────────────────────────────────────────────────

class TestCompleteness:
    def test_returns_entry_for_every_rider(self):
        riders = [make_rider(holdet_id=f"r{i}") for i in range(10)]
        stage = make_stage()
        probs = generate_priors(riders, stage)
        assert len(probs) == 10
        for rider in riders:
            assert rider.holdet_id in probs

    def test_mixed_dns_and_active(self):
        riders = [
            make_rider(holdet_id="r1", status="active"),
            make_rider(holdet_id="r2", status="dns"),
            make_rider(holdet_id="r3", status="active"),
        ]
        stage = make_stage()
        probs = generate_priors(riders, stage)
        assert len(probs) == 3
        assert probs["r2"].p_dnf == 1.0
        assert probs["r1"].p_dnf < 1.0


# ── Jersey retention tests ────────────────────────────────────────────────────

class TestJerseyRetention:
    def test_green_jersey_flat_above_0_5(self):
        rider = make_rider(jerseys=["green"])
        stage = make_stage("flat")
        rp = generate_priors([rider], stage)["r1"]
        assert rp.p_jersey_retain.get("green", 0) > 0.5

    def test_yellow_jersey_mountain_below_flat(self):
        rider_flat = make_rider(holdet_id="r1", jerseys=["yellow"])
        rider_mtn  = make_rider(holdet_id="r2", jerseys=["yellow"])
        flat_prob  = generate_priors([rider_flat], make_stage("flat"))["r1"]
        mtn_prob   = generate_priors([rider_mtn],  make_stage("mountain"))["r2"]
        assert flat_prob.p_jersey_retain["yellow"] > mtn_prob.p_jersey_retain["yellow"]

    def test_no_jersey_no_retain_entry(self):
        rider = make_rider(jerseys=[])
        stage = make_stage("flat")
        rp = generate_priors([rider], stage)["r1"]
        assert rp.p_jersey_retain == {}


# ── Sprint / KOM expectation tests ───────────────────────────────────────────

class TestSprintKOM:
    def test_no_sprint_points_if_no_sprint_defined(self):
        rider = make_rider()
        stage = make_stage("flat", sprint_points=[])
        rp = generate_priors([rider], stage)["r1"]
        assert rp.expected_sprint_points == 0.0

    def test_sprint_points_nonzero_on_flat_with_sprint(self):
        rider = make_rider()
        stage = make_stage("flat", sprint_points=[make_sprint_point()])
        rp = generate_priors([rider], stage)["r1"]
        assert rp.expected_sprint_points > 0.0

    def test_no_kom_points_if_no_kom_defined(self):
        rider = make_rider()
        stage = make_stage("mountain", kom_points=[])
        rp = generate_priors([rider], stage)["r1"]
        assert rp.expected_kom_points == 0.0

    def test_kom_points_nonzero_on_mountain_with_kom(self):
        rider = make_rider()
        stage = make_stage("mountain", kom_points=[make_kom_point()])
        rp = generate_priors([rider], stage)["r1"]
        assert rp.expected_kom_points > 0.0


# ── Rider type classification tests ──────────────────────────────────────────

class TestRiderTypeClassification:
    """A1: value-bracket classification gives differentiated priors."""

    def test_gc_rider_higher_p_top15_on_mountain_than_flat(self):
        """GC rider (value > 8M) gets higher p_top15 on mountain than flat."""
        gc_rider = make_rider(value=10_000_000)
        rp_mountain = generate_priors([gc_rider], make_stage("mountain"))["r1"]
        rp_flat = generate_priors([gc_rider], make_stage("flat"))["r1"]
        assert rp_mountain.p_top15 > rp_flat.p_top15, (
            f"GC rider mountain p_top15 ({rp_mountain.p_top15}) should exceed "
            f"flat p_top15 ({rp_flat.p_top15})"
        )

    def test_high_value_rider_outperforms_domestique_on_flat(self):
        """On a flat stage, high-value rider (sprinter type) has much higher p_top15 than domestique."""
        sprinter = make_rider(holdet_id="s1", value=10_000_000)
        domestique = make_rider(holdet_id="d1", value=2_000_000)
        flat = make_stage("flat")
        rp_sprinter = generate_priors([sprinter, domestique], flat)["s1"]
        rp_domestique = generate_priors([sprinter, domestique], flat)["d1"]
        assert rp_sprinter.p_top15 > rp_domestique.p_top15, (
            f"Sprinter flat p_top15 ({rp_sprinter.p_top15}) should exceed "
            f"domestique flat p_top15 ({rp_domestique.p_top15})"
        )

    def test_domestique_gets_low_p_top15_on_all_stage_types(self):
        """Domestique (value < 3M) gets low p_top15 across all stage types."""
        domestique = make_rider(value=2_000_000)
        for stage_type in ("flat", "hilly", "mountain", "itt"):
            rp = generate_priors([domestique], make_stage(stage_type))["r1"]
            assert rp.p_top15 <= 0.05, (
                f"Domestique p_top15 on {stage_type} is {rp.p_top15} (expected ≤ 0.05)"
            )


# ── Manual adjustment tests ───────────────────────────────────────────────────

class TestInteractiveAdjust:
    def _run(self, probs, stage, inputs):
        input_iter = iter(inputs)
        def mock_input(prompt=""):
            try:
                return next(input_iter)
            except StopIteration:
                return "done"
        return interactive_adjust(probs, stage, _input_fn=mock_input)

    def _run(self, probs, stage, inputs, riders=None):
        input_iter = iter(inputs)
        def mock_input(prompt=""):
            try:
                return next(input_iter)
            except StopIteration:
                return "done"
        return interactive_adjust(probs, stage, riders=riders, _input_fn=mock_input)

    def test_adjustment_updates_source_to_adjusted(self):
        rider = make_rider(holdet_id="r1", name="Milan Jan")
        stage = make_stage()
        probs = generate_priors([rider], stage)
        updated = self._run(probs, stage, ["milan win 35", "done"], riders=[rider])
        assert updated["r1"].source == "adjusted"

    def test_adjustment_stores_in_manual_overrides(self):
        rider = make_rider(holdet_id="r1", name="Milan Jan")
        stage = make_stage()
        probs = generate_priors([rider], stage)
        updated = self._run(probs, stage, ["milan win 35", "done"], riders=[rider])
        assert "p_win" in updated["r1"].manual_overrides
        assert abs(updated["r1"].manual_overrides["p_win"] - 0.35) < 1e-6

    def test_adjustment_value_is_correct(self):
        rider = make_rider(holdet_id="r1", name="Vingegaard Jonas")
        stage = make_stage()
        probs = generate_priors([rider], stage)
        updated = self._run(probs, stage, ["vingegaard win 50", "done"], riders=[rider])
        assert abs(updated["r1"].p_win - 0.50) < 1e-6

    def test_unadjusted_riders_unchanged(self):
        r1 = make_rider(holdet_id="r1", name="Milan Jan")
        r2 = make_rider(holdet_id="r2", name="Vingegaard Jonas")
        stage = make_stage()
        probs = generate_priors([r1, r2], stage)
        original_r2_win = probs["r2"].p_win
        updated = self._run(probs, stage, ["milan win 35", "done"], riders=[r1, r2])
        assert updated["r2"].p_win == original_r2_win
        assert updated["r2"].source == "model"

    def test_dnf_adjustment_clamped_to_1(self):
        rider = make_rider(holdet_id="r1", name="Rider A")
        stage = make_stage()
        probs = generate_priors([rider], stage)
        updated = self._run(probs, stage, ["rider dnf 150", "done"], riders=[rider])
        assert updated["r1"].p_dnf <= 1.0


# ── Persistence tests ─────────────────────────────────────────────────────────

class TestPersistence:
    def test_save_load_round_trip(self):
        rider = make_rider(holdet_id="r1")
        stage = make_stage(number=3)
        probs = generate_priors([rider], stage)

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name

        try:
            save_probs(probs, 3, path)
            loaded = load_probs(3, path)

            assert loaded is not None
            assert "r1" in loaded
            rp = loaded["r1"]
            assert rp.p_win == probs["r1"].p_win
            assert rp.p_top15 == probs["r1"].p_top15
            assert rp.p_dnf == probs["r1"].p_dnf
            assert rp.stage_number == 3
        finally:
            os.unlink(path)

    def test_load_missing_stage_returns_none(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            rider = make_rider()
            probs = generate_priors([rider], make_stage(number=1))
            save_probs(probs, 1, path)
            result = load_probs(99, path)
            assert result is None
        finally:
            os.unlink(path)

    def test_load_nonexistent_file_returns_none(self):
        result = load_probs(1, "/tmp/does_not_exist_xyz123.json")
        assert result is None

    def test_save_creates_file_if_not_exists(self):
        path = "/tmp/test_state_create_xyz123.json"
        if os.path.exists(path):
            os.unlink(path)
        try:
            probs = generate_priors([make_rider()], make_stage(number=1))
            save_probs(probs, 1, path)
            assert os.path.exists(path)
            data = json.loads(open(path).read())
            assert "prob_history" in data
            assert "stage_1" in data["prob_history"]
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_save_preserves_other_state_keys(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"my_team": ["r1", "r2"], "current_stage": 5}, f)
            path = f.name
        try:
            probs = generate_priors([make_rider()], make_stage(number=5))
            save_probs(probs, 5, path)
            data = json.loads(open(path).read())
            assert data["my_team"] == ["r1", "r2"]
            assert data["current_stage"] == 5
            assert "prob_history" in data
        finally:
            os.unlink(path)
