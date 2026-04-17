"""
tests/test_optimizer.py — Unit tests for scoring/optimizer.py

Key fixture:
  - 16 riders: 8 GC riders (current team, all GC top-10),
               8 sprinters (pool, no GC position)
  - Flat stage
  - GC rider sim:   realistic flat-stage values: p10=+60k (guaranteed GC
                    standing value), ev=+70k, p95=+100k.
                    On a flat stage a GC top-10 rider finishes in the peloton,
                    earns 60–100k of GC standing value regardless of stage
                    outcome → floor is reliable but ceiling is modest.
  - Sprinter sim:   realistic flat-stage values: p10=+25k (bad day / missed
                    sprint), ev=+80k, p95=+300k (winning the stage).
                    Sprinters have higher EV and ceiling but more variable
                    floor because they can crash or miss the sprint.
  - bank=50_000_000 (not a binding constraint)
  - stages_remaining=10

Expected optimizer behaviour:
  ANCHOR     → 0 transfers: GC top-10 riders are hard-protected AND their
               p10 (+60k) is higher than sprinter p10 (+25k), confirming
               ANCHOR keeps GC riders for the right reason — guaranteed
               per-stage GC standing income, not artificially inflated p10.
  BALANCED   → swaps some GC riders where ev_gain (80k–70k=10k) > fee/stages
  AGGRESSIVE → swaps riders where p80 improves significantly
  ALL_IN     → swaps all 8 GC riders for sprinters (p95 300k vs 100k)
"""
import pytest

from scoring.engine import Rider, Stage
from scoring.optimizer import (
    RiskProfile,
    TransferAction,
    ProfileRecommendation,
    optimize,
    optimize_all_profiles,
    suggest_profile,
    format_briefing_table,
    _profile_metric,
    _buy_fee,
    _count_teams,
    _eval_swap,
)
from scoring.simulator import SimResult


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

RIDER_VALUE = 5_000_000  # all riders same price for easy budget reasoning


def _make_rider(
    rid: str,
    team_abbr: str,
    gc_position=None,
    status="active",
    jerseys=None,
) -> Rider:
    return Rider(
        holdet_id=rid,
        person_id=f"p_{rid}",
        team_id=f"t_{team_abbr}",
        name=f"Rider {rid}",
        team=f"Team {team_abbr}",
        team_abbr=team_abbr,
        value=RIDER_VALUE,
        start_value=RIDER_VALUE,
        points=0,
        status=status,
        gc_position=gc_position,
        jerseys=jerseys if jerseys is not None else [],
        in_my_team=False,
        is_captain=False,
    )


def _mountain_sim(rid: str) -> SimResult:
    """
    GC rider on a flat stage — realistic values.

    Even on a flat stage where GC riders are irrelevant to the sprint,
    they earn reliable GC standing value (60–100k per stage for positions
    1–10). This produces a high, stable floor. The ceiling is modest
    because they won't contest the sprint.
    """
    return SimResult(
        rider_id=rid,
        expected_value=70_000,
        std_dev=15_000,
        percentile_10=60_000,   # worst case: still earns GC standing value
        percentile_50=70_000,
        percentile_80=85_000,
        percentile_90=95_000,
        percentile_95=100_000,  # good day: GC standing + minor bonuses
        p_positive=0.95,
    )


def _sprinter_sim(rid: str) -> SimResult:
    """
    Sprinter on a flat stage — realistic values.

    Sprinters have higher EV and ceiling (winning pays ~300k+) but a
    lower floor than GC riders because crashes and missed sprints happen.
    A bad day still earns moderate value (15th–20th finish + Etapebonus).
    """
    return SimResult(
        rider_id=rid,
        expected_value=80_000,
        std_dev=90_000,
        percentile_10=25_000,   # bad day: crash / missed sprint, finishes 15–20th
        percentile_50=80_000,
        percentile_80=150_000,
        percentile_90=200_000,
        percentile_95=300_000,  # winning the stage
        p_positive=0.75,
    )


def _flat_stage() -> Stage:
    return Stage(
        number=5,
        race="giro_2026",
        stage_type="flat",
        distance_km=185.0,
        is_ttt=False,
        start_location="A",
        finish_location="B",
    )


