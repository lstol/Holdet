"""
tests/test_report.py — Unit tests for output/report.py.

All mocked — no live API calls, no file I/O side effects.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_rider(holdet_id="r1", name="Test Rider", team_abbr="TST",
                value=5_000_000, start_value=5_000_000, status="active",
                gc_position=None):
    from scoring.engine import Rider
    return Rider(
        holdet_id=holdet_id,
        person_id="p" + holdet_id,
        team_id="t1",
        name=name,
        team="Test Team",
        team_abbr=team_abbr,
        value=value,
        start_value=start_value,
        points=0,
        status=status,
        gc_position=gc_position,
        jerseys=[],
        in_my_team=False,
        is_captain=False,
    )


def _make_stage(number=1, stage_type="flat", is_ttt=False,
                start="Durres", finish="Tirana", distance=156.0):
    from scoring.engine import Stage
    return Stage(
        number=number,
        race="giro_2026",
        stage_type=stage_type,
        distance_km=distance,
        is_ttt=is_ttt,
        start_location=start,
        finish_location=finish,
        sprint_points=[],
        kom_points=[],
        notes="",
    )


def _make_rider_prob(rider_id="r1", source="model",
                     p_win=0.05, p_top3=0.15, p_top15=0.40, p_dnf=0.05):
    from scoring.probabilities import RiderProb
    return RiderProb(
        rider_id=rider_id,
        stage_number=1,
        p_win=p_win,
        p_top3=p_top3,
        p_top10=p_top3,
        p_top15=p_top15,
        p_dnf=p_dnf,
        source=source,
    )


def _make_profile_rec(profile, captain="r1", transfers=None,
                      ev=200_000, upside=400_000, downside=50_000):
    from scoring.optimizer import ProfileRecommendation
    return ProfileRecommendation(
        profile=profile,
        transfers=transfers or [],
        captain=captain,
        expected_value=ev,
        upside_90pct=upside,
        downside_10pct=downside,
        transfer_cost=0,
        reasoning="test",
    )


def _make_briefing(my_team=None, captain="r1", riders=None, probs=None,
                   suggested_profile=None, suggested_reason="default reason",
                   profiles=None):
    from scoring.optimizer import RiskProfile
    from output.report import BriefingOutput

    if my_team is None:
        my_team = ["r1"]
    if riders is None:
        riders = [_make_rider("r1")]
    if probs is None:
        probs = {"r1": _make_rider_prob("r1")}
    if profiles is None:
        profiles = {p: _make_profile_rec(p) for p in RiskProfile}

    return BriefingOutput(
        stage=_make_stage(),
        my_team=my_team,
        captain=captain,
        riders=riders,
        probs=probs,
        current_team_ev=200_000.0,
        suggested_profile=suggested_profile,
        suggested_profile_reason=suggested_reason,
        profiles=profiles,
    )


def _make_state(my_team=None, captain="r1", bank=50_000_000,
                stage=1, rank=None, total=None):
    return {
        "my_team": ["r1"] if my_team is None else my_team,
        "captain": captain,
        "bank": bank,
        "current_stage": stage,
        "stages_completed": [],
        "rank": rank,
        "total_participants": total,
        "probs_by_stage": {},
    }


# ── Tests: format_briefing ─────────────────────────────────────────────────────

class TestFormatBriefing(unittest.TestCase):

    def setUp(self):
        from output.report import format_briefing
        self.format_briefing = format_briefing

    def test_returns_string(self):
        b = _make_briefing()
        result = self.format_briefing(b, _make_state())
        self.assertIsInstance(result, str)

    def test_stage_header_present(self):
        """Stage header must include stage number, start, finish, type, distance."""
        b = _make_briefing()
        result = self.format_briefing(b, _make_state())
        self.assertIn("Stage 1", result)
        self.assertIn("Durres", result)
        self.assertIn("Tirana", result)
        self.assertIn("flat", result)
        self.assertIn("156", result)

    def test_dns_alert_when_rider_not_active(self):
        """DNS alert must appear when a team rider has status != 'active'."""
        dns_rider = _make_rider("r1", name="Sick Guy", status="dns")
        b = _make_briefing(riders=[dns_rider])
        result = self.format_briefing(b, _make_state())
        self.assertIn("ALERT", result)
        self.assertIn("Sick Guy", result)

    def test_no_dns_alert_for_active_rider(self):
        b = _make_briefing()  # default rider is active
        result = self.format_briefing(b, _make_state())
        self.assertNotIn("ALERT", result)

    def test_adjusted_probs_marked_with_asterisk(self):
        """Source='adjusted' must appear marked with * in the table."""
        probs = {"r1": _make_rider_prob("r1", source="adjusted")}
        b = _make_briefing(probs=probs)
        result = self.format_briefing(b, _make_state())
        self.assertIn("adjusted", result)
        self.assertIn("*", result)

    def test_four_profile_rows_present(self):
        """All 4 profile names must appear in the output."""
        b = _make_briefing()
        result = self.format_briefing(b, _make_state())
        for name in ("ANCHOR", "BALANCED", "AGGRESSIVE", "ALL-IN"):
            self.assertIn(name, result)

    def test_suggested_profile_present(self):
        from scoring.optimizer import RiskProfile
        b = _make_briefing(
            suggested_profile=RiskProfile.BALANCED,
            suggested_reason="standard situation",
        )
        result = self.format_briefing(b, _make_state())
        self.assertIn("SUGGESTED", result)
        self.assertIn("BALANCED", result)
        self.assertIn("standard situation", result)


# ── Tests: format_status ───────────────────────────────────────────────────────

class TestFormatStatus(unittest.TestCase):

    def setUp(self):
        from output.report import format_status
        self.format_status = format_status

    def test_returns_string(self):
        rider = _make_rider("r1", name="Jonas Vingegaard")
        result = self.format_status(_make_state(), [rider])
        self.assertIsInstance(result, str)

    def test_captain_marker_present(self):
        """Captain must be marked with [C]."""
        rider = _make_rider("r1", name="Jonas Vingegaard")
        result = self.format_status(_make_state(captain="r1"), [rider])
        self.assertIn("[C]", result)

    def test_dns_alert_in_status(self):
        """DNS rider must trigger ALERT in status output."""
        rider = _make_rider("r1", name="Sick Rider", status="dns")
        result = self.format_status(_make_state(), [rider])
        self.assertIn("ALERT", result)
        self.assertIn("Sick Rider", result)

    def test_no_dns_alert_for_active_in_status(self):
        rider = _make_rider("r1", name="Healthy Rider", status="active")
        result = self.format_status(_make_state(), [rider])
        self.assertNotIn("ALERT", result)

    def test_total_value_shown(self):
        """Total team value must appear in status."""
        rider = _make_rider("r1", value=7_500_000)
        result = self.format_status(_make_state(), [rider])
        self.assertIn("Total team value", result)
        self.assertIn("7.50", result)

    def test_bank_shown(self):
        state = _make_state(bank=4_500_000)
        rider = _make_rider("r1")
        result = self.format_status(state, [rider])
        self.assertIn("4.500", result)

    def test_no_team_message_when_empty(self):
        state = _make_state(my_team=[])
        result = self.format_status(state, [])
        self.assertIn("No team loaded", result)


if __name__ == "__main__":
    unittest.main()
