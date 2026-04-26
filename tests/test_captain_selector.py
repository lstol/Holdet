"""
tests/test_captain_selector.py — Session 22: Captain selection module.

5 tests covering team-only selection, mode effects, candidate count,
candidate ordering, and EV sourcing from sim_results.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from scoring.captain_selector import select_captain, LAMBDA
from scoring.probabilities import RiderProb


# ── Minimal stubs ─────────────────────────────────────────────────────────────

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


def _make_prob(rider_id: str, p_win: float = 0.05) -> RiderProb:
    return RiderProb(
        rider_id=rider_id,
        stage_number=1,
        p_win=p_win,
        p_top3=p_win * 2,
        p_top10=p_win * 4,
        p_top15=p_win * 5,
        p_dnf=0.02,
        source="model",
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestCaptainSelector:

    def test_captain_selected_from_team_only(self):
        """captain_id must be in the team list — no out-of-squad selection."""
        team = ["r1", "r2", "r3"]
        probs = {rid: _make_prob(rid) for rid in ["r1", "r2", "r3", "r4", "r5"]}
        sim_results = {rid: _SimResult(rider_id=rid, expected_value=float(i * 10_000))
                       for i, rid in enumerate(["r1", "r2", "r3", "r4", "r5"])}

        captain_id, candidates = select_captain(team, probs, sim_results, mode="balanced")

        assert captain_id in team, f"captain_id '{captain_id}' is not in team {team}"
        for c in candidates:
            assert c["rider_id"] in team, f"candidate {c['rider_id']} not in team"

    def test_captain_mode_affects_selection(self):
        """
        stable picks highest EV, aggressive picks highest EV + 1.5 * p_win.
        Result must differ when a high-p_win rider has lower EV.
        """
        team = ["ev_king", "win_machine"]
        # ev_king: highest EV but low p_win
        # win_machine: lower EV but much higher p_win
        probs = {
            "ev_king":     _make_prob("ev_king",     p_win=0.02),
            "win_machine": _make_prob("win_machine",  p_win=0.40),
        }
        # EV values must be small enough for λ * p_win to matter in the formula.
        # Formula: score = ev + λ * p_win  (p_win ∈ [0,1])
        # With λ=1.5: win_machine = 0.5 + 1.5*0.40 = 1.10 > ev_king = 1.0 + 1.5*0.02 = 1.03
        sim_results = {
            "ev_king":     _SimResult(rider_id="ev_king",     expected_value=1.0),
            "win_machine": _SimResult(rider_id="win_machine", expected_value=0.5),
        }

        stable_captain, _     = select_captain(team, probs, sim_results, mode="stable")
        aggressive_captain, _ = select_captain(team, probs, sim_results, mode="aggressive")

        assert stable_captain == "ev_king", "stable mode must pick highest EV rider"
        assert aggressive_captain == "win_machine", "aggressive mode must prefer high p_win"

    def test_captain_candidates_always_returns_five(self):
        """Even when team has exactly 5 eligible riders, candidates has 5 entries."""
        team = [f"r{i}" for i in range(5)]
        probs = {rid: _make_prob(rid) for rid in team}
        sim_results = {
            rid: _SimResult(rider_id=rid, expected_value=float(i * 5_000))
            for i, rid in enumerate(team)
        }

        _, candidates = select_captain(team, probs, sim_results, mode="balanced")
        assert len(candidates) == 5

    def test_captain_candidates_sorted_by_score(self):
        """candidates[0].score >= candidates[1].score >= ... >= candidates[-1].score."""
        team = [f"r{i}" for i in range(8)]
        probs = {rid: _make_prob(rid, p_win=float(i) * 0.01 + 0.01) for i, rid in enumerate(team)}
        sim_results = {
            rid: _SimResult(rider_id=rid, expected_value=float(i * 10_000))
            for i, rid in enumerate(team)
        }

        _, candidates = select_captain(team, probs, sim_results, mode="balanced")
        scores = [c["score"] for c in candidates]
        assert scores == sorted(scores, reverse=True), "candidates must be sorted descending by score"

    def test_captain_ev_from_sim_results_not_probs(self):
        """ev in candidates must match sim_results[rid].expected_value, not probs[rid].p_win."""
        team = ["r1", "r2"]
        probs = {"r1": _make_prob("r1", p_win=0.20), "r2": _make_prob("r2", p_win=0.10)}
        expected_evs = {"r1": 75_000.0, "r2": 42_000.0}
        sim_results = {
            rid: _SimResult(rider_id=rid, expected_value=expected_evs[rid])
            for rid in team
        }

        _, candidates = select_captain(team, probs, sim_results, mode="stable")
        for c in candidates:
            rid = c["rider_id"]
            assert abs(c["ev"] - expected_evs[rid]) < 1e-6, (
                f"ev for {rid} should be {expected_evs[rid]}, got {c['ev']}"
            )
            assert abs(c["p_win"] - probs[rid].p_win) < 1e-6, (
                f"p_win for {rid} should come from probs, got {c['p_win']}"
            )
