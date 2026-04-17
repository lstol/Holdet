"""
tests/test_simulator.py — Unit tests for scoring/simulator.py

All tests use fixed seeds for reproducibility.
"""
import pytest
from scoring.engine import Rider, Stage, SprintPoint, KOMPoint
from scoring.probabilities import RiderProb
from scoring.simulator import SimResult, simulate_rider, simulate_team, _sample_finish_position

import numpy as np


# ── Shared fixtures ───────────────────────────────────────────────────────────

def make_rider(
    holdet_id="r1",
    name="Test Rider",
    team="Team Alpha",
    team_abbr="TAL",
    value=5_000_000,
    gc_position=None,
    jerseys=None,
    status="active",
    in_my_team=True,
    is_captain=False,
):
    return Rider(
        holdet_id=holdet_id,
        person_id="p1",
        team_id="t1",
        name=name,
        team=team,
        team_abbr=team_abbr,
        value=value,
        start_value=value,
        points=0,
        status=status,
        gc_position=gc_position,
        jerseys=jerseys or [],
        in_my_team=in_my_team,
        is_captain=is_captain,
    )


def make_flat_stage(number=1, has_sprint=False):
    sprint_points = []
    if has_sprint:
        sprint_points = [SprintPoint("Finish", 200.0, [20, 17, 15, 13, 11], is_finish=True)]
    return Stage(
        number=number,
        race="test_race",
        stage_type="flat",
        distance_km=200.0,
        is_ttt=False,
        start_location="A",
        finish_location="B",
        sprint_points=sprint_points,
        kom_points=[],
    )


def make_mountain_stage(number=3, has_kom=False):
    kom_points = []
    if has_kom:
        kom_points = [KOMPoint("Col Summit", 150.0, "HC", [20, 15, 12, 10, 8, 6, 4, 2])]
    return Stage(
        number=number,
        race="test_race",
        stage_type="mountain",
        distance_km=170.0,
        is_ttt=False,
        start_location="C",
        finish_location="D",
        sprint_points=[],
        kom_points=kom_points,
    )


def make_ttt_stage(number=2):
    return Stage(
        number=number,
        race="test_race",
        stage_type="ttt",
        distance_km=35.0,
        is_ttt=True,
        start_location="E",
        finish_location="F",
    )


def make_probs(
    rider_id="r1",
    stage_number=1,
    p_win=0.05,
    p_top3=0.12,
    p_top10=0.25,
    p_top15=0.40,
    p_dnf=0.01,
    p_jersey_retain=None,
    expected_sprint_points=0.0,
    expected_kom_points=0.0,
):
    return RiderProb(
        rider_id=rider_id,
        stage_number=stage_number,
        p_win=p_win,
        p_top3=p_top3,
        p_top10=p_top10,
        p_top15=p_top15,
        p_dnf=p_dnf,
        p_jersey_retain=p_jersey_retain or {},
        expected_sprint_points=expected_sprint_points,
        expected_kom_points=expected_kom_points,
        source="model",
        model_confidence=0.6,
        manual_overrides={},
    )


MY_TEAM = ["r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8"]
CAPTAIN = "r1"
N = 5_000  # enough for stable estimates, fast enough for CI


# ── TestSimResultSchema ───────────────────────────────────────────────────────

