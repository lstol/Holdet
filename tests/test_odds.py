"""
tests/test_odds.py — Unit tests for scoring/odds.py
"""
import pytest
from scoring.odds import (
    decimal_to_implied,
    normalise,
    odds_to_p_win,
    h2h_to_prob,
    apply_odds_to_probs,
    cli_odds_input,
)
from scoring.probabilities import RiderProb
from scoring.engine import Rider, Stage


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_rider(holdet_id: str, name: str) -> Rider:
    return Rider(
        holdet_id=holdet_id,
        person_id="p" + holdet_id,
        team_id="t1",
        name=name,
        team="Team A",
        team_abbr="TA",
        value=10_000_000,
        start_value=10_000_000,
        points=0,
        status="active",
        gc_position=None,
        jerseys=[],
        in_my_team=False,
        is_captain=False,
    )


def _make_prob(rider_id: str, stage_number: int = 1) -> RiderProb:
    return RiderProb(
        rider_id=rider_id,
        stage_number=stage_number,
        p_win=0.01,
        p_top3=0.05,
        p_top10=0.10,
        p_top15=0.15,
        p_dnf=0.02,
        source="model",
        model_confidence=0.6,
    )


def _make_stage() -> Stage:
    return Stage(
        number=1,
        race="giro_2026",
        stage_type="flat",
        distance_km=180.0,
        is_ttt=False,
        start_location="City A",
        finish_location="City B",
        sprint_points=[],
        kom_points=[],
    )


# ── decimal_to_implied ────────────────────────────────────────────────────────

def test_decimal_to_implied_2():
    assert decimal_to_implied(2.0) == pytest.approx(0.5)


def test_decimal_to_implied_4():
    assert decimal_to_implied(4.0) == pytest.approx(0.25)


# ── normalise ────────────────────────────────────────────────────────────────

def test_normalise_two_riders_sum_to_one():
    raw = {"a": 0.5, "b": 0.6}  # overround present
    result = normalise(raw)
    assert sum(result.values()) == pytest.approx(1.0)


def test_normalise_three_riders_removes_overround():
    # 1/2.0 + 1/3.0 + 1/4.0 = 0.5 + 0.333 + 0.25 = 1.083 (overround)
    raw = {"a": 0.5, "b": 1/3, "c": 0.25}
    result = normalise(raw)
    assert sum(result.values()) == pytest.approx(1.0)
    # 'a' should still have the highest probability
    assert result["a"] > result["b"] > result["c"]


# ── odds_to_p_win ─────────────────────────────────────────────────────────────

def test_odds_to_p_win_normalised():
    raw_odds = {"milan": 2.50, "girmay": 4.00, "cavendish": 6.00}
    result = odds_to_p_win(raw_odds)
    assert sum(result.values()) == pytest.approx(1.0)
    # milan (lower odds) should have higher probability
    assert result["milan"] > result["girmay"] > result["cavendish"]


# ── h2h_to_prob ───────────────────────────────────────────────────────────────

def test_h2h_to_prob_sums_to_one():
    result = h2h_to_prob("milan", 1.80, "girmay", 2.10)
    assert sum(result.values()) == pytest.approx(1.0)


def test_h2h_favourite_gets_higher_prob():
    result = h2h_to_prob("milan", 1.80, "girmay", 2.10)
    assert result["milan"] > result["girmay"]


# ── apply_odds_to_probs ───────────────────────────────────────────────────────

def _setup_two_riders():
    r1 = _make_rider("r1", "Jonathan Milan")
    r2 = _make_rider("r2", "Biniam Girmay")
    probs = {
        "r1": _make_prob("r1"),
        "r2": _make_prob("r2"),
    }
    riders_by_id = {"r1": r1, "r2": r2}
    return probs, riders_by_id


def test_apply_odds_sets_p_win():
    probs, riders_by_id = _setup_two_riders()
    p_win_map = {"milan": 0.40}
    result = apply_odds_to_probs(probs, p_win_map, riders_by_id)
    assert result["r1"].p_win == pytest.approx(0.40, abs=1e-4)


def test_apply_odds_hierarchy_consistent():
    probs, riders_by_id = _setup_two_riders()
    p_win_map = {"milan": 0.10}
    result = apply_odds_to_probs(probs, p_win_map, riders_by_id)
    rp = result["r1"]
    assert rp.p_top3 > rp.p_win
    assert rp.p_top10 > rp.p_top3
    assert rp.p_top15 > rp.p_top10


def test_apply_odds_unmatched_rider_unchanged():
    probs, riders_by_id = _setup_two_riders()
    original_p_win = probs["r2"].p_win
    p_win_map = {"milan": 0.40}
    result = apply_odds_to_probs(probs, p_win_map, riders_by_id)
    assert result["r2"].p_win == original_p_win


def test_apply_odds_source_set_to_odds():
    probs, riders_by_id = _setup_two_riders()
    apply_odds_to_probs(probs, {"milan": 0.30}, riders_by_id)
    assert probs["r1"].source == "odds"


def test_apply_odds_model_confidence_set():
    probs, riders_by_id = _setup_two_riders()
    apply_odds_to_probs(probs, {"milan": 0.30}, riders_by_id)
    assert probs["r1"].model_confidence == pytest.approx(0.8)


# ── cli_odds_input ────────────────────────────────────────────────────────────

def test_cli_skip_returns_unchanged():
    r1 = _make_rider("r1", "Jonathan Milan")
    probs = {"r1": _make_prob("r1")}
    stage = _make_stage()
    original_p_win = probs["r1"].p_win

    inputs = iter(["skip"])
    result = cli_odds_input(probs, stage, [r1], _input_fn=lambda _: next(inputs))
    assert result["r1"].p_win == original_p_win


def test_cli_single_outright_applied():
    r1 = _make_rider("r1", "Jonathan Milan")
    probs = {"r1": _make_prob("r1")}
    stage = _make_stage()

    # "milan 2.50" then "done"
    inputs = iter(["milan 2.50", "done"])
    result = cli_odds_input(probs, stage, [r1], _input_fn=lambda _: next(inputs))
    # With only one rider, normalised p_win == 1.0
    assert result["r1"].p_win == pytest.approx(1.0, abs=1e-4)
    assert result["r1"].source == "odds"


def test_cli_invalid_line_skipped_valid_applied():
    r1 = _make_rider("r1", "Jonathan Milan")
    r2 = _make_rider("r2", "Biniam Girmay")
    probs = {"r1": _make_prob("r1"), "r2": _make_prob("r2")}
    stage = _make_stage()

    inputs = iter(["NOTVALID", "milan 2.50", "girmay 4.00", "done"])
    result = cli_odds_input(probs, stage, [r1, r2], _input_fn=lambda _: next(inputs))
    # Both riders should be updated (invalid line skipped)
    assert result["r1"].source == "odds"
    assert result["r2"].source == "odds"
    assert sum([result["r1"].p_win, result["r2"].p_win]) == pytest.approx(1.0, abs=1e-3)


def test_cli_h2h_applied_to_both_riders():
    r1 = _make_rider("r1", "Jonathan Milan")
    r2 = _make_rider("r2", "Biniam Girmay")
    probs = {"r1": _make_prob("r1"), "r2": _make_prob("r2")}
    stage = _make_stage()

    inputs = iter(["h2h milan 1.80 vs girmay 2.10", "done"])
    result = cli_odds_input(probs, stage, [r1, r2], _input_fn=lambda _: next(inputs))
    assert result["r1"].source == "odds"
    assert result["r2"].source == "odds"
    # milan (lower odds) should have higher p_win
    assert result["r1"].p_win > result["r2"].p_win
