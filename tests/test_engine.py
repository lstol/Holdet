"""
tests/test_engine.py — Comprehensive unit tests for the Holdet scoring engine.

Covers all 11 scoring cases from SESSION_ROADMAP.md Session 1.
Every test cites the relevant RULES.md section.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from scoring.engine import (
    Rider, Stage, StageResult, ValueDelta,
    score_rider, late_arrival_penalty,
    STAGE_POSITION_TABLE, GC_STANDING_TABLE, ETAPEBONUS_TABLE,
    TTT_PLACEMENT_TABLE, TEAM_BONUS_TABLE, JERSEY_VALUES,
)


# ── Test fixtures ──────────────────────────────────────────────────────────────

def make_rider(
    holdet_id="r1",
    team="Team Alpha",
    team_abbr="TMA",
    status="active",
    name="Test Rider",
) -> Rider:
    return Rider(
        holdet_id=holdet_id,
        person_id="p1",
        team_id="t1",
        name=name,
        team=team,
        team_abbr=team_abbr,
        value=5_000_000,
        start_value=5_000_000,
        points=0,
        status=status,
        gc_position=None,
        jerseys=[],
        in_my_team=True,
        is_captain=False,
    )


def make_stage(is_ttt=False, stage_type="flat", number=1) -> Stage:
    return Stage(
        number=number,
        race="giro_2026",
        stage_type="ttt" if is_ttt else stage_type,
        distance_km=180.0,
        is_ttt=is_ttt,
        start_location="City A",
        finish_location="City B",
    )


def make_result(
    stage_number=1,
    finish_order=None,
    times_behind_winner=None,
    sprint_point_winners=None,
    kom_point_winners=None,
    jersey_winners=None,
    most_aggressive=None,
    dnf_riders=None,
    dns_riders=None,
    disqualified=None,
    ttt_team_order=None,
    gc_standings=None,
) -> StageResult:
    return StageResult(
        stage_number=stage_number,
        finish_order=finish_order or [],
        times_behind_winner=times_behind_winner or {},
        sprint_point_winners=sprint_point_winners or {},
        kom_point_winners=kom_point_winners or {},
        jersey_winners=jersey_winners or {},
        most_aggressive=most_aggressive,
        dnf_riders=dnf_riders or [],
        dns_riders=dns_riders or [],
        disqualified=disqualified or [],
        ttt_team_order=ttt_team_order,
        gc_standings=gc_standings or [],
    )


MY_TEAM = ["r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8"]
NO_CAPTAIN = "r99"  # not on team — disables captain bonus


# ── Test 1: Late arrival truncation (RULES.md 2.5) ───────────────────────────

class TestLateArrivalPenalty:
    """
    RULES.md 2.5 — Truncated (not rounded) minutes × −3,000, cap −90,000.
    4 min 54 sec = 294 seconds = 4 full minutes = −12,000, NOT −15,000.
    """

    def test_truncation_4min54sec(self):
        # 294 seconds = 4 full minutes (floor), penalty = 4 × −3,000
        assert late_arrival_penalty(294) == -12_000

    def test_exactly_5_minutes(self):
        # 300 seconds = exactly 5 minutes
        assert late_arrival_penalty(300) == -15_000

    def test_cap_at_30_minutes(self):
        # 1800 seconds = 30 minutes = cap
        assert late_arrival_penalty(1800) == -90_000

    def test_over_cap_still_capped(self):
        # 2000 seconds > 30 minutes, still returns −90,000
        assert late_arrival_penalty(2000) == -90_000

    def test_zero_seconds(self):
        # Stage winner has 0 seconds behind — no penalty
        assert late_arrival_penalty(0) == 0

    def test_negative_seconds_ignored(self):
        # Should never happen in practice, but handle gracefully
        assert late_arrival_penalty(-60) == 0

    def test_truncation_59_seconds(self):
        # 59 seconds = 0 full minutes = 0 penalty (under 1 minute)
        assert late_arrival_penalty(59) == 0

    def test_truncation_1_minute_1_second(self):
        # 61 seconds = 1 full minute = −3,000
        assert late_arrival_penalty(61) == -3_000

    def test_score_rider_late_arrival_via_result(self):
        """Late arrival penalty flows correctly through score_rider."""
        rider = make_rider("r1")
        stage = make_stage()
        result = make_result(
            finish_order=["other", "r1"],
            times_behind_winner={"r1": 294},  # 4 min 54 sec
            gc_standings=[],
        )
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10)
        # RULES.md 2.5 — truncate: 294 // 60 = 4 minutes → −12,000
        assert delta.late_arrival_penalty == -12_000

    def test_late_arrival_not_applied_on_ttt(self):
        """RULES.md 4 — late arrival penalty does NOT apply on TTT stages."""
        rider = make_rider("r1", team="Team Visma | Lease a Bike")
        stage = make_stage(is_ttt=True)
        result = make_result(
            times_behind_winner={"r1": 600},  # 10 minutes
            ttt_team_order=["Team Visma | Lease a Bike", "Team Beta"],
            gc_standings=[],
        )
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10)
        assert delta.late_arrival_penalty == 0


# ── Test 2: Jersey rule (RULES.md 2.3) ───────────────────────────────────────

class TestJerseyRule:
    """
    RULES.md 2.3 CRITICAL: bonus goes to rider who HOLDS jersey at END of stage.
    Rider who wears yellow all day but loses it at finish gets ZERO.
    """

    def test_jersey_lost_at_finish_gives_zero(self):
        rider = make_rider("r1")
        stage = make_stage()
        # Rider wore yellow entering but "other_rider" holds it at finish
        result = make_result(
            finish_order=["r1"],
            jersey_winners={"yellow": "other_rider"},
            gc_standings=[],
        )
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10)
        assert delta.jersey_bonus == 0

    def test_yellow_holder_at_finish_gets_bonus(self):
        rider = make_rider("r1")
        stage = make_stage()
        result = make_result(
            finish_order=["r1"],
            jersey_winners={"yellow": "r1"},
            gc_standings=[],
        )
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10)
        # RULES.md 2.3 — yellow = +25,000
        assert delta.jersey_bonus == 25_000

    def test_multiple_jerseys_accumulated(self):
        """Rider holds yellow + polkadot = 25,000 + 25,000 = 50,000."""
        rider = make_rider("r1")
        stage = make_stage()
        result = make_result(
            finish_order=["r1"],
            jersey_winners={"yellow": "r1", "polkadot": "r1", "green": "r2"},
            gc_standings=[],
        )
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10)
        assert delta.jersey_bonus == 50_000  # 25k + 25k

    def test_white_jersey_value(self):
        """RULES.md 2.3 — white jersey = +15,000 (less than other jerseys)."""
        rider = make_rider("r1")
        stage = make_stage()
        result = make_result(
            finish_order=["r1"],
            jersey_winners={"white": "r1"},
            gc_standings=[],
        )
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10)
        assert delta.jersey_bonus == 15_000

    def test_most_aggressive_bonus(self):
        """RULES.md 2.3 — Most Aggressive = +50,000, separate from jersey_winners."""
        rider = make_rider("r1")
        stage = make_stage()
        result = make_result(
            finish_order=["r1"],
            most_aggressive="r1",
            gc_standings=[],
        )
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10)
        assert delta.jersey_bonus == 50_000

    def test_jerseys_apply_on_ttt(self):
        """RULES.md 4 — jersey bonuses still apply normally on TTT stages."""
        rider = make_rider("r1", team="Team Alpha")
        stage = make_stage(is_ttt=True)
        result = make_result(
            jersey_winners={"yellow": "r1"},
            ttt_team_order=["Team Alpha"],
            gc_standings=[],
        )
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10)
        assert delta.jersey_bonus == 25_000


# ── Test 3: Captain bonus — positive and negative days (RULES.md 3.2) ────────

class TestCaptainBonus:
    """
    RULES.md 3.2 — Positive value growth → same amount deposited to bank.
    Negative days: NOT amplified. captain_bank_deposit is never negative.
    """

    def test_captain_positive_day_deposits_to_bank(self):
        rider = make_rider("r1")
        stage = make_stage()
        # r1 finishes 2nd: +150,000 stage position value
        result = make_result(
            finish_order=["other", "r1"],
            gc_standings=[],
        )
        delta = score_rider(rider, stage, result, MY_TEAM, captain="r1", stages_remaining=10)
        # RULES.md 3.2 — captain gets +150,000 to rider value AND +150,000 to bank
        assert delta.stage_position_value == 150_000
        assert delta.captain_bank_deposit == 150_000
        assert delta.total_rider_value_delta >= 150_000

    def test_captain_negative_day_no_bank_penalty(self):
        """RULES.md 3.2 — bad stage: only normal rider loss, bank untouched."""
        rider = make_rider("r1")
        stage = make_stage()
        # r1 finishes outside top 15 with late arrival
        result = make_result(
            finish_order=["o1", "o2", "o3", "o4", "o5",
                          "o6", "o7", "o8", "o9", "o10",
                          "o11", "o12", "o13", "o14", "o15", "r1"],
            times_behind_winner={"r1": 600},  # 10 min late = −30,000
            gc_standings=[],
        )
        delta = score_rider(rider, stage, result, MY_TEAM, captain="r1", stages_remaining=10)
        # Captain had a bad day
        assert delta.total_rider_value_delta < 0
        # RULES.md 3.2 — no bank penalty on negative day
        assert delta.captain_bank_deposit == 0
        assert delta.total_bank_delta == delta.etapebonus_bank_deposit  # only etapebonus

    def test_captain_zero_value_day_no_bank_deposit(self):
        """Captain with exactly 0 value growth → 0 bank deposit."""
        rider = make_rider("r1")
        stage = make_stage()
        # r1 finishes 16th (no position value), no penalty, no jersey
        result = make_result(
            finish_order=["o1", "o2", "o3", "o4", "o5",
                          "o6", "o7", "o8", "o9", "o10",
                          "o11", "o12", "o13", "o14", "o15", "r1"],
            gc_standings=[],
        )
        delta = score_rider(rider, stage, result, MY_TEAM, captain="r1", stages_remaining=10)
        assert delta.total_rider_value_delta == 0
        assert delta.captain_bank_deposit == 0

    def test_non_captain_gets_no_bank_deposit(self):
        """Only the designated captain gets a bank deposit."""
        rider = make_rider("r1")
        stage = make_stage()
        result = make_result(
            finish_order=["r1"],  # wins stage
            gc_standings=[],
        )
        delta = score_rider(rider, stage, result, MY_TEAM, captain="r2", stages_remaining=10)
        assert delta.captain_bank_deposit == 0

    def test_captain_win_full_example(self):
        """RULES.md 3.2 example: captain finishes 2nd → +150,000 rider + +150,000 bank."""
        rider = make_rider("r1")
        stage = make_stage()
        result = make_result(
            finish_order=["other", "r1"],
            gc_standings=[],
        )
        delta = score_rider(rider, stage, result, MY_TEAM, captain="r1", stages_remaining=10)
        assert delta.stage_position_value == 150_000
        assert delta.captain_bank_deposit == 150_000


# ── Test 4: DNF — penalty, sprint/KOM, no team bonus (RULES.md 2.6) ──────────

class TestDNF:
    """
    RULES.md 2.6 — DNF: −50,000 once. Still earns sprint/KOM from before
    abandonment. Does NOT receive team bonus.
    """

    def test_dnf_penalty(self):
        rider = make_rider("r1")
        stage = make_stage()
        result = make_result(
            dnf_riders=["r1"],
            gc_standings=[],
        )
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10)
        # RULES.md 2.6 — one-time −50,000
        assert delta.dnf_penalty == -50_000

    def test_dnf_still_earns_sprint_kom(self):
        """RULES.md 2.6 — DNF riders keep sprint/KOM points earned before abandonment."""
        rider = make_rider("r1")
        stage = make_stage()
        result = make_result(
            dnf_riders=["r1"],
            sprint_point_winners={"r1": [2]},  # 2 points at one sprint
            gc_standings=[],
        )
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10)
        assert delta.sprint_kom_value == 6_000  # 2 × 3,000
        assert delta.dnf_penalty == -50_000

    def test_dnf_no_team_bonus(self):
        """RULES.md 3.1 — DNF riders do NOT receive team bonus."""
        dnf_rider = make_rider("r1", team="Team Alpha")
        winner = make_rider("other_winner", team="Team Alpha")
        stage = make_stage()
        result = make_result(
            finish_order=["other_winner"],
            dnf_riders=["r1"],
            gc_standings=[],
        )
        all_riders = {"r1": dnf_rider, "other_winner": winner}
        delta = score_rider(dnf_rider, stage, result, MY_TEAM, NO_CAPTAIN, 10,
                            all_riders=all_riders)
        assert delta.team_bonus == 0

    def test_dnf_no_stage_position(self):
        """DNF rider should not receive stage finish position value."""
        rider = make_rider("r1")
        stage = make_stage()
        result = make_result(
            finish_order=["r1"],  # listed but also in DNF
            dnf_riders=["r1"],
            gc_standings=[],
        )
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10)
        assert delta.stage_position_value == 0

    def test_dnf_no_late_arrival_penalty(self):
        """DNF riders did not finish — late arrival doesn't apply."""
        rider = make_rider("r1")
        stage = make_stage()
        result = make_result(
            dnf_riders=["r1"],
            times_behind_winner={"r1": 3600},  # would be huge penalty if applied
            gc_standings=[],
        )
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10)
        assert delta.late_arrival_penalty == 0

    def test_disqualified_same_as_dnf(self):
        """RULES.md 2.6 — Disqualified: same penalty as DNF."""
        rider = make_rider("r1")
        stage = make_stage()
        result = make_result(
            disqualified=["r1"],
            sprint_point_winners={"r1": [5]},
            gc_standings=[],
        )
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10)
        assert delta.dnf_penalty == -50_000
        assert delta.sprint_kom_value == 15_000  # 5 × 3,000