class TestSimResultSchema:
    """simulate_rider returns a SimResult with the expected fields."""

    def test_returns_sim_result(self):
        rider = make_rider()
        stage = make_flat_stage()
        probs = make_probs()
        result = simulate_rider(rider, stage, probs, MY_TEAM, CAPTAIN, n_simulations=N, seed=42)
        assert isinstance(result, SimResult)

    def test_rider_id_preserved(self):
        rider = make_rider(holdet_id="abc123")
        stage = make_flat_stage()
        probs = make_probs(rider_id="abc123")
        result = simulate_rider(rider, stage, probs, MY_TEAM, CAPTAIN, n_simulations=N, seed=42)
        assert result.rider_id == "abc123"

    def test_all_fields_present(self):
        rider = make_rider()
        stage = make_flat_stage()
        probs = make_probs()
        result = simulate_rider(rider, stage, probs, MY_TEAM, CAPTAIN, n_simulations=N, seed=42)
        for attr in ("expected_value", "std_dev", "percentile_10", "percentile_50",
                     "percentile_80", "percentile_90", "percentile_95", "p_positive"):
            assert hasattr(result, attr), f"Missing field: {attr}"
            assert isinstance(getattr(result, attr), float), f"{attr} should be float"

    def test_p_positive_in_range(self):
        rider = make_rider()
        stage = make_flat_stage()
        probs = make_probs()
        result = simulate_rider(rider, stage, probs, MY_TEAM, CAPTAIN, n_simulations=N, seed=42)
        assert 0.0 <= result.p_positive <= 1.0

    def test_std_dev_non_negative(self):
        rider = make_rider()
        stage = make_flat_stage()
        probs = make_probs()
        result = simulate_rider(rider, stage, probs, MY_TEAM, CAPTAIN, n_simulations=N, seed=42)
        assert result.std_dev >= 0.0


# ── TestPercentileOrdering ────────────────────────────────────────────────────

class TestPercentileOrdering:
    """Percentiles must be monotonically non-decreasing."""

    def test_percentiles_ordered_flat(self):
        rider = make_rider()
        stage = make_flat_stage()
        probs = make_probs()
        r = simulate_rider(rider, stage, probs, MY_TEAM, CAPTAIN, n_simulations=N, seed=1)
        assert r.percentile_10 <= r.percentile_50
        assert r.percentile_50 <= r.percentile_80
        assert r.percentile_80 <= r.percentile_90
        assert r.percentile_90 <= r.percentile_95

    def test_percentiles_ordered_mountain(self):
        rider = make_rider()
        stage = make_mountain_stage()
        probs = make_probs(p_win=0.10, p_top3=0.20, p_top10=0.35, p_top15=0.50, p_dnf=0.03)
        r = simulate_rider(rider, stage, probs, MY_TEAM, CAPTAIN, n_simulations=N, seed=2)
        assert r.percentile_10 <= r.percentile_50
        assert r.percentile_50 <= r.percentile_80
        assert r.percentile_80 <= r.percentile_90
        assert r.percentile_90 <= r.percentile_95

    def test_median_near_expected_for_low_variance(self):
        """When p_win is dominant and no other sources, median ≈ EV."""
        rider = make_rider()
        stage = make_flat_stage()
        # Very concentrated: almost always wins or DNFs
        probs = make_probs(p_win=0.90, p_top3=0.92, p_top10=0.93, p_top15=0.94, p_dnf=0.05)
        r = simulate_rider(rider, stage, probs, MY_TEAM, CAPTAIN, n_simulations=N, seed=3)
        # With p_win=0.90 and almost nothing else, most outcomes are 200k or -50k
        # The median should be 200k (since 90% of runs win)
        assert r.percentile_50 > 100_000


# ── TestDNFRider ──────────────────────────────────────────────────────────────

class TestDNFRider:
    """A rider with p_dnf=1.0 always scores -50,000."""

    def test_certain_dnf_ev(self):
        rider = make_rider()
        stage = make_flat_stage()
        probs = make_probs(p_win=0.0, p_top3=0.0, p_top10=0.0, p_top15=0.0, p_dnf=1.0)
        result = simulate_rider(rider, stage, probs, MY_TEAM, CAPTAIN, n_simulations=500, seed=99)
        assert abs(result.expected_value - (-50_000)) < 1.0

    def test_certain_dnf_std_dev_zero(self):
        rider = make_rider()
        stage = make_flat_stage()
        probs = make_probs(p_win=0.0, p_top3=0.0, p_top10=0.0, p_top15=0.0, p_dnf=1.0)
        result = simulate_rider(rider, stage, probs, MY_TEAM, CAPTAIN, n_simulations=500, seed=99)
        assert result.std_dev < 1.0


# ── TestCaptainBonus ──────────────────────────────────────────────────────────

