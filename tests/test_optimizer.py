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
    _eval_team,
    _eval_cache,
    NOISE_FLOOR,
    apply_intent_to_ev,
    compute_transfer_penalty,
)
from scoring.probabilities import RiderProb
from scoring.simulator import SimResult, TeamSimResult
from scoring.stage_intent import StageIntent, compute_stage_intent


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


def _build_pool():
    """Build the shared 16-rider pool for module-scoped fixtures."""
    mountain_squad_ids = [f"R{i}" for i in range(1, 9)]
    mountain_teams = ["TEAM_A", "TEAM_A", "TEAM_B", "TEAM_B",
                      "TEAM_C", "TEAM_C", "TEAM_D", "TEAM_D"]
    sprint_teams   = ["TEAM_E", "TEAM_E", "TEAM_F", "TEAM_F",
                      "TEAM_G", "TEAM_G", "TEAM_H", "TEAM_H"]
    pool = []
    for i, (rid, team) in enumerate(zip(mountain_squad_ids, mountain_teams), start=1):
        pool.append(_make_rider(rid, team, gc_position=i))
    for sid, team in zip([f"S{i}" for i in range(1, 9)], sprint_teams):
        pool.append(_make_rider(sid, team, gc_position=None))
    sr = {}
    for rid in mountain_squad_ids:
        sr[rid] = _mountain_sim(rid)
    for i in range(1, 9):
        sr[f"S{i}"] = _sprinter_sim(f"S{i}")
    return pool, mountain_squad_ids, sr


@pytest.fixture(scope="module")
def all_recommendations():
    """
    Module-scoped (computed once). probs={} so all riders equal in simulation.
    n_sim=10 for speed. Used for schema/constraint tests.
    """
    pool, squad_ids, sr = _build_pool()
    stage = _flat_stage()
    return optimize_all_profiles(
        riders=pool, my_team=squad_ids, stage=stage, probs={}, sim_results=sr,
        bank=50_000_000.0, rank=5000, total_participants=100_000, stages_remaining=10,
        n_sim=10,
    )