# ── Test 5: DNS cascade (RULES.md 2.6) ───────────────────────────────────────

class TestDNS:
    """
    RULES.md 2.6 — DNS: −100,000 per remaining stage. Cascades every stage.
    ACTION RULE: sell immediately.
    """

    def test_dns_penalty_cascade(self):
        rider = make_rider("r1", status="dns")
        stage = make_stage()
        result = make_result(
            dns_riders=["r1"],
            gc_standings=[],
        )
        # 13 stages remaining → −1,300,000
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN,
                            stages_remaining=13)
        assert delta.dns_penalty == -1_300_000

    def test_dns_one_stage_remaining(self):
        rider = make_rider("r1", status="dns")
        stage = make_stage()
        result = make_result(dns_riders=["r1"], gc_standings=[])
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN,
                            stages_remaining=1)
        assert delta.dns_penalty == -100_000

    def test_dns_no_position_or_team_bonus(self):
        """DNS rider gets nothing except the cascade penalty."""
        rider = make_rider("r1", team="Team Alpha", status="dns")
        winner = make_rider("winner", team="Team Alpha")
        stage = make_stage()
        result = make_result(
            finish_order=["winner"],
            dns_riders=["r1"],
            gc_standings=[],
        )
        all_riders = {"r1": rider, "winner": winner}
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN,
                            stages_remaining=5, all_riders=all_riders)
        assert delta.stage_position_value == 0
        assert delta.team_bonus == 0
        assert delta.gc_standing_value == 0
        assert delta.dns_penalty == -500_000

    def test_dns_example_from_rules(self):
        """RULES.md 2.6 example: DNF Stage 8 of 21 → −50k + −100k × 13 = −1,350,000."""
        # Stage 8 DNF (not DNS): dnf_penalty + then dns on subsequent stages
        # In this test we simulate the DNS cascade for remaining stages after DNF.
        rider = make_rider("r1", status="dns")
        stage = make_stage(number=9)  # stage 9 onwards after Stage 8 DNF
        result = make_result(dns_riders=["r1"], gc_standings=[])
        # 13 remaining stages × −100,000
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN,
                            stages_remaining=13)
        assert delta.dns_penalty == -1_300_000