@pytest.fixture
def flat_stage():
    return _flat_stage()


@pytest.fixture
def mountain_squad_ids():
    """8 GC mountain riders — current team holdet_ids."""
    return [f"R{i}" for i in range(1, 9)]


@pytest.fixture
def riders(mountain_squad_ids):
    """
    16-rider pool:
      R1–R8  mountain riders, GC top-10, teams TEAM_A (2) + TEAM_B (2) + TEAM_C (2) + TEAM_D (2)
      S1–S8  sprinters,       no GC,     teams TEAM_E (2) + TEAM_F (2) + TEAM_G (2) + TEAM_H (2)
    """
    mountain_teams = ["TEAM_A", "TEAM_A", "TEAM_B", "TEAM_B",
                      "TEAM_C", "TEAM_C", "TEAM_D", "TEAM_D"]
    sprint_teams   = ["TEAM_E", "TEAM_E", "TEAM_F", "TEAM_F",
                      "TEAM_G", "TEAM_G", "TEAM_H", "TEAM_H"]
    pool = []
    for i, (rid, team) in enumerate(zip(mountain_squad_ids, mountain_teams), start=1):
        pool.append(_make_rider(rid, team, gc_position=i))
    for j, (sid, team) in enumerate(
        zip([f"S{i}" for i in range(1, 9)], sprint_teams), start=1
    ):
        pool.append(_make_rider(sid, team, gc_position=None))
    return pool


@pytest.fixture
def sim_results(mountain_squad_ids):
    """Pre-computed SimResult for all 16 riders."""
    results = {}
    for rid in mountain_squad_ids:
        results[rid] = _mountain_sim(rid)
    for i in range(1, 9):
        sid = f"S{i}"
        results[sid] = _sprinter_sim(sid)
    return results


@pytest.fixture
def bank():
    return 50_000_000.0


@pytest.fixture
def stages_remaining():
    return 10