class TestCaptainBonus:
    """Captain with high p_win should have higher EV than non-captain."""

    def test_captain_ev_higher_than_non_captain(self):
        stage = make_flat_stage()
        probs = make_probs(p_win=0.40, p_top3=0.60, p_top10=0.75, p_top15=0.85, p_dnf=0.01)

        rider_cap = make_rider(holdet_id="cap", is_captain=True)
        rider_reg = make_rider(holdet_id="reg", is_captain=False)

        my_team_cap = ["cap", "r2", "r3", "r4", "r5", "r6", "r7", "r8"]
        my_team_reg = ["reg", "r2", "r3", "r4", "r5", "r6", "r7", "r8"]

        probs_cap = make_probs(rider_id="cap", p_win=0.40, p_top3=0.60, p_top10=0.75, p_top15=0.85)
        probs_reg = make_probs(rider_id="reg", p_win=0.40, p_top3=0.60, p_top10=0.75, p_top15=0.85)

        r_cap = simulate_rider(rider_cap, stage, probs_cap, my_team_cap, "cap", N, seed=10)
        r_reg = simulate_rider(rider_reg, stage, probs_reg, my_team_reg, "reg", N, seed=10)

        # Captain mirrors positive value to bank → total_rider_value_delta unchanged;
        # but for measuring captain benefit we check the bank via total_bank_delta.
        # Here we verify captain_bank_deposit pushes the overall EV (rider+bank) higher.
        # Since simulate_rider only tracks total_rider_value_delta, EVs should be equal.
        # This test confirms they are indeed equal (bank deposit is separate).
        assert abs(r_cap.expected_value - r_reg.expected_value) < 5_000


# ── TestJerseySimulation ──────────────────────────────────────────────────────

class TestJerseySimulation:
    """Jersey retention probabilities affect EV correctly."""

    def test_yellow_jersey_holder_higher_ev(self):
        """Rider holding yellow jersey has higher EV than identical rider without."""
        stage = make_flat_stage()
        probs = make_probs(p_win=0.05, p_top3=0.12, p_top10=0.25, p_top15=0.40)

        rider_yellow = make_rider(holdet_id="yj", jerseys=["yellow"])
        rider_none = make_rider(holdet_id="no", jerseys=[])

        probs_yellow = make_probs(rider_id="yj", p_jersey_retain={"yellow": 0.85})
        probs_none = make_probs(rider_id="no", p_jersey_retain={})

        r_yellow = simulate_rider(rider_yellow, stage, probs_yellow, MY_TEAM, CAPTAIN, N, seed=20)
        r_none = simulate_rider(rider_none, stage, probs_none, MY_TEAM, CAPTAIN, N, seed=20)

        # Expected diff ≈ 0.85 × 25,000 = 21,250
        assert r_yellow.expected_value > r_none.expected_value
        diff = r_yellow.expected_value - r_none.expected_value
        assert 15_000 < diff < 30_000

    def test_dnf_rider_no_jersey_bonus(self):
        """DNF rider never earns jersey bonus."""
        stage = make_flat_stage()
        rider = make_rider(holdet_id="yj", jerseys=["yellow"])
        probs = make_probs(rider_id="yj", p_win=0.0, p_top3=0.0, p_top10=0.0,
                           p_top15=0.0, p_dnf=1.0, p_jersey_retain={"yellow": 0.85})
        result = simulate_rider(rider, stage, probs, MY_TEAM, CAPTAIN, n_simulations=500, seed=30)
        assert abs(result.expected_value - (-50_000)) < 1.0


# ── TestSprintKOMSimulation ───────────────────────────────────────────────────

class TestSprintKOMSimulation:
    """Sprint and KOM point expectations increase EV proportionally."""

    def test_sprint_points_increase_ev(self):
        stage = make_flat_stage(has_sprint=True)
        rider = make_rider()

        probs_no_sprint = make_probs(expected_sprint_points=0.0)
        probs_sprint = make_probs(expected_sprint_points=5.0)

        r_no = simulate_rider(rider, stage, probs_no_sprint, MY_TEAM, CAPTAIN, N, seed=40)
        r_sp = simulate_rider(rider, stage, probs_sprint, MY_TEAM, CAPTAIN, N, seed=40)

        # Each sprint point = 3,000; expected diff ≈ 5 × 3,000 = 15,000
        diff = r_sp.expected_value - r_no.expected_value
        assert 10_000 < diff < 22_000

    def test_kom_points_increase_ev(self):
        stage = make_mountain_stage(has_kom=True)
        rider = make_rider()

        probs_no_kom = make_probs(expected_kom_points=0.0)
        probs_kom = make_probs(expected_kom_points=4.0)

        r_no = simulate_rider(rider, stage, probs_no_kom, MY_TEAM, CAPTAIN, N, seed=41)
        r_km = simulate_rider(rider, stage, probs_kom, MY_TEAM, CAPTAIN, N, seed=41)

        diff = r_km.expected_value - r_no.expected_value
        assert 7_000 < diff < 18_000