# ── Test 6: Etapebonus nonlinear (RULES.md 3.3) ───────────────────────────────

class TestEtapebonus:
    """
    RULES.md 3.3 — Stage depth bonus: nonlinear bank deposit by top-15 count.
    Paid to bank, not rider values.
    """

    def _run_etapebonus(self, n_top15: int) -> int:
        """Helper: set n_top15 of my 8 riders in top 15, return etapebonus."""
        rider = make_rider("r1")
        stage = make_stage()
        # Put exactly n_top15 of MY_TEAM in top 15
        top15 = MY_TEAM[:n_top15]
        others = [f"other_{i}" for i in range(15 - n_top15)]
        finish_order = top15 + others
        result = make_result(finish_order=finish_order, gc_standings=[])
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10)
        return delta.etapebonus_bank_deposit

    def test_etapebonus_table_full(self):
        """Verify the complete nonlinear etapebonus table."""
        expected = {
            0: 0, 1: 4_000, 2: 8_000, 3: 15_000, 4: 35_000,
            5: 65_000, 6: 120_000, 7: 220_000, 8: 400_000,
        }
        for n, value in expected.items():
            assert self._run_etapebonus(n) == value, \
                f"Etapebonus for {n} riders in top-15 should be {value}"

    def test_etapebonus_4_riders(self):
        """RULES.md 3.3 — 4 of 8 riders in top 15 → 35,000 to bank."""
        assert self._run_etapebonus(4) == 35_000

    def test_etapebonus_goes_to_bank_not_rider(self):
        """Etapebonus is in total_bank_delta, not total_rider_value_delta."""
        rider = make_rider("r1")
        stage = make_stage()
        result = make_result(
            finish_order=MY_TEAM[:4],  # 4 in top 15
            gc_standings=[],
        )
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10)
        # Not in rider value
        assert delta.etapebonus_bank_deposit == 35_000
        # In bank delta (no captain this run, so total_bank_delta == etapebonus)
        assert delta.total_bank_delta == 35_000

    def test_etapebonus_zero_on_ttt(self):
        """RULES.md 4 — Etapebonus not applied on TTT stages."""
        rider = make_rider("r1", team="Team Alpha")
        stage = make_stage(is_ttt=True)
        result = make_result(
            finish_order=MY_TEAM,  # all 8 in "top 15" but TTT ignores this
            ttt_team_order=["Team Alpha"],
            gc_standings=[],
        )
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10)
        assert delta.etapebonus_bank_deposit == 0


