"""
scoring/simulator.py — Monte Carlo simulator for per-rider value projections.

Uses scoring/engine.py internally for each trial.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from scoring.engine import (
    Rider, Stage, StageResult, score_rider,
    STAGE_POSITION_TABLE, TTT_PLACEMENT_TABLE,
)
from scoring.probabilities import RiderProb


# ── Output schema ─────────────────────────────────────────────────────────────

@dataclass
class SimResult:
    rider_id: str
    expected_value: float
    std_dev: float
    percentile_10: float
    percentile_50: float
    percentile_80: float
    percentile_90: float
    percentile_95: float
    p_positive: float


# ── Sampling helpers ──────────────────────────────────────────────────────────

def _sample_finish_position(probs: RiderProb, rng) -> tuple[Optional[int], bool]:
    """
    Sample a finish position from the RiderProb probability brackets.

    Returns (position, is_dnf):
      - position 1..15 for named finish positions
      - position 16 for outside top 15 (stage_position_value = 0)
      - is_dnf=True, position=None for abandonment
    """
    p_dnf = probs.p_dnf
    p1 = probs.p_win
    p2_3 = max(0.0, probs.p_top3 - probs.p_win)
    p4_10 = max(0.0, probs.p_top10 - probs.p_top3)
    p11_15 = max(0.0, probs.p_top15 - probs.p_top10)
    p_other = max(0.0, 1.0 - probs.p_top15 - p_dnf)

    weights = [p_dnf, p1, p2_3, p4_10, p11_15, p_other]
    total = sum(weights)
    if total <= 0:
        return (16, False)
    weights = [w / total for w in weights]

    r = float(rng.random())
    cumul = 0.0
    for bucket, w in enumerate(weights):
        cumul += w
        if r < cumul:
            break

    # bucket: 0=dnf, 1=1st, 2=2-3, 3=4-10, 4=11-15, 5=16+
    if bucket == 0:
        return (None, True)
    elif bucket == 1:
        return (1, False)
    elif bucket == 2:
        return (int(rng.integers(2, 4)), False)
    elif bucket == 3:
        return (int(rng.integers(4, 11)), False)
    elif bucket == 4:
        return (int(rng.integers(11, 16)), False)
    else:
        return (16, False)


def _sample_time_behind(stage: Stage, position: int, rng) -> int:
    """
    Sample seconds behind winner based on stage type and position.
    These are heuristic models — accuracy improves during Session 8 tuning.
    """
    if position <= 1:
        return 0

    stype = stage.stage_type

    if stype == "flat":
        # Bunch sprint: virtually no gaps for top 50 finishers
        if position <= 15:
            return int(rng.integers(0, 5))
        return int(rng.integers(30, 300))

    elif stype == "hilly":
        if position <= 3:
            return int(rng.integers(0, 30))
        elif position <= 15:
            return int(rng.integers(10, 120))
        return int(rng.integers(60, 600))

    elif stype in ("mountain", "itt"):
        if position <= 3:
            return int(rng.integers(0, 120))
        elif position <= 10:
            return int(rng.integers(30, 300))
        elif position <= 15:
            return int(rng.integers(120, 600))
        return int(rng.integers(300, 1800))

    # ttt / unknown
    return 0


def _build_stage_result(
    rider: Rider,
    stage: Stage,
    position: Optional[int],
    is_dnf: bool,
    sprint_pts: int,
    kom_pts: int,
    jersey_winners: dict[str, str],
    gc_position: Optional[int],
    seconds_behind: int,
) -> StageResult:
    """
    Construct a minimal synthetic StageResult for a single Monte Carlo trial.
    Other riders are represented by unique dummy IDs so the engine can compute
    team bonuses and etapebonus without name collisions.
    """
    rid = rider.holdet_id

    # finish_order: place rider at sampled position; fill surrounding slots
    # with dummies so position lookups inside engine work correctly.
    finish_order: list[str] = []
    if not is_dnf and position is not None:
        for slot in range(1, 20):
            if slot == position:
                finish_order.append(rid)
            else:
                finish_order.append(f"__dummy_{slot}__")
    else:
        finish_order = [f"__dummy_{i}__" for i in range(1, 20)]

    # gc_standings: place rider at current GC position if known
    gc_standings: list[str] = []
    if not is_dnf and gc_position is not None:
        max_slots = max(gc_position + 5, 20)
        for slot in range(1, max_slots + 1):
            if slot == gc_position:
                gc_standings.append(rid)
            else:
                gc_standings.append(f"__dummy_gc_{slot}__")

    # sprint / KOM points
    sprint_point_winners: dict[str, list[int]] = {}
    if sprint_pts > 0:
        sprint_point_winners[rid] = [sprint_pts]

    kom_point_winners: dict[str, list[int]] = {}
    if kom_pts > 0:
        kom_point_winners[rid] = [kom_pts]

    # time gaps
    times_behind: dict[str, int] = {}
    if not is_dnf and position is not None and position > 1:
        times_behind[rid] = seconds_behind

    return StageResult(
        stage_number=stage.number,
        finish_order=finish_order,
        times_behind_winner=times_behind,
        sprint_point_winners=sprint_point_winners,
        kom_point_winners=kom_point_winners,
        jersey_winners=jersey_winners,
        most_aggressive=None,
        dnf_riders=[rid] if is_dnf else [],
        dns_riders=[],
        disqualified=[],
        ttt_team_order=None,  # TTT team placement not modelled here
        gc_standings=gc_standings,
    )


# ── Core simulation functions ─────────────────────────────────────────────────

def simulate_rider(
    rider: Rider,
    stage: Stage,
    probs: RiderProb,
    my_team: list[str],
    captain: str,
    n_simulations: int = 10_000,
    stages_remaining: int = 1,
    seed: Optional[int] = None,
) -> SimResult:
    """
    Monte Carlo simulation of total_rider_value_delta for one rider.

    Parameters
    ----------
    rider           : Rider dataclass from engine.py
    stage           : Stage dataclass from engine.py
    probs           : RiderProb probability estimates for this rider/stage
    my_team         : list of holdet_ids currently on my team (for etapebonus)
    captain         : holdet_id of designated captain
    n_simulations   : number of Monte Carlo trials (default 10,000)
    stages_remaining: passed through to score_rider for DNS penalty calculation
    seed            : optional RNG seed for reproducibility

    Returns
    -------
    SimResult with EV, std_dev, percentile distribution, and p_positive
    """
    rng = np.random.default_rng(seed)
    deltas = np.empty(n_simulations, dtype=float)

    for i in range(n_simulations):
        position, is_dnf = _sample_finish_position(probs, rng)

        # Sprint and KOM points: sample from Poisson if expected > 0
        sprint_pts = 0
        kom_pts = 0
        if not is_dnf:
            if probs.expected_sprint_points > 0:
                sprint_pts = int(rng.poisson(probs.expected_sprint_points))
            if probs.expected_kom_points > 0:
                kom_pts = int(rng.poisson(probs.expected_kom_points))

        # Jersey retention: sample each held jersey independently
        jersey_winners: dict[str, str] = {}
        if not is_dnf:
            for jersey, p_retain in probs.p_jersey_retain.items():
                if float(rng.random()) < p_retain:
                    jersey_winners[jersey] = rider.holdet_id

        # Time behind winner
        seconds_behind = 0
        if not is_dnf and position is not None and position > 1:
            seconds_behind = _sample_time_behind(stage, position, rng)

        result = _build_stage_result(
            rider=rider,
            stage=stage,
            position=position,
            is_dnf=is_dnf,
            sprint_pts=sprint_pts,
            kom_pts=kom_pts,
            jersey_winners=jersey_winners,
            gc_position=rider.gc_position,
            seconds_behind=seconds_behind,
        )

        vd = score_rider(
            rider=rider,
            stage=stage,
            result=result,
            my_team=my_team,
            captain=captain,
            stages_remaining=stages_remaining,
        )

        deltas[i] = vd.total_rider_value_delta

    return SimResult(
        rider_id=rider.holdet_id,
        expected_value=float(np.mean(deltas)),
        std_dev=float(np.std(deltas)),
        percentile_10=float(np.percentile(deltas, 10)),
        percentile_50=float(np.percentile(deltas, 50)),
        percentile_80=float(np.percentile(deltas, 80)),
        percentile_90=float(np.percentile(deltas, 90)),
        percentile_95=float(np.percentile(deltas, 95)),
        p_positive=float(np.mean(deltas > 0)),
    )


def simulate_team(
    riders: list[Rider],
    stage: Stage,
    probs: dict[str, RiderProb],
    my_team: list[str],
    captain: str,
    n_simulations: int = 10_000,
    stages_remaining: int = 1,
    seed: Optional[int] = None,
) -> dict[str, SimResult]:
    """
    Simulate all riders in the list and return a dict of holdet_id → SimResult.

    Each rider is simulated independently. Riders without a RiderProb entry
    in `probs` are skipped.

    Returns results sorted descending by expected_value.
    """
    # Derive per-rider seeds from the master seed so runs are reproducible
    master_rng = np.random.default_rng(seed)
    rider_seeds = master_rng.integers(0, 2**31, size=len(riders)).tolist()

    results: dict[str, SimResult] = {}
    for rider, rseed in zip(riders, rider_seeds):
        rid = rider.holdet_id
        rp = probs.get(rid)
        if rp is None:
            continue
        results[rid] = simulate_rider(
            rider=rider,
            stage=stage,
            probs=rp,
            my_team=my_team,
            captain=captain,
            n_simulations=n_simulations,
            stages_remaining=stages_remaining,
            seed=int(rseed),
        )

    return dict(
        sorted(results.items(), key=lambda kv: kv[1].expected_value, reverse=True)
    )