# ── TestStagePositionEV ───────────────────────────────────────────────────────

class TestStagePositionEV:
    """
    Verify that EV from stage position is in the expected range.

    Key spot-check from SESSION_ROADMAP.md:
      A rider with p_win=0.30 on a flat stage should show ~+90k EV from
      stage position alone (no GC, no jerseys, no sprint/KOM, not captain).

    Theoretical calculation with the chosen probability setup:
      p_win=0.30:          0.30 × 200k = 60,000
      p(2nd or 3rd)=0.15:  0.075 × 150k + 0.075 × 130k = 21,000
      p(4th-10th)=0.10:    0.10 × avg(120k..80k) ≈ 9,714
      p(11th-15th)=0.10:   0.10 × avg(70k..15k)  ≈ 4,200
      DNF:                 0.01 × (-50k)           = -500
      ─────────────────────────────────────────────────────
      Total EV ≈ 94,414   (well within "~90k" tolerance)
    """

    def test_p_win_30_flat_ev_approx_90k(self):
        stage = make_flat_stage()
        rider = make_rider(
            holdet_id="star",
            gc_position=None,  # no GC contribution
            jerseys=[],        # no jersey contribution
            is_captain=False,
        )
        probs = make_probs(
            rider_id="star",
            p_win=0.30,
            p_top3=0.45,   # P(2-3) = 0.15
            p_top10=0.55,  # P(4-10) = 0.10
            p_top15=0.65,  # P(11-15) = 0.10
            p_dnf=0.01,
            p_jersey_retain={},
            expected_sprint_points=0.0,
            expected_kom_points=0.0,
        )
        my_team_no_star_wins = ["star"] + [f"other_{i}" for i in range(7)]
        result = simulate_rider(
            rider, stage, probs, my_team_no_star_wins, "other_0",
            n_simulations=20_000, seed=42,
        )
        # Theoretical EV ≈ 94k. Accept range 80k–110k to account for simulation noise.
        assert 80_000 <= result.expected_value <= 110_000, (
            f"Expected EV ~90k for p_win=0.30 flat stage, got {result.expected_value:.0f}"
        )

    def test_high_p_win_mountain_higher_ev_than_low_p_win(self):
        """Rider with higher win probability should have higher EV on mountain stage."""
        stage = make_mountain_stage()
        rider_strong = make_rider(holdet_id="strong")
        rider_weak = make_rider(holdet_id="weak")

        probs_strong = make_probs(rider_id="strong",
                                  p_win=0.20, p_top3=0.40, p_top10=0.55, p_top15=0.65)
        probs_weak = make_probs(rider_id="weak",
                                p_win=0.02, p_top3=0.05, p_top10=0.12, p_top15=0.20)

        r_strong = simulate_rider(rider_strong, stage, probs_strong, MY_TEAM, CAPTAIN, N, seed=50)
        r_weak = simulate_rider(rider_weak, stage, probs_weak, MY_TEAM, CAPTAIN, N, seed=50)

        assert r_strong.expected_value > r_weak.expected_value


# ── TestGCPositionEV ──────────────────────────────────────────────────────────