# ── Test 7: TTT mode (RULES.md 4) ─────────────────────────────────────────────

class TestTTT:
    """
    RULES.md 4 — On TTT stages: stage finish position, team bonus, late arrival,
    and etapebonus are ALL replaced/removed. GC, jerseys, sprint/KOM still apply.
    """

    def test_ttt_first_place_value(self):
        """RULES.md 4 — 1st place TTT team → +200,000 per rider."""
        rider = make_rider("r1", team="Team Visma | Lease a Bike")
        stage = make_stage(is_ttt=True)
        result = make_result(
            ttt_team_order=["Team Visma | Lease a Bike", "Team Beta", "Team Gamma"],
            gc_standings=[],
        )
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10)
        assert delta.ttt_value == 200_000

    def test_ttt_replacements_all_zero(self):
        """RULES.md 4 — stage position, team bonus, late arrival, etapebonus = 0."""
        rider = make_rider("r1", team="Team Visma | Lease a Bike")
        stage = make_stage(is_ttt=True)
        result = make_result(
            finish_order=["r1"],  # would give stage position value on non-TTT
            times_behind_winner={"r1": 600},  # would give late arrival on non-TTT
            ttt_team_order=["Team Visma | Lease a Bike"],
            gc_standings=[],
        )
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10)
        assert delta.stage_position_value == 0
        assert delta.team_bonus == 0
        assert delta.late_arrival_penalty == 0
        assert delta.etapebonus_bank_deposit == 0
        assert delta.ttt_value == 200_000  # 1st place

    def test_ttt_gc_standing_still_applies(self):
        """RULES.md 4 — GC standing applies normally on TTT stages."""
        rider = make_rider("r1", team="Team Alpha")
        stage = make_stage(is_ttt=True)
        result = make_result(
            ttt_team_order=["Team Alpha"],
            gc_standings=["r1"],  # r1 is GC leader after TTT
        )
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10)
        assert delta.gc_standing_value == 100_000  # GC 1st = +100,000

    def test_ttt_placements(self):
        """Verify all 5 TTT placement values from RULES.md 4."""
        expected = {1: 200_000, 2: 150_000, 3: 100_000, 4: 50_000, 5: 25_000}
        teams = ["T1", "T2", "T3", "T4", "T5", "T6"]
        for place, value in expected.items():
            rider = make_rider("r1", team=teams[place - 1])
            stage = make_stage(is_ttt=True)
            result = make_result(ttt_team_order=teams, gc_standings=[])
            delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10)
            assert delta.ttt_value == value, \
                f"TTT place {place} should give {value}, got {delta.ttt_value}"

    def test_ttt_sixth_place_zero(self):
        """RULES.md 4 — 6th place and below = 0 on TTT."""
        rider = make_rider("r1", team="T6")
        stage = make_stage(is_ttt=True)
        result = make_result(
            ttt_team_order=["T1", "T2", "T3", "T4", "T5", "T6"],
            gc_standings=[],
        )
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10)
        assert delta.ttt_value == 0

    def test_ttt_captain_bank_deposit_from_ttt_value(self):
        """Captain gets bank deposit from TTT value on TTT stages."""
        rider = make_rider("r1", team="Team Alpha")
        stage = make_stage(is_ttt=True)
        result = make_result(
            ttt_team_order=["Team Alpha"],
            gc_standings=[],
        )
        delta = score_rider(rider, stage, result, MY_TEAM, captain="r1", stages_remaining=10)
        # TTT 1st = +200,000 → captain bank deposit = +200,000
        assert delta.ttt_value == 200_000
        assert delta.captain_bank_deposit == 200_000