@pytest.fixture(scope="module")
def behaviour_recs():
    """
    Module-scoped. Proper probs: sprinters dominant on flat (p_top15=0.45 vs 0.08).
    n_sim=50 for reliable directional results. Used by TestProfileBehaviour.
    """
    pool, squad_ids, sr = _build_pool()
    stage = _flat_stage()
    probs = {}
    for r in pool:
        is_sprint = r.gc_position is None
        probs[r.holdet_id] = RiderProb(
            rider_id=r.holdet_id, stage_number=stage.number,
            p_win=0.05 if is_sprint else 0.002,
            p_top3=0.15 if is_sprint else 0.008,
            p_top10=0.35 if is_sprint else 0.04,
            p_top15=0.45 if is_sprint else 0.08,
            p_dnf=0.01,
        )
    return optimize_all_profiles(
        riders=pool, my_team=squad_ids, stage=stage, probs=probs, sim_results=sr,
        bank=50_000_000.0, rank=5000, total_participants=100_000, stages_remaining=10,
        n_sim=50,
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
        # ev_gain = 55k-40k = 15k > fee_threshold 5k, gain=50k > NOISE_FLOOR → accept
        score = _eval_swap(RiskProfile.BALANCED, 50_000, 55_000, 40_000, 50_000, 10)
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

    def test_all_in_accepts_gain_above_noise_floor(self):
        # gain must exceed NOISE_FLOOR (20k) — small gains are simulation noise
        score = _eval_swap(RiskProfile.ALL_IN, 25_000, 55_000, 40_000, 50_000, 10)
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
            n_sim=10,
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

    def test_anchor_makes_zero_transfers_due_to_gc_protection(self, behaviour_recs):
        """ANCHOR protects all GC top-10 riders → 0 voluntary transfers."""
        rec = behaviour_recs[RiskProfile.ANCHOR]
        assert self._transfer_count(rec) == 0

    def test_all_in_makes_more_transfers_than_anchor(self, behaviour_recs):
        anchor_count = self._transfer_count(behaviour_recs[RiskProfile.ANCHOR])
        allin_count = self._transfer_count(behaviour_recs[RiskProfile.ALL_IN])
        assert allin_count > anchor_count, (
            f"ALL_IN transfers ({allin_count}) should exceed ANCHOR ({anchor_count})"
        )

    def test_aggressive_makes_more_transfers_than_anchor(self, behaviour_recs):
        anchor_count = self._transfer_count(behaviour_recs[RiskProfile.ANCHOR])
        agg_count = self._transfer_count(behaviour_recs[RiskProfile.AGGRESSIVE])
        assert agg_count > anchor_count, (
            f"AGGRESSIVE transfers ({agg_count}) should exceed ANCHOR ({anchor_count})"
        )

    def test_all_in_squad_has_more_sprinters_than_anchor(self, behaviour_recs):
        pool, squad_ids, _ = _build_pool()
        anchor_squad = self._final_squad(squad_ids, behaviour_recs[RiskProfile.ANCHOR])
        allin_squad  = self._final_squad(squad_ids, behaviour_recs[RiskProfile.ALL_IN])
        anchor_sprinters = self._sprinter_count(anchor_squad, pool)
        allin_sprinters  = self._sprinter_count(allin_squad, pool)
        assert allin_sprinters > anchor_sprinters, (
            f"ALL_IN sprinters ({allin_sprinters}) should exceed ANCHOR ({anchor_sprinters})"
        )

    def test_anchor_squad_has_more_gc_riders_than_all_in(self, behaviour_recs):
        pool, squad_ids, _ = _build_pool()
        anchor_squad = self._final_squad(squad_ids, behaviour_recs[RiskProfile.ANCHOR])
        allin_squad  = self._final_squad(squad_ids, behaviour_recs[RiskProfile.ALL_IN])
        anchor_gc = self._gc_rider_count(anchor_squad, pool)
        allin_gc  = self._gc_rider_count(allin_squad, pool)
        assert anchor_gc > allin_gc, (
            f"ANCHOR GC riders ({anchor_gc}) should exceed ALL_IN ({allin_gc})"
        )

    def test_all_in_upside_exceeds_anchor_upside(self, behaviour_recs):
        anchor = behaviour_recs[RiskProfile.ANCHOR]
        allin  = behaviour_recs[RiskProfile.ALL_IN]
        assert allin.upside_90pct > anchor.upside_90pct

    def test_anchor_floor_exceeds_all_in_floor(self, behaviour_recs):
        anchor = behaviour_recs[RiskProfile.ANCHOR]
        allin  = behaviour_recs[RiskProfile.ALL_IN]
        assert anchor.downside_10pct > allin.downside_10pct


# ═══════════════════════════════════════════════════════════════════════════════
# TestAnchorRealisticFixtures (A2 done conditions)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAnchorRealisticFixtures:
    """
    A2: Verify ANCHOR retains GC riders for the right reason — guaranteed
    per-stage GC standing income — not artificially inflated test fixtures.

    Fixture facts (from _mountain_sim / _sprinter_sim):
      GC rider:   p10 = +60k (reliable: GC standing value even on bad flat day)
                  EV  = +70k
      Sprinter:   p10 = +25k (lower floor: crash / missed sprint risk)
                  EV  = +80k (higher EV and ceiling)

    ANCHOR maximises p10. GC rider p10 (60k) > sprinter p10 (25k) → ANCHOR
    correctly keeps GC riders. This is "the right reason".
    """

    def test_gc_rider_p10_exceeds_sprinter_p10(self):
        """Document that GC standing income produces higher p10 than sprinter floor."""
        gc_sim = _mountain_sim("R1")
        sp_sim = _sprinter_sim("S1")
        assert gc_sim.percentile_10 > sp_sim.percentile_10, (
            f"GC p10 ({gc_sim.percentile_10:,}) should exceed "
            f"sprinter p10 ({sp_sim.percentile_10:,})"
        )

    def test_anchor_downside_10pct_is_positive(
        self, riders, mountain_squad_ids, flat_stage, sim_results, bank, stages_remaining, all_recommendations
    ):
        """ANCHOR recommendation's p10 is positive — GC standing income guaranteed even on bad days."""
        anchor = all_recommendations[RiskProfile.ANCHOR]
        assert anchor.downside_10pct > 0, (
            f"ANCHOR p10 ({anchor.downside_10pct:,}) should be positive "
            "(GC standing value guaranteed even when not contesting sprint)"
        )

    def test_anchor_does_not_protect_dns_gc_rider(
        self, riders, flat_stage, sim_results, bank, stages_remaining
    ):
        """ANCHOR must sell a DNS GC top-10 rider — protection doesn't apply to unavailable riders."""
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
            risk_profile=RiskProfile.ANCHOR,
            rank=None,
            total_participants=None,
            stages_remaining=stages_remaining,
            n_sim=10,
        )
        sold_ids = {t.rider_id for t in rec.transfers if t.action == "sell"}
        assert "R1" in sold_ids, (
            "ANCHOR should sell DNS GC rider R1 — status=dns overrides GC protection"
        )


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

    def test_anchor_captain_has_highest_p10(self, flat_stage):
        # R1 has highest p10 (floor); all are GC top-10 (protected), no transfers → captain from current squad
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
            "R1": self._make_varied_sim("R1", ev=100_000, std_dev=20_000, p10=30_000, p50=100_000, p80=150_000, p90=180_000, p95=200_000),
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
            stages_remaining=10, n_sim=10,
        )
        assert rec.captain == "R1"  # R1 has highest p10 (floor)

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
            stages_remaining=10, n_sim=10,
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