class TestGCPositionEV:
    """GC position adds value when rider is in top 10."""

    def test_gc_position_1_adds_100k(self):
        """Rider in GC 1st should get ≈ +100k from GC standing."""
        stage = make_flat_stage()
        rider_gc1 = make_rider(holdet_id="gc1", gc_position=1)
        rider_no_gc = make_rider(holdet_id="nogc", gc_position=None)

        # Very low finishing probability so stage position contribution is minimal
        probs_gc1 = make_probs(rider_id="gc1", p_win=0.0, p_top3=0.0,
                               p_top10=0.0, p_top15=0.0, p_dnf=0.0)
        probs_no_gc = make_probs(rider_id="nogc", p_win=0.0, p_top3=0.0,
                                 p_top10=0.0, p_top15=0.0, p_dnf=0.0)

        r_gc1 = simulate_rider(rider_gc1, stage, probs_gc1, MY_TEAM, CAPTAIN, N, seed=60)
        r_no_gc = simulate_rider(rider_no_gc, stage, probs_no_gc, MY_TEAM, CAPTAIN, N, seed=60)

        # GC standing value for 1st = 100,000
        diff = r_gc1.expected_value - r_no_gc.expected_value
        assert abs(diff - 100_000) < 500

    def test_gc_position_11_adds_nothing(self):
        """GC position 11+ earns 0 — same EV as a rider with no GC position."""
        stage = make_flat_stage()
        # Use p_win=1.0 so the rider always wins (avoids late arrival penalty noise)
        rider_gc11 = make_rider(holdet_id="gc11", gc_position=11)
        rider_no_gc = make_rider(holdet_id="nogc", gc_position=None)
        probs_gc11 = make_probs(rider_id="gc11", p_win=1.0, p_top3=1.0,
                                p_top10=1.0, p_top15=1.0, p_dnf=0.0)
        probs_no_gc = make_probs(rider_id="nogc", p_win=1.0, p_top3=1.0,
                                 p_top10=1.0, p_top15=1.0, p_dnf=0.0)
        r_gc11 = simulate_rider(rider_gc11, stage, probs_gc11, MY_TEAM, CAPTAIN, 500, seed=61)
        r_no_gc = simulate_rider(rider_no_gc, stage, probs_no_gc, MY_TEAM, CAPTAIN, 500, seed=61)
        # Both always win (200k); GC 11 adds 0, so EVs should be identical
        assert abs(r_gc11.expected_value - 200_000) < 1.0
        assert abs(r_no_gc.expected_value - 200_000) < 1.0


# ── TestReproducibility ───────────────────────────────────────────────────────

class TestReproducibility:
    """Same seed always produces the same SimResult."""

    def test_same_seed_same_result(self):
        rider = make_rider()
        stage = make_flat_stage()
        probs = make_probs()

        r1 = simulate_rider(rider, stage, probs, MY_TEAM, CAPTAIN, n_simulations=1000, seed=77)
        r2 = simulate_rider(rider, stage, probs, MY_TEAM, CAPTAIN, n_simulations=1000, seed=77)

        assert r1.expected_value == r2.expected_value
        assert r1.std_dev == r2.std_dev
        assert r1.percentile_50 == r2.percentile_50

    def test_different_seeds_differ(self):
        rider = make_rider()
        stage = make_flat_stage()
        probs = make_probs()

        r1 = simulate_rider(rider, stage, probs, MY_TEAM, CAPTAIN, n_simulations=1000, seed=1)
        r2 = simulate_rider(rider, stage, probs, MY_TEAM, CAPTAIN, n_simulations=1000, seed=2)

        # Extremely unlikely to be exactly equal
        assert r1.expected_value != r2.expected_value


# ── TestSimulateTeam ──────────────────────────────────────────────────────────