# ── Test 8: Team bonus (RULES.md 3.1) ────────────────────────────────────────

class TestTeamBonus:
    """
    RULES.md 3.1 — Active riders from same real-world team as top-3 finisher
    receive bonus. DNF riders do not.
    """

    def test_active_rider_gets_team_bonus_on_win(self):
        """Active team rider gets +60,000 when teammate wins."""
        active = make_rider("r1", team="Team Alpha")
        winner = make_rider("winner", team="Team Alpha")
        stage = make_stage()
        result = make_result(
            finish_order=["winner", "r1"],
            gc_standings=[],
        )
        all_riders = {"r1": active, "winner": winner}
        delta = score_rider(active, stage, result, MY_TEAM, NO_CAPTAIN, 10,
                            all_riders=all_riders)
        # r1 finishes 2nd (+150,000) AND gets team bonus for winner (+60,000)
        assert delta.team_bonus == 60_000
        assert delta.stage_position_value == 150_000

    def test_dnf_rider_no_team_bonus(self):
        """RULES.md 2.6 — DNF rider does NOT receive team bonus."""
        dnf_rider = make_rider("r1", team="Team Alpha")
        winner = make_rider("winner", team="Team Alpha")
        stage = make_stage()
        result = make_result(
            finish_order=["winner"],
            dnf_riders=["r1"],
            gc_standings=[],
        )
        all_riders = {"r1": dnf_rider, "winner": winner}
        delta = score_rider(dnf_rider, stage, result, MY_TEAM, NO_CAPTAIN, 10,
                            all_riders=all_riders)
        assert delta.team_bonus == 0

    def test_no_team_bonus_different_team(self):
        """No bonus when top-3 finisher is from a different real-world team."""
        rider = make_rider("r1", team="Team Alpha")
        winner = make_rider("winner", team="Team Beta")  # different team
        stage = make_stage()
        result = make_result(
            finish_order=["winner", "r1"],
            gc_standings=[],
        )
        all_riders = {"r1": rider, "winner": winner}
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10,
                            all_riders=all_riders)
        assert delta.team_bonus == 0

    def test_team_bonus_values(self):
        """RULES.md 3.1 — 1st=60k, 2nd=30k, 3rd=20k."""
        for pos, expected_bonus in [(1, 60_000), (2, 30_000), (3, 20_000)]:
            rider = make_rider("r1", team="Team Alpha")
            others = [make_rider(f"o{i}", team="Team Alpha") for i in range(3)]
            stage = make_stage()
            # Place the teammate at `pos`, fill gaps with other teams
            finish = [f"other_{i}" for i in range(pos - 1)] + [f"o{pos-1}"] + ["r1"]
            result = make_result(finish_order=finish, gc_standings=[])
            all_riders = {f"o{i}": make_rider(f"o{i}", team="Team Alpha")
                          for i in range(3)}
            all_riders["r1"] = rider
            for i in range(pos - 1):
                all_riders[f"other_{i}"] = make_rider(f"other_{i}", team="Other Team")
            delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10,
                                all_riders=all_riders)
            assert delta.team_bonus == expected_bonus, \
                f"Pos {pos} team bonus should be {expected_bonus}"

    def test_team_bonus_zero_on_ttt(self):
        """RULES.md 4 — team bonus (Holdbonus) replaced by TTT scoring on TTT stages."""
        rider = make_rider("r1", team="Team Alpha")
        winner = make_rider("winner", team="Team Alpha")
        stage = make_stage(is_ttt=True)
        result = make_result(
            finish_order=["winner", "r1"],
            ttt_team_order=["Team Alpha"],
            gc_standings=[],
        )
        all_riders = {"r1": rider, "winner": winner}
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10,
                            all_riders=all_riders)
        assert delta.team_bonus == 0

    def test_team_bonus_without_all_riders_is_zero(self):
        """When all_riders is None, team bonus cannot be computed → returns 0."""
        rider = make_rider("r1", team="Team Alpha")
        stage = make_stage()
        result = make_result(finish_order=["winner", "r1"], gc_standings=[])
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10,
                            all_riders=None)
        assert delta.team_bonus == 0