# ═══════════════════════════════════════════════════════════════════════════════
# TestEmptyTeamFill
# ═══════════════════════════════════════════════════════════════════════════════

class TestEmptyTeamFill:
    """Starting from an empty team, all 4 profiles must return exactly 8 riders."""

    def test_optimizer_always_returns_8_riders(self):
        """
        With an empty my_team and 50M budget, all 4 profiles must fill exactly 8.
        Uses 16 riders across 8 teams (2 per team) with a range of values so the
        budget-aware fill and emergency fill paths are exercised.
        """
        # 16 riders, 8 teams × 2, values 1M–8M so even 8 cheap ones fit in 50M
        riders = []
        sim_results = {}
        for i in range(1, 17):
            team = f"T{(i - 1) // 2 + 1}"  # T1..T8, 2 riders each
            value = (i % 8 + 1) * 1_000_000  # values cycle 1M–8M
            r = Rider(
                holdet_id=f"X{i}",
                person_id=f"p{i}",
                team_id=f"tid{team}",
                name=f"Rider X{i}",
                team=f"Team {team}",
                team_abbr=team,
                value=value,
                start_value=value,
                points=0,
                status="active",
                gc_position=None,
                jerseys=[],
                in_my_team=False,
                is_captain=False,
            )
            riders.append(r)
            sim_results[f"X{i}"] = SimResult(
                rider_id=f"X{i}",
                expected_value=50_000,
                std_dev=20_000,
                percentile_10=10_000,
                percentile_50=50_000,
                percentile_80=80_000,
                percentile_90=100_000,
                percentile_95=120_000,
                p_positive=0.8,
            )

        stage = _flat_stage()

        for profile in RiskProfile:
            rec = optimize(
                riders=riders,
                my_team=[],          # empty — building from scratch
                stage=stage,
                probs={},
                sim_results=sim_results,
                bank=50_000_000,
                risk_profile=profile,
                rank=None,
                total_participants=None,
                stages_remaining=10,
            )
            # Reconstruct final squad from transfers
            squad: set = set()
            for t in rec.transfers:
                if t.action == "buy":
                    squad.add(t.rider_id)
                elif t.action == "sell":
                    squad.discard(t.rider_id)
            assert len(squad) == 8, (
                f"{profile.value}: expected 8 riders, got {len(squad)}"
            )

    def test_optimizer_fills_8_from_real_budget(self):
        """
        Realistic budget scenario: one rider at 17.5M would eat 35% of the budget
        alone. The budget-aware knapsack must still produce 8 riders for all profiles.
        Rider pool: 1×17.5M, 2×10M, 3×9M, 5×8M, 10×5M, rest at 3M.
        Budget: 50M. Empty team.
        """
        import itertools

        raw_values = (
            [17_500_000] * 1 +
            [10_000_000] * 2 +
            [9_000_000] * 3 +
            [8_000_000] * 5 +
            [5_000_000] * 10 +
            [3_000_000] * 5   # 26 riders total, 13 teams × 2
        )
        riders = []
        sim_results = {}
        team_cycle = itertools.cycle([f"TM{i}" for i in range(1, 14)])
        team_counts_local: dict = {}
        for i, value in enumerate(raw_values, start=1):
            # Assign to teams ensuring max 2 per team
            while True:
                team = next(team_cycle)
                if team_counts_local.get(team, 0) < 2:
                    team_counts_local[team] = team_counts_local.get(team, 0) + 1
                    break
            rid = f"RB{i}"
            r = Rider(
                holdet_id=rid,
                person_id=f"p{i}",
                team_id=f"tid{team}",
                name=f"Rider {i}",
                team=f"Team {team}",
                team_abbr=team,
                value=value,
                start_value=value,
                points=0,
                status="active",
                gc_position=None,
                jerseys=[],
                in_my_team=False,
                is_captain=False,
            )
            riders.append(r)
            sim_results[rid] = SimResult(
                rider_id=rid,
                expected_value=60_000,
                std_dev=25_000,
                percentile_10=20_000,
                percentile_50=60_000,
                percentile_80=90_000,
                percentile_90=110_000,
                percentile_95=150_000,
                p_positive=0.8,
            )

        stage = _flat_stage()
        for profile in RiskProfile:
            rec = optimize(
                riders=riders,
                my_team=[],
                stage=stage,
                probs={},
                sim_results=sim_results,
                bank=50_000_000,
                risk_profile=profile,
                rank=None,
                total_participants=None,
                stages_remaining=10,
            )
            squad: set = set()
            for t in rec.transfers:
                if t.action == "buy":
                    squad.add(t.rider_id)
                elif t.action == "sell":
                    squad.discard(t.rider_id)
            assert len(squad) == 8, (
                f"{profile.value}: expected 8 riders with real-budget pool, got {len(squad)}"
            )
            # Total value of squad must be ≤ 50M (fees are extra, but values alone must fit)
            total_value = sum(
                next(r.value for r in riders if r.holdet_id == rid)
                for rid in squad
            )
            assert total_value <= 50_000_000, (
                f"{profile.value}: squad value {total_value:,} exceeds 50M budget"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# Session 15-Fixes tests
# ═══════════════════════════════════════════════════════════════════════════════

def _make_flat_stage_fix():
    return Stage(
        number=1, race="giro_2026", stage_type="flat", distance_km=180.0,
        is_ttt=False, start_location="A", finish_location="B",
        sprint_points=[], kom_points=[], notes="",
    )

def _make_rider_fix(holdet_id, team_abbr="T1", value=8_000_000):
    return Rider(
        holdet_id=holdet_id, person_id=f"p_{holdet_id}", team_id=f"t_{team_abbr}",
        name=f"Rider {holdet_id}", team="Team", team_abbr=team_abbr,
        value=value, start_value=value, points=0, status="active",
        gc_position=None, jerseys=[], in_my_team=False, is_captain=False,
    )


class TestEvalCacheKeySorted:
    """_eval_team enforces sorted key — different call order hits same cache entry."""

    def test_unsorted_and_sorted_ids_hit_same_cache(self):
        import scoring.optimizer as opt_mod
        opt_mod._eval_cache.clear()

        riders = [_make_rider_fix(f"r{i}", f"T{i}") for i in range(1, 9)]
        stage = _make_flat_stage_fix()
        probs = {}

        ids_abc = tuple(["r3", "r1", "r2", "r4", "r5", "r6", "r7", "r8"])
        ids_sorted = tuple(sorted(ids_abc))
        captain = "r1"

        result_a = _eval_team(ids_abc,   captain, stage, riders, probs, n=5, seed=42)
        result_b = _eval_team(ids_sorted, captain, stage, riders, probs, n=5, seed=42)

        assert result_a is result_b  # same object from cache
        assert len(opt_mod._eval_cache) == 1  # only one cache entry created


class TestThresholdNoiseFloor:
    """NOISE_FLOOR constant and scale-aware threshold logic."""

    def test_noise_floor_constant_defined(self):
        assert NOISE_FLOOR == 20_000

    def test_threshold_uses_noise_floor_when_metric_is_zero(self):
        # With current_metric=0, threshold must equal NOISE_FLOOR (not zero)
        score = _eval_swap(RiskProfile.ALL_IN, 0, 0, 0, 0, 1, current_metric=0)
        assert score is None  # gain=0 < NOISE_FLOOR=20k → rejected

        score2 = _eval_swap(RiskProfile.ALL_IN, NOISE_FLOOR + 1, 0, 0, 0, 1, current_metric=0)
        assert score2 is not None  # gain > NOISE_FLOOR → accepted

    def test_threshold_scales_with_metric_above_noise_floor(self):
        # current_metric=5_000_000 → 1% = 50_000 > NOISE_FLOOR → threshold = 50_000
        score_below = _eval_swap(
            RiskProfile.ALL_IN, 40_000, 0, 0, 0, 1, current_metric=5_000_000
        )
        assert score_below is None  # 40k < 50k threshold → rejected

        score_above = _eval_swap(
            RiskProfile.ALL_IN, 60_000, 0, 0, 0, 1, current_metric=5_000_000
        )
        assert score_above is not None  # 60k > 50k threshold → accepted


# ── Session 16: Transfer fix tests ───────────────────────────────────────────

class TestDiffBasedTransferReporting:
    """Verify diff-based transfer reporting: Stage 1 empty team and Stage 2+ semantics."""

    def _make_pool(self):
        """16-rider pool (8 GC + 8 sprinters) with realistic sim results."""
        pool, ids, sr = _build_pool()
        return pool, ids, sr

    def test_stage1_empty_team_shows_8_buys_0_sells(self):
        """With no prior team, all profiles report exactly 8 buys and 0 sells."""
        import scoring.optimizer as opt_mod
        opt_mod._eval_cache.clear()

        pool, _, sr = self._make_pool()
        stage = _flat_stage()

        rec = optimize(
            riders=pool,
            my_team=[],
            stage=stage,
            probs={},
            sim_results=sr,
            bank=50_000_000,
            risk_profile=RiskProfile.BALANCED,
            rank=None,
            total_participants=None,
            stages_remaining=10,
            n_sim=10,
        )
        buys  = [t for t in rec.transfers if t.action == "buy"]
        sells = [t for t in rec.transfers if t.action == "sell"]
        assert len(buys) == 8,  f"Expected 8 buys, got {len(buys)}: {[t.rider_name for t in buys]}"
        assert len(sells) == 0, f"Expected 0 sells, got {len(sells)}: {[t.rider_name for t in sells]}"

    def test_stage2_sells_reference_only_owned_riders(self):
        """Sell actions must only reference riders that were in my_team."""
        import scoring.optimizer as opt_mod
        opt_mod._eval_cache.clear()

        pool, my_team_ids, sr = self._make_pool()
        stage = _flat_stage()

        rec = optimize(
            riders=pool,
            my_team=my_team_ids,  # 8 GC riders
            stage=stage,
            probs={},
            sim_results=sr,
            bank=50_000_000,
            risk_profile=RiskProfile.ALL_IN,
            rank=None,
            total_participants=None,
            stages_remaining=10,
            n_sim=10,
        )
        for t in rec.transfers:
            if t.action == "sell":
                assert t.rider_id in my_team_ids, (
                    f"Sell references {t.rider_id!r} which was not in my_team"
                )

    def test_net_squad_always_8(self):
        """After applying all transfers, the active squad is always exactly 8 riders."""
        import scoring.optimizer as opt_mod
        opt_mod._eval_cache.clear()

        pool, my_team_ids, sr = self._make_pool()
        stage = _flat_stage()

        for profile in RiskProfile:
            opt_mod._eval_cache.clear()
            rec = optimize(
                riders=pool,
                my_team=my_team_ids,
                stage=stage,
                probs={},
                sim_results=sr,
                bank=50_000_000,
                risk_profile=profile,
                rank=None,
                total_participants=None,
                stages_remaining=10,
                n_sim=10,
            )
            final = set(my_team_ids)
            for t in rec.transfers:
                if t.action == "sell":
                    final.discard(t.rider_id)
                elif t.action == "buy":
                    final.add(t.rider_id)
            assert len(final) == 8, (
                f"Profile {profile.value}: expected 8 riders after transfers, "
                f"got {len(final)}: {sorted(final)}"
            )


# ── TestOptimizeAcceptsIntent (Session 18) ────────────────────────────────────

class TestOptimizeAcceptsIntent:
    """Smoke tests: optimize() and optimize_all_profiles() accept intent param."""

    def _make_args(self):
        pool, ids, sr = _build_pool()
        stage = _flat_stage()
        return pool, ids, sr, stage

    def test_optimize_accepts_none_intent_without_error(self):
        import scoring.optimizer as opt_mod
        opt_mod._eval_cache.clear()
        pool, ids, sr, stage = self._make_args()
        rec = optimize(
            riders=pool,
            my_team=ids,
            stage=stage,
            probs={},
            sim_results=sr,
            bank=50_000_000,
            risk_profile=RiskProfile.BALANCED,
            rank=None,
            total_participants=None,
            stages_remaining=10,
            n_sim=10,
            intent=None,
        )
        assert rec.captain in {r.holdet_id for r in pool}

    def test_optimize_accepts_stage_intent_without_error(self):
        import scoring.optimizer as opt_mod
        opt_mod._eval_cache.clear()
        pool, ids, sr, stage = self._make_args()
        intent = compute_stage_intent(stage, {}, next_stage=None, riders=pool)
        rec = optimize(
            riders=pool,
            my_team=ids,
            stage=stage,
            probs={},
            sim_results=sr,
            bank=50_000_000,
            risk_profile=RiskProfile.BALANCED,
            rank=None,
            total_participants=None,
            stages_remaining=10,
            n_sim=10,
            intent=intent,
        )
        assert rec.captain in {r.holdet_id for r in pool}

    def test_optimize_all_profiles_accepts_intent(self):
        import scoring.optimizer as opt_mod
        opt_mod._eval_cache.clear()
        pool, ids, sr, stage = self._make_args()
        intent = compute_stage_intent(stage, {}, next_stage=None, riders=pool)
        results = optimize_all_profiles(
            riders=pool,
            my_team=ids,
            stage=stage,
            probs={},
            sim_results=sr,
            bank=50_000_000,
            rank=None,
            total_participants=None,
            stages_remaining=10,
            n_sim=10,
            intent=intent,
        )
        assert len(results) == 4
        for profile in RiskProfile:
            assert profile in results


# ── TestICDLRegressionGuard (Session 18) ─────────────────────────────────────

class TestICDLRegressionGuard:
    """
    Regression guard: λ=0 and win_priority=0 must reproduce pre-Session-18 EV.

    When intent.win_priority=0 and lambda=0:
      adjusted_ev = base_ev * (1 + 0.3 * 0) = base_ev
      transfer_penalty = fee * (1 + 0) = fee
      net_ev = base_ev - fee + 0 * next_ev = base_ev - fee

    This must equal the pre-Session-18 single-stage calculation.
    Ensures ICDL is additive, not destructive.
    """

    def test_lambda_zero_win_priority_zero_matches_pre_session18_ev(self):
        base_ev = 150_000.0
        fee = 30_000
        next_ev = 999_999.0  # irrelevant when λ=0

        intent = StageIntent(
            win_priority=0.0,
            survival_priority=0.5,
            transfer_pressure=0.0,
            team_bonus_value=0.5,
            breakaway_likelihood=0.3,
        )
        lambda_val = 0.0

        adjusted_ev = apply_intent_to_ev(base_ev, intent)
        assert abs(adjusted_ev - base_ev) < 1.0, "win_priority=0 must leave EV unchanged"

        penalty = compute_transfer_penalty(fee, intent)
        assert abs(penalty - fee) < 1.0, "transfer_pressure=0 must leave fee unchanged"

        net_ev = adjusted_ev - penalty + lambda_val * next_ev
        pre_session18_net = base_ev - fee
        assert abs(net_ev - pre_session18_net) < 1.0, (
            f"ICDL regression: expected {pre_session18_net}, got {net_ev}"
        )

    def _squad_from_rec(self, rec, my_team):
        """Derive final squad from transfers applied to my_team."""
        squad = set(my_team)
        for t in rec.transfers:
            if t.action == "sell":
                squad.discard(t.rider_id)
            elif t.action == "buy":
                squad.add(t.rider_id)
        return squad

    def test_anchor_captain_unaffected_by_intent(self):
        """ANCHOR captain selection is identical with and without intent."""
        import scoring.optimizer as opt_mod
        opt_mod._eval_cache.clear()
        pool, ids, sr = _build_pool()
        stage = _flat_stage()

        from scoring.optimizer import _pick_captain
        rider_map = {r.holdet_id: r for r in pool}
        intent = compute_stage_intent(stage, {}, next_stage=None, riders=pool)

        captain_without = _pick_captain(ids, sr, RiskProfile.ANCHOR, rider_map, intent=None)
        captain_with = _pick_captain(ids, sr, RiskProfile.ANCHOR, rider_map, intent=intent)
        assert captain_without == captain_with


# ── TestIntentDoesNotAffectOptimizerPreSession20 (18H) ───────────────────────

class TestIntentDoesNotAffectOptimizerPreSession20:
    """
    Regression guard: optimize() must produce identical squad, transfers, and
    expected_value with intent=None and intent=some_intent until Session 20
    wires apply_intent_to_ev() into _eval_team().
    Captain is allowed to differ for BALANCED profile (intent nudges it).
    """

    def _squad_from_rec(self, rec, my_team):
        squad = set(my_team)
        for t in rec.transfers:
            if t.action == "sell":
                squad.discard(t.rider_id)
            elif t.action == "buy":
                squad.add(t.rider_id)
        return squad

    def test_optimizer_output_identical_with_and_without_intent(self):
        import scoring.optimizer as opt_mod
        pool, ids, sr = _build_pool()
        stage = _flat_stage()
        intent = compute_stage_intent(stage, {}, next_stage=None, riders=pool)

        opt_mod._eval_cache.clear()
        rec1 = optimize(
            riders=pool,
            my_team=ids,
            stage=stage,
            probs={},
            sim_results=sr,
            bank=50_000_000,
            risk_profile=RiskProfile.ANCHOR,
            rank=None,
            total_participants=None,
            stages_remaining=10,
            n_sim=10,
            intent=None,
        )
        opt_mod._eval_cache.clear()
        rec2 = optimize(
            riders=pool,
            my_team=ids,
            stage=stage,
            probs={},
            sim_results=sr,
            bank=50_000_000,
            risk_profile=RiskProfile.ANCHOR,
            rank=None,
            total_participants=None,
            stages_remaining=10,
            n_sim=10,
            intent=intent,
        )

        assert self._squad_from_rec(rec1, ids) == self._squad_from_rec(rec2, ids)
        assert rec1.transfers == rec2.transfers
        assert abs(rec1.expected_value - rec2.expected_value) < 1.0