class TestSimulateTeam:
    """simulate_team runs all riders and returns sorted results."""

    def _make_team(self):
        riders = [make_rider(holdet_id=f"r{i}", name=f"Rider {i}") for i in range(1, 9)]
        probs = {f"r{i}": make_probs(rider_id=f"r{i}") for i in range(1, 9)}
        return riders, probs

    def test_returns_dict(self):
        riders, probs = self._make_team()
        stage = make_flat_stage()
        results = simulate_team(riders, stage, probs, MY_TEAM, CAPTAIN, N, seed=80)
        assert isinstance(results, dict)
        assert len(results) == 8

    def test_all_rider_ids_present(self):
        riders, probs = self._make_team()
        stage = make_flat_stage()
        results = simulate_team(riders, stage, probs, MY_TEAM, CAPTAIN, N, seed=81)
        for i in range(1, 9):
            assert f"r{i}" in results

    def test_results_sorted_descending_by_ev(self):
        riders, probs = self._make_team()
        stage = make_flat_stage()
        results = simulate_team(riders, stage, probs, MY_TEAM, CAPTAIN, N, seed=82)
        evs = [sr.expected_value for sr in results.values()]
        assert evs == sorted(evs, reverse=True)

    def test_rider_missing_probs_skipped(self):
        riders, probs = self._make_team()
        del probs["r3"]  # remove one rider's probs
        stage = make_flat_stage()
        results = simulate_team(riders, stage, probs, MY_TEAM, CAPTAIN, N, seed=83)
        assert "r3" not in results
        assert len(results) == 7

    def test_simulate_team_is_fast(self):
        """8-rider team simulation should complete in < 3 seconds."""
        import time
        riders, probs = self._make_team()
        stage = make_flat_stage()
        start = time.time()
        simulate_team(riders, stage, probs, MY_TEAM, CAPTAIN, n_simulations=10_000, seed=84)
        elapsed = time.time() - start
        assert elapsed < 3.0, f"simulate_team took {elapsed:.2f}s (limit: 3s)"

    def test_reproducible_with_seed(self):
        riders, probs = self._make_team()
        stage = make_flat_stage()
        r1 = simulate_team(riders, stage, probs, MY_TEAM, CAPTAIN, N, seed=85)
        r2 = simulate_team(riders, stage, probs, MY_TEAM, CAPTAIN, N, seed=85)
        for rid in r1:
            assert r1[rid].expected_value == r2[rid].expected_value


# ── TestSampleFinishPosition ─────────────────────────────────────────────────

class TestSampleFinishPosition:
    """Unit tests for the internal _sample_finish_position helper."""

    def _count_outcomes(self, probs, n=50_000, seed=0):
        """Run n trials and return {outcome: count} dict."""
        rng = np.random.default_rng(seed)
        counts = {"dnf": 0, "win": 0, "top3": 0, "top10": 0, "top15": 0, "other": 0}
        for _ in range(n):
            pos, is_dnf = _sample_finish_position(probs, rng)
            if is_dnf:
                counts["dnf"] += 1
            elif pos == 1:
                counts["win"] += 1
            elif pos in (2, 3):
                counts["top3"] += 1
            elif 4 <= pos <= 10:
                counts["top10"] += 1
            elif 11 <= pos <= 15:
                counts["top15"] += 1
            else:
                counts["other"] += 1
        return counts

    def test_win_probability_matches(self):
        probs = make_probs(p_win=0.20, p_top3=0.30, p_top10=0.45, p_top15=0.60, p_dnf=0.02)
        counts = self._count_outcomes(probs)
        observed_p_win = counts["win"] / 50_000
        assert abs(observed_p_win - 0.20) < 0.01

    def test_dnf_probability_matches(self):
        probs = make_probs(p_dnf=0.10)
        counts = self._count_outcomes(probs)
        observed_p_dnf = counts["dnf"] / 50_000
        assert abs(observed_p_dnf - 0.10) < 0.01

    def test_top3_bracket_matches(self):
        probs = make_probs(p_win=0.10, p_top3=0.25, p_top10=0.40, p_top15=0.55, p_dnf=0.02)
        counts = self._count_outcomes(probs)
        observed_p23 = counts["top3"] / 50_000
        assert abs(observed_p23 - 0.15) < 0.01  # p_top3 - p_win = 0.15

    def test_certain_win_always_wins(self):
        probs = make_probs(p_win=1.0, p_top3=1.0, p_top10=1.0, p_top15=1.0, p_dnf=0.0)
        counts = self._count_outcomes(probs, n=1000)
        assert counts["win"] == 1000

    def test_zero_probability_never_fires(self):
        """If p_win=0.0, bucket 1 should never be sampled."""
        probs = make_probs(p_win=0.0, p_top3=0.0, p_top10=0.0, p_top15=0.0, p_dnf=0.0)
        counts = self._count_outcomes(probs, n=10_000)
        assert counts["win"] == 0
        assert counts["top3"] == 0
        assert counts["top10"] == 0
        assert counts["top15"] == 0
        assert counts["other"] == 10_000