# ── Test 9: GC standing (RULES.md 2.2) ───────────────────────────────────────

class TestGCStanding:
    """RULES.md 2.2 — GC standing applies every stage, including TTT."""

    def test_gc_values(self):
        """Verify all 10 GC standing values."""
        expected = {
            1: 100_000, 2: 90_000, 3: 80_000, 4: 70_000, 5: 60_000,
            6: 50_000,  7: 40_000, 8: 30_000, 9: 20_000, 10: 10_000,
        }
        for pos, value in expected.items():
            gc_list = [f"gc_{i}" for i in range(pos - 1)] + ["r1"]
            rider = make_rider("r1")
            stage = make_stage()
            result = make_result(gc_standings=gc_list)
            delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10)
            assert delta.gc_standing_value == value, \
                f"GC pos {pos} should give {value}"

    def test_gc_11th_gives_zero(self):
        """RULES.md 2.2 — 11th+ GC position = 0."""
        gc_list = [f"gc_{i}" for i in range(10)] + ["r1"]
        rider = make_rider("r1")
        stage = make_stage()
        result = make_result(gc_standings=gc_list)
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10)
        assert delta.gc_standing_value == 0

    def test_gc_not_in_standings_gives_zero(self):
        """Rider not in gc_standings list → 0."""
        rider = make_rider("r1")
        stage = make_stage()
        result = make_result(gc_standings=["other_rider"])
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10)
        assert delta.gc_standing_value == 0