@pytest.fixture
def all_recommendations(riders, mountain_squad_ids, flat_stage, sim_results, bank, stages_remaining):
    return optimize_all_profiles(
        riders=riders,
        my_team=mountain_squad_ids,
        stage=flat_stage,
        probs={},
        sim_results=sim_results,
        bank=bank,
        rank=5000,
        total_participants=100_000,
        stages_remaining=stages_remaining,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TestRiskProfileEnum
# ═══════════════════════════════════════════════════════════════════════════════

class TestRiskProfileEnum:
    def test_has_four_members(self):
        assert len(RiskProfile) == 4

    def test_anchor_value(self):
        assert RiskProfile.ANCHOR.value == "anchor"

    def test_balanced_value(self):
        assert RiskProfile.BALANCED.value == "balanced"

    def test_aggressive_value(self):
        assert RiskProfile.AGGRESSIVE.value == "aggressive"

    def test_all_in_value(self):
        assert RiskProfile.ALL_IN.value == "all_in"

    def test_no_steady_or_lottery(self):
        values = {p.value for p in RiskProfile}
        assert "steady" not in values
        assert "lottery" not in values


# ═══════════════════════════════════════════════════════════════════════════════
# TestProfileMetric
# ═══════════════════════════════════════════════════════════════════════════════

class TestProfileMetric:
    def test_anchor_uses_p10(self):
        sim = _mountain_sim("R1")
        assert _profile_metric(sim, RiskProfile.ANCHOR) == sim.percentile_10

    def test_balanced_uses_ev(self):
        sim = _mountain_sim("R1")
        assert _profile_metric(sim, RiskProfile.BALANCED) == sim.expected_value

    def test_aggressive_uses_p80(self):
        sim = _mountain_sim("R1")
        assert _profile_metric(sim, RiskProfile.AGGRESSIVE) == sim.percentile_80

    def test_all_in_uses_p95(self):
        sim = _mountain_sim("R1")
        assert _profile_metric(sim, RiskProfile.ALL_IN) == sim.percentile_95


# ═══════════════════════════════════════════════════════════════════════════════
# TestBuyFee
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuyFee:
    def test_one_percent(self):
        assert _buy_fee(5_000_000) == 50_000

    def test_zero_value(self):
        assert _buy_fee(0) == 0

    def test_rounding(self):
        assert _buy_fee(100) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# TestEvalSwap
# ═══════════════════════════════════════════════════════════════════════════════

class TestEvalSwap:
    """Unit tests for the profile-specific swap acceptance logic."""

    def test_anchor_accepts_if_effective_gain_positive(self):
        # gain=100k, fee_per_stage=5k → effective=95k > 0
        score = _eval_swap(RiskProfile.ANCHOR, 100_000, 55_000, 40_000, 50_000, 10)
        assert score is not None and score > 0

    def test_anchor_rejects_if_effective_gain_zero_or_negative(self):
        # gain=3k, fee_per_stage=5k → effective=-2k ≤ 0
        score = _eval_swap(RiskProfile.ANCHOR, 3_000, 55_000, 40_000, 50_000, 10)
        assert score is None

    def test_balanced_accepts_if_ev_gain_exceeds_threshold(self):
        # ev_gain = 55k-40k = 15k, threshold = 50k/10 = 5k → accept
        score = _eval_swap(RiskProfile.BALANCED, 15_000, 55_000, 40_000, 50_000, 10)
        assert score is not None and score > 0

    def test_balanced_rejects_if_ev_gain_below_threshold(self):
        # ev_gain = 41k-40k = 1k < threshold 5k → reject
        score = _eval_swap(RiskProfile.BALANCED, 1_000, 41_000, 40_000, 50_000, 10)
        assert score is None

    def test_aggressive_accepts_high_p80_gain(self):
        # gain=90k > 0, ev_change=15k > -30k → accept
        score = _eval_swap(RiskProfile.AGGRESSIVE, 90_000, 55_000, 40_000, 50_000, 10)
        assert score is not None and score > 0

    def test_aggressive_rejects_negative_gain(self):
        score = _eval_swap(RiskProfile.AGGRESSIVE, -1_000, 55_000, 40_000, 50_000, 10)
        assert score is None

    def test_aggressive_rejects_if_ev_drop_too_large_and_p80_gain_small(self):
        # ev_change = 5k-40k = -35k < -30k, gain=70k < 80k → reject
        score = _eval_swap(RiskProfile.AGGRESSIVE, 70_000, 5_000, 40_000, 50_000, 10)
        assert score is None

    def test_aggressive_accepts_if_ev_drop_large_but_p80_gain_sufficient(self):
        # ev_change = 5k-40k = -35k < -30k, gain=90k >= 80k → accept
        score = _eval_swap(RiskProfile.AGGRESSIVE, 90_000, 5_000, 40_000, 50_000, 10)
        assert score is not None and score > 0

    def test_all_in_accepts_any_positive_gain(self):
        score = _eval_swap(RiskProfile.ALL_IN, 1, 55_000, 40_000, 50_000, 10)
        assert score is not None and score > 0

    def test_all_in_rejects_zero_or_negative_gain(self):
        score = _eval_swap(RiskProfile.ALL_IN, 0, 55_000, 40_000, 50_000, 10)
        assert score is None
        score2 = _eval_swap(RiskProfile.ALL_IN, -100, 55_000, 40_000, 50_000, 10)
        assert score2 is None


# ═══════════════════════════════════════════════════════════════════════════════
# TestProfileRecommendationSchema
# ═══════════════════════════════════════════════════════════════════════════════

class TestProfileRecommendationSchema:
    def test_returns_profile_recommendation(self, all_recommendations):
        for profile, rec in all_recommendations.items():
            assert isinstance(rec, ProfileRecommendation), f"{profile}: wrong type"

    def test_profile_field_matches_key(self, all_recommendations):
        for profile, rec in all_recommendations.items():
            assert rec.profile == profile

    def test_captain_is_string(self, all_recommendations):
        for rec in all_recommendations.values():
            assert isinstance(rec.captain, str) and rec.captain != ""

    def test_expected_value_is_numeric(self, all_recommendations):
        for rec in all_recommendations.values():
            assert isinstance(rec.expected_value, (int, float))

    def test_upside_90pct_is_numeric(self, all_recommendations):
        for rec in all_recommendations.values():
            assert isinstance(rec.upside_90pct, (int, float))

    def test_downside_10pct_is_numeric(self, all_recommendations):
        for rec in all_recommendations.values():
            assert isinstance(rec.downside_10pct, (int, float))

    def test_transfer_cost_non_negative(self, all_recommendations):
        for rec in all_recommendations.values():
            assert rec.transfer_cost >= 0

    def test_reasoning_non_empty(self, all_recommendations):
        for rec in all_recommendations.values():
            assert isinstance(rec.reasoning, str) and len(rec.reasoning) > 0

    def test_transfers_is_list(self, all_recommendations):
        for rec in all_recommendations.values():
            assert isinstance(rec.transfers, list)

    def test_transfer_actions_have_required_fields(self, all_recommendations):
        for rec in all_recommendations.values():
            for t in rec.transfers:
                assert isinstance(t, TransferAction)
                assert t.action in ("sell", "buy")
                assert isinstance(t.rider_id, str)
                assert isinstance(t.rider_name, str)
                assert isinstance(t.value, int)
                assert isinstance(t.fee, int)
                assert t.fee == 0 or t.action == "buy"


# ═══════════════════════════════════════════════════════════════════════════════
# TestConstraints
# ═══════════════════════════════════════════════════════════════════════════════

class TestConstraints:
    """Constraints enforced for all profiles."""

    def _final_squad(self, riders, my_team, rec):
        """Reconstruct final squad from transfers."""
        squad = set(my_team)
        for t in rec.transfers:
            if t.action == "sell":
                squad.discard(t.rider_id)
            else:
                squad.add(t.rider_id)
        return squad

    def _rider_map(self, riders):
        return {r.holdet_id: r for r in riders}

    def test_squad_size_is_8_for_all_profiles(self, riders, mountain_squad_ids, flat_stage, sim_results, bank, stages_remaining, all_recommendations):
        rm = self._rider_map(riders)
        for profile, rec in all_recommendations.items():
            squad = self._final_squad(rm, mountain_squad_ids, rec)
            assert len(squad) == 8, f"{profile}: squad size {len(squad)} ≠ 8"

    def test_max_two_riders_per_team(self, riders, mountain_squad_ids, flat_stage, sim_results, bank, stages_remaining, all_recommendations):
        rm = self._rider_map(riders)
        for profile, rec in all_recommendations.items():
            squad = self._final_squad(rm, mountain_squad_ids, rec)
            team_counts: dict = {}
            for rid in squad:
                abbr = rm[rid].team_abbr
                team_counts[abbr] = team_counts.get(abbr, 0) + 1
            for team, count in team_counts.items():
                assert count <= 2, (
                    f"{profile}: team {team} has {count} riders (max 2)"
                )

    def test_no_dns_dnf_riders_in_final_squad(self, riders, flat_stage, sim_results, bank, stages_remaining):
        """DNS rider on team must be sold, not in final squad."""
        # Replace R1 with a DNS rider
        dns_riders = [
            _make_rider("R1", "TEAM_A", gc_position=1, status="dns"),
        ] + [r for r in riders if r.holdet_id != "R1"]
        my_team = [f"R{i}" for i in range(1, 9)]

        rec = optimize(
            riders=dns_riders,
            my_team=my_team,
            stage=flat_stage,
            probs={},
            sim_results=sim_results,
            bank=bank,
            risk_profile=RiskProfile.BALANCED,
            rank=None,
            total_participants=None,
            stages_remaining=stages_remaining,
        )
        final_squad = set(my_team)
        for t in rec.transfers:
            if t.action == "sell":
                final_squad.discard(t.rider_id)
            else:
                final_squad.add(t.rider_id)

        rider_map = {r.holdet_id: r for r in dns_riders}
        for rid in final_squad:
            assert rider_map[rid].status == "active", (
                f"DNS/DNF rider {rid} ended up in final squad"
            )

    def test_captain_in_final_squad(self, riders, mountain_squad_ids, flat_stage, sim_results, bank, stages_remaining, all_recommendations):
        rm = {r.holdet_id: r for r in riders}
        for profile, rec in all_recommendations.items():
            squad = set(mountain_squad_ids)
            for t in rec.transfers:
                if t.action == "sell":
                    squad.discard(t.rider_id)
                else:
                    squad.add(t.rider_id)
            assert rec.captain in squad, (
                f"{profile}: captain {rec.captain} not in final squad {squad}"
            )

    def test_budget_not_exceeded(self, riders, mountain_squad_ids, flat_stage, sim_results, bank, stages_remaining, all_recommendations):
        rm = {r.holdet_id: r for r in riders}
        for profile, rec in all_recommendations.items():
            sell_proceeds = sum(
                rm[t.rider_id].value for t in rec.transfers
                if t.action == "sell" and t.rider_id in rm
            )
            buy_costs = sum(
                t.value + t.fee for t in rec.transfers if t.action == "buy"
            )
            net_spend = buy_costs - sell_proceeds
            assert net_spend <= bank + 1, (
                f"{profile}: net spend {net_spend:,} exceeds bank {bank:,}"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# TestProfileBehaviour (done conditions)
# ═══════════════════════════════════════════════════════════════════════════════

class TestProfileBehaviour:
    """
    Verifies the key done conditions:
      1. ALL_IN has more sprinters than ANCHOR on flat stage (mountain squad → flat switch)
      2. ANCHOR has more GC riders than ALL_IN
      3. AGGRESSIVE and ALL_IN transfer counts > ANCHOR
    """

    def _final_squad(self, my_team, rec):
        squad = set(my_team)
        for t in rec.transfers:
            if t.action == "sell":
                squad.discard(t.rider_id)
            else:
                squad.add(t.rider_id)
        return squad

    def _gc_rider_count(self, squad, riders):
        rm = {r.holdet_id: r for r in riders}
        return sum(
            1 for rid in squad
            if rm.get(rid) and rm[rid].gc_position is not None
        )

    def _sprinter_count(self, squad, riders):
        rm = {r.holdet_id: r for r in riders}
        return sum(
            1 for rid in squad
            if rm.get(rid) and rm[rid].gc_position is None
        )

    def _transfer_count(self, rec):
        return sum(1 for t in rec.transfers if t.action == "buy")

    def test_anchor_makes_zero_transfers_due_to_gc_protection(
        self, riders, mountain_squad_ids, flat_stage, sim_results, bank, stages_remaining, all_recommendations
    ):
        """ANCHOR protects all GC top-10 riders → 0 voluntary transfers."""
        rec = all_recommendations[RiskProfile.ANCHOR]
        assert self._transfer_count(rec) == 0

    def test_all_in_makes_more_transfers_than_anchor(
        self, riders, mountain_squad_ids, flat_stage, sim_results, bank, stages_remaining, all_recommendations
    ):
        anchor_count = self._transfer_count(all_recommendations[RiskProfile.ANCHOR])
        allin_count = self._transfer_count(all_recommendations[RiskProfile.ALL_IN])
        assert allin_count > anchor_count, (
            f"ALL_IN transfers ({allin_count}) should exceed ANCHOR ({anchor_count})"
        )

    def test_aggressive_makes_more_transfers_than_anchor(
        self, riders, mountain_squad_ids, flat_stage, sim_results, bank, stages_remaining, all_recommendations
    ):
        anchor_count = self._transfer_count(all_recommendations[RiskProfile.ANCHOR])
        agg_count = self._transfer_count(all_recommendations[RiskProfile.AGGRESSIVE])
        assert agg_count > anchor_count, (
            f"AGGRESSIVE transfers ({agg_count}) should exceed ANCHOR ({anchor_count})"
        )

    def test_all_in_squad_has_more_sprinters_than_anchor(
        self, riders, mountain_squad_ids, flat_stage, sim_results, bank, stages_remaining, all_recommendations
    ):
        anchor_squad = self._final_squad(mountain_squad_ids, all_recommendations[RiskProfile.ANCHOR])
        allin_squad = self._final_squad(mountain_squad_ids, all_recommendations[RiskProfile.ALL_IN])
        anchor_sprinters = self._sprinter_count(anchor_squad, riders)
        allin_sprinters = self._sprinter_count(allin_squad, riders)
        assert allin_sprinters > anchor_sprinters, (
            f"ALL_IN sprinters ({allin_sprinters}) should exceed ANCHOR ({anchor_sprinters})"
        )

    def test_anchor_squad_has_more_gc_riders_than_all_in(
        self, riders, mountain_squad_ids, flat_stage, sim_results, bank, stages_remaining, all_recommendations
    ):
        anchor_squad = self._final_squad(mountain_squad_ids, all_recommendations[RiskProfile.ANCHOR])
        allin_squad = self._final_squad(mountain_squad_ids, all_recommendations[RiskProfile.ALL_IN])
        anchor_gc = self._gc_rider_count(anchor_squad, riders)
        allin_gc = self._gc_rider_count(allin_squad, riders)
        assert anchor_gc > allin_gc, (
            f"ANCHOR GC riders ({anchor_gc}) should exceed ALL_IN ({allin_gc})"
        )

    def test_all_in_upside_exceeds_anchor_upside(self, all_recommendations):
        anchor = all_recommendations[RiskProfile.ANCHOR]
        allin = all_recommendations[RiskProfile.ALL_IN]
        assert allin.upside_90pct > anchor.upside_90pct

    def test_anchor_floor_exceeds_all_in_floor(self, all_recommendations):
        anchor = all_recommendations[RiskProfile.ANCHOR]
        allin = all_recommendations[RiskProfile.ALL_IN]
        assert anchor.downside_10pct > allin.downside_10pct


# ═══════════════════════════════════════════════════════════════════════════════
# TestCaptainSelection
# ═══════════════════════════════════════════════════════════════════════════════

class TestCaptainSelection:
    """
    Captain rules per profile:
      ANCHOR     → highest EV rider
      BALANCED   → best EV/std_dev ratio
      AGGRESSIVE → highest p90
      ALL_IN     → highest p95
    """

    def _make_varied_sim(self, rid, ev, std_dev, p10, p50, p80, p90, p95):
        return SimResult(
            rider_id=rid,
            expected_value=ev,
            std_dev=std_dev,
            percentile_10=p10,
            percentile_50=p50,
            percentile_80=p80,
            percentile_90=p90,
            percentile_95=p95,
            p_positive=0.7,
        )

    def test_anchor_captain_has_highest_ev(self, flat_stage):
        # R1 has highest EV; all are GC top-10 (protected), no transfers → captain from current squad
        riders = [
            _make_rider("R1", "TEAM_A", gc_position=1),
            _make_rider("R2", "TEAM_A", gc_position=2),
            _make_rider("R3", "TEAM_B", gc_position=3),
            _make_rider("R4", "TEAM_B", gc_position=4),
            _make_rider("R5", "TEAM_C", gc_position=5),
            _make_rider("R6", "TEAM_C", gc_position=6),
            _make_rider("R7", "TEAM_D", gc_position=7),
            _make_rider("R8", "TEAM_D", gc_position=8),
        ]
        sim_results = {
            "R1": self._make_varied_sim("R1", ev=100_000, std_dev=20_000, p10=10_000, p50=100_000, p80=150_000, p90=180_000, p95=200_000),
            "R2": self._make_varied_sim("R2", ev=60_000, std_dev=15_000, p10=20_000, p50=60_000, p80=80_000, p90=90_000, p95=100_000),
            "R3": self._make_varied_sim("R3", ev=40_000, std_dev=10_000, p10=15_000, p50=40_000, p80=55_000, p90=65_000, p95=70_000),
            "R4": self._make_varied_sim("R4", ev=40_000, std_dev=10_000, p10=15_000, p50=40_000, p80=55_000, p90=65_000, p95=70_000),
            "R5": self._make_varied_sim("R5", ev=40_000, std_dev=10_000, p10=15_000, p50=40_000, p80=55_000, p90=65_000, p95=70_000),
            "R6": self._make_varied_sim("R6", ev=40_000, std_dev=10_000, p10=15_000, p50=40_000, p80=55_000, p90=65_000, p95=70_000),
            "R7": self._make_varied_sim("R7", ev=40_000, std_dev=10_000, p10=15_000, p50=40_000, p80=55_000, p90=65_000, p95=70_000),
            "R8": self._make_varied_sim("R8", ev=40_000, std_dev=10_000, p10=15_000, p50=40_000, p80=55_000, p90=65_000, p95=70_000),
        }
        my_team = [f"R{i}" for i in range(1, 9)]
        rec = optimize(
            riders=riders, my_team=my_team, stage=flat_stage, probs={},
            sim_results=sim_results, bank=50_000_000,
            risk_profile=RiskProfile.ANCHOR, rank=None, total_participants=None,
            stages_remaining=10,
        )
        assert rec.captain == "R1"  # R1 has highest EV

    def test_all_in_captain_has_highest_p95(self, flat_stage):
        # Small squad where one rider has clearly highest p95
        riders = [
            _make_rider("R1", "TEAM_A", gc_position=1),
            _make_rider("R2", "TEAM_A", gc_position=2),
            _make_rider("R3", "TEAM_B", gc_position=3),
            _make_rider("R4", "TEAM_B", gc_position=4),
            _make_rider("S1", "TEAM_C"),  # sprinter, highest p95
            _make_rider("S2", "TEAM_C"),
            _make_rider("S3", "TEAM_D"),
            _make_rider("S4", "TEAM_D"),
        ]
        sim_results = {}
        for i in range(1, 5):
            sim_results[f"R{i}"] = self._make_varied_sim(
                f"R{i}", ev=40_000, std_dev=20_000, p10=20_000, p50=40_000,
                p80=60_000, p90=70_000, p95=80_000,
            )
        # S1 has highest p95
        sim_results["S1"] = self._make_varied_sim(
            "S1", ev=55_000, std_dev=100_000, p10=-20_000, p50=60_000,
            p80=150_000, p90=200_000, p95=400_000,
        )
        for sid in ("S2", "S3", "S4"):
            sim_results[sid] = self._make_varied_sim(
                sid, ev=50_000, std_dev=90_000, p10=-20_000, p50=55_000,
                p80=140_000, p90=190_000, p95=300_000,
            )
        my_team = ["R1", "R2", "R3", "R4", "S1", "S2", "S3", "S4"]
        rec = optimize(
            riders=riders, my_team=my_team, stage=flat_stage, probs={},
            sim_results=sim_results, bank=50_000_000,
            risk_profile=RiskProfile.ALL_IN, rank=None, total_participants=None,
            stages_remaining=10,
        )
        assert rec.captain == "S1"


# ═══════════════════════════════════════════════════════════════════════════════
# TestSuggestProfile
# ═══════════════════════════════════════════════════════════════════════════════

class TestSuggestProfile:
    def test_top_01pct_returns_anchor(self):
        profile, reason = suggest_profile(rank=50, total=100_000, stages_remaining=10)
        assert profile == RiskProfile.ANCHOR
        assert isinstance(reason, str) and len(reason) > 0

    def test_top_1pct_returns_balanced(self):
        profile, reason = suggest_profile(rank=500, total=100_000, stages_remaining=10)
        assert profile == RiskProfile.BALANCED

    def test_few_stages_left_returns_all_in(self):
        profile, reason = suggest_profile(rank=50_000, total=100_000, stages_remaining=3)
        assert profile == RiskProfile.ALL_IN

    def test_large_gap_returns_aggressive(self):
        # gap = 900_000 - 100 = 899_900 > 10 * 80_000 = 800_000
        profile, reason = suggest_profile(
            rank=900_000, total=1_000_000, stages_remaining=10, target_rank=100
        )
        assert profile == RiskProfile.AGGRESSIVE

    def test_standard_situation_returns_balanced(self):
        # rank 5000, gap=4900, stages=10, threshold=800k → gap < threshold → BALANCED
        profile, reason = suggest_profile(
            rank=5_000, total=100_000, stages_remaining=10, target_rank=100
        )
        assert profile == RiskProfile.BALANCED

    def test_returns_tuple_of_two(self):
        result = suggest_profile(rank=1000, total=100_000, stages_remaining=10)
        assert isinstance(result, tuple) and len(result) == 2

    def test_reason_is_non_empty_string(self):
        _, reason = suggest_profile(rank=1000, total=100_000, stages_remaining=10)
        assert isinstance(reason, str) and len(reason) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# TestOptimizeAllProfiles
# ═══════════════════════════════════════════════════════════════════════════════

class TestOptimizeAllProfiles:
    def test_returns_four_profiles(self, all_recommendations):
        assert len(all_recommendations) == 4

    def test_all_profiles_present(self, all_recommendations):
        for profile in RiskProfile:
            assert profile in all_recommendations

    def test_each_value_is_profile_recommendation(self, all_recommendations):
        for rec in all_recommendations.values():
            assert isinstance(rec, ProfileRecommendation)


# ═══════════════════════════════════════════════════════════════════════════════
# TestForcedSells
# ═══════════════════════════════════════════════════════════════════════════════

class TestForcedSells:
    def test_dns_rider_is_sold(self, riders, flat_stage, sim_results, bank, stages_remaining):
        dns_riders = [
            _make_rider("R1", "TEAM_A", gc_position=1, status="dns"),
        ] + [r for r in riders if r.holdet_id != "R1"]
        my_team = [f"R{i}" for i in range(1, 9)]

        rec = optimize(
            riders=dns_riders, my_team=my_team, stage=flat_stage, probs={},
            sim_results=sim_results, bank=bank,
            risk_profile=RiskProfile.BALANCED, rank=None, total_participants=None,
            stages_remaining=stages_remaining,
        )
        sell_ids = {t.rider_id for t in rec.transfers if t.action == "sell"}
        assert "R1" in sell_ids

    def test_dns_rider_sell_fee_is_zero(self, riders, flat_stage, sim_results, bank, stages_remaining):
        dns_riders = [
            _make_rider("R1", "TEAM_A", gc_position=1, status="dns"),
        ] + [r for r in riders if r.holdet_id != "R1"]
        my_team = [f"R{i}" for i in range(1, 9)]

        rec = optimize(
            riders=dns_riders, my_team=my_team, stage=flat_stage, probs={},
            sim_results=sim_results, bank=bank,
            risk_profile=RiskProfile.BALANCED, rank=None, total_participants=None,
            stages_remaining=stages_remaining,
        )
        forced_sells = [t for t in rec.transfers if t.action == "sell" and t.rider_id == "R1"]
        assert len(forced_sells) == 1
        assert forced_sells[0].fee == 0

    def test_dnf_rider_replaced_by_eligible(self, riders, flat_stage, sim_results, bank, stages_remaining):
        dnf_riders = [
            _make_rider("R1", "TEAM_A", gc_position=1, status="dnf"),
        ] + [r for r in riders if r.holdet_id != "R1"]
        my_team = [f"R{i}" for i in range(1, 9)]

        rec = optimize(
            riders=dnf_riders, my_team=my_team, stage=flat_stage, probs={},
            sim_results=sim_results, bank=bank,
            risk_profile=RiskProfile.BALANCED, rank=None, total_participants=None,
            stages_remaining=stages_remaining,
        )
        buy_ids = {t.rider_id for t in rec.transfers if t.action == "buy"}
        assert len(buy_ids) >= 1  # at least one replacement bought


# ═══════════════════════════════════════════════════════════════════════════════
# TestFormatBriefingTable
# ═══════════════════════════════════════════════════════════════════════════════

class TestFormatBriefingTable:
    def test_returns_string(self, riders, mountain_squad_ids, flat_stage, sim_results, bank, stages_remaining, all_recommendations):
        rm = {r.holdet_id: r for r in riders}
        output = format_briefing_table(all_recommendations, rm, flat_stage)
        assert isinstance(output, str)

    def test_contains_all_profile_names(self, riders, mountain_squad_ids, flat_stage, sim_results, bank, stages_remaining, all_recommendations):
        rm = {r.holdet_id: r for r in riders}
        output = format_briefing_table(all_recommendations, rm, flat_stage)
        for header in ("ANCHOR", "BALANCED", "AGGRESSIVE", "ALL-IN"):
            assert header in output, f"'{header}' not found in briefing table"

    def test_contains_stage_info(self, riders, mountain_squad_ids, flat_stage, sim_results, bank, stages_remaining, all_recommendations):
        rm = {r.holdet_id: r for r in riders}
        output = format_briefing_table(all_recommendations, rm, flat_stage)
        assert "flat" in output.lower() or "FLAT" in output