# ── Test 10: Stage finish position (RULES.md 2.1) ────────────────────────────

class TestStagePosition:
    """RULES.md 2.1 — Stage finish position values for non-TTT stages."""

    def test_stage_position_values(self):
        """Verify key position values from the table."""
        cases = [(1, 200_000), (2, 150_000), (3, 130_000), (10, 80_000),
                 (15, 15_000)]
        for pos, expected in cases:
            finish = [f"o{i}" for i in range(pos - 1)] + ["r1"]
            rider = make_rider("r1")
            stage = make_stage()
            result = make_result(finish_order=finish, gc_standings=[])
            delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10)
            assert delta.stage_position_value == expected, \
                f"Position {pos} should give {expected}"

    def test_16th_gives_zero(self):
        """RULES.md 2.1 — 16th+ position = 0."""
        finish = [f"o{i}" for i in range(15)] + ["r1"]
        rider = make_rider("r1")
        stage = make_stage()
        result = make_result(finish_order=finish, gc_standings=[])
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10)
        assert delta.stage_position_value == 0

    def test_stage_position_zero_on_ttt(self):
        """RULES.md 4 — stage finish position not used on TTT stages."""
        rider = make_rider("r1", team="Team Alpha")
        stage = make_stage(is_ttt=True)
        result = make_result(
            finish_order=["r1"],  # 1st in finish_order — but TTT, so ignored
            ttt_team_order=["Team Alpha"],
            gc_standings=[],
        )
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10)
        assert delta.stage_position_value == 0


# ── Test 11: Sprint & KOM points (RULES.md 2.4) ──────────────────────────────

class TestSprintKOM:
    """RULES.md 2.4 — +3,000 per point (sprint or KOM), always ≥ 0."""

    def test_sprint_points(self):
        rider = make_rider("r1")
        stage = make_stage()
        result = make_result(
            sprint_point_winners={"r1": [5, 3]},  # 8 sprint points total
            gc_standings=[],
        )
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10)
        assert delta.sprint_kom_value == 24_000  # 8 × 3,000

    def test_kom_points(self):
        rider = make_rider("r1")
        stage = make_stage(stage_type="mountain")
        result = make_result(
            kom_point_winners={"r1": [10, 6, 4]},  # 20 KOM points
            gc_standings=[],
        )
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10)
        assert delta.sprint_kom_value == 60_000  # 20 × 3,000

    def test_sprint_and_kom_combined(self):
        """Sprint + KOM points both count toward sprint_kom_value."""
        rider = make_rider("r1")
        stage = make_stage()
        result = make_result(
            sprint_point_winners={"r1": [3]},    # 3 sprint points
            kom_point_winners={"r1": [4]},       # 4 KOM points
            gc_standings=[],
        )
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10)
        assert delta.sprint_kom_value == 21_000  # 7 × 3,000

    def test_no_points_zero(self):
        rider = make_rider("r1")
        stage = make_stage()
        result = make_result(gc_standings=[])
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10)
        assert delta.sprint_kom_value == 0


# ── Test 12: ValueDelta totals consistency ────────────────────────────────────

class TestValueDeltaTotals:
    """Verify that total fields are correct sums of their components."""

    def test_total_rider_value_delta_is_sum_of_components(self):
        """total_rider_value_delta == sum of all rider value components."""
        rider = make_rider("r1", team="Team Alpha")
        winner = make_rider("winner", team="Team Alpha")
        stage = make_stage()
        result = make_result(
            finish_order=["winner", "r1"],
            times_behind_winner={"r1": 120},  # 2 min = −6,000
            sprint_point_winners={"r1": [3]},
            jersey_winners={"white": "r1"},
            gc_standings=["other_gc", "r1"],  # r1 is GC 2nd
        )
        all_riders = {"r1": rider, "winner": winner}
        delta = score_rider(rider, stage, result, MY_TEAM, NO_CAPTAIN, 10,
                            all_riders=all_riders)

        expected_total = (
            delta.stage_position_value   # 2nd = +150,000
            + delta.gc_standing_value    # GC 2nd = +90,000
            + delta.jersey_bonus         # white = +15,000
            + delta.sprint_kom_value     # 3 × 3,000 = +9,000
            + delta.late_arrival_penalty # 2 min = −6,000
            + delta.dnf_penalty          # 0
            + delta.dns_penalty          # 0
            + delta.team_bonus           # winner from same team = +60,000
            + delta.ttt_value            # 0
        )
        assert delta.total_rider_value_delta == expected_total

    def test_total_bank_delta_is_sum_of_bank_components(self):
        """total_bank_delta == captain_bank_deposit + etapebonus_bank_deposit."""
        rider = make_rider("r1")
        stage = make_stage()
        result = make_result(
            finish_order=["r1"] + [f"o{i}" for i in range(4)],  # 5 of MY_TEAM in top 15
            gc_standings=[],
        )
        # Use only 5 of my_team in finish, but MY_TEAM has 8 — need 5 in result
        my_team_5 = MY_TEAM[:5]
        result2 = make_result(
            finish_order=MY_TEAM[:5],  # 5 my riders in top 15
            gc_standings=[],
        )
        delta = score_rider(rider, stage, result2, MY_TEAM, captain="r1",
                            stages_remaining=10)
        assert delta.total_bank_delta == (
            delta.captain_bank_deposit + delta.etapebonus_bank_deposit
        )
