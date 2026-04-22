"""
scoring/simulator.py — Monte Carlo simulator for value projections.

Two simulation modes:
  simulate_rider / simulate_all_riders — independent per-rider draws (fast, legacy)
  simulate_stage_outcome / simulate_team — coherent stage-level draws (accurate)

The stage-level simulation guarantees a valid finish order (Plackett-Luce),
coherent etapebonus, team bonus, and dynamic captain selection.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from scoring.engine import (
    Rider, Stage, StageResult, score_rider,
    STAGE_POSITION_TABLE, TTT_PLACEMENT_TABLE,
)
from scoring.probabilities import RiderProb, RiderRole, _rider_type


# ── Output schemas ─────────────────────────────────────────────────────────────

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


@dataclass
class TeamSimResult:
    team_ids: list
    captain_id: str
    expected_value: float
    percentile_10: float
    percentile_50: float
    percentile_80: float
    percentile_95: float
    etapebonus_ev: float = 0.0   # mean etapebonus across simulations
    etapebonus_p95: float = 0.0  # 95th percentile etapebonus


# ── Stage scenario definitions (A1) ───────────────────────────────────────────

STAGE_SCENARIOS: dict[str, list] = {
    "flat":     [("bunch_sprint", 0.65), ("reduced_sprint", 0.20), ("breakaway", 0.15)],
    "hilly":    [("bunch_sprint", 0.25), ("reduced_sprint", 0.25), ("breakaway", 0.30), ("gc_day", 0.20)],
    "mountain": [("gc_day", 0.70), ("breakaway", 0.25), ("reduced_sprint", 0.05)],
    "itt":      [("itt", 1.0)],
    "ttt":      [("itt", 1.0)],
}

# Role weight multipliers per scenario.
# Applied to base p_top15 weight before Plackett-Luce sampling.
SCENARIO_MULTIPLIERS: dict[str, dict[str, float]] = {
    "bunch_sprint": {
        RiderRole.GC_CONTENDER: 0.40,
        RiderRole.SPRINTER:     4.00,
        RiderRole.CLIMBER:      0.30,
        RiderRole.BREAKAWAY:    0.60,
        RiderRole.TT:           0.50,
        RiderRole.DOMESTIQUE:   0.30,
    },
    "reduced_sprint": {
        RiderRole.GC_CONTENDER: 0.70,
        RiderRole.SPRINTER:     2.00,
        RiderRole.CLIMBER:      0.60,
        RiderRole.BREAKAWAY:    1.80,
        RiderRole.TT:           0.60,
        RiderRole.DOMESTIQUE:   0.50,
    },
    "breakaway": {
        RiderRole.GC_CONTENDER: 0.40,
        RiderRole.SPRINTER:     0.40,
        RiderRole.CLIMBER:      1.20,
        RiderRole.BREAKAWAY:    3.50,
        RiderRole.TT:           0.80,
        RiderRole.DOMESTIQUE:   0.70,
    },
    "gc_day": {
        RiderRole.GC_CONTENDER: 3.50,
        RiderRole.SPRINTER:     0.20,
        RiderRole.CLIMBER:      2.50,
        RiderRole.BREAKAWAY:    0.50,
        RiderRole.TT:           0.70,
        RiderRole.DOMESTIQUE:   0.20,
    },
    "itt": {
        RiderRole.GC_CONTENDER: 1.00,
        RiderRole.SPRINTER:     0.40,
        RiderRole.CLIMBER:      0.40,
        RiderRole.BREAKAWAY:    0.30,
        RiderRole.TT:           3.00,
        RiderRole.DOMESTIQUE:   0.20,
    },
}

_DOMESTIQUE_P15 = 0.02  # fallback for riders without a RiderProb entry


# ── Stage-level simulation helpers ────────────────────────────────────────────

def _sample_scenario(stage_type: str, rng) -> str:
    """Sample a race scenario for the given stage type."""
    scenarios = STAGE_SCENARIOS.get(stage_type, [("gc_day", 1.0)])
    if len(scenarios) == 1:
        return scenarios[0][0]
    probs = np.array([p for _, p in scenarios], dtype=float)
    probs /= probs.sum()
    idx = int(rng.choice(len(scenarios), p=probs))
    return scenarios[idx][0]


def _build_weights(
    riders: list[Rider],
    probs: dict[str, RiderProb],
    stage: Stage,
    scenario: str,
    dnf_mask: np.ndarray,
) -> np.ndarray:
    """
    Compute Plackett-Luce weights for each rider.
    Weight = p_top15 × scenario_multiplier[role], zero for DNF/DNS riders.
    """
    mult_table = SCENARIO_MULTIPLIERS.get(scenario, SCENARIO_MULTIPLIERS["gc_day"])
    weights = np.empty(len(riders), dtype=float)
    for i, rider in enumerate(riders):
        if dnf_mask[i]:
            weights[i] = 0.0
            continue
        rp = probs.get(rider.holdet_id)
        base = rp.p_top15 if rp is not None else _DOMESTIQUE_P15
        role = _rider_type(rider, stage)
        mult = mult_table.get(role, 1.0)
        weights[i] = max(base * mult, 1e-8)
    return weights


def _plackett_luce(weights: np.ndarray, rider_ids: list[str], rng) -> list[str]:
    """
    Sample finish order via the Gumbel-max trick (exact Plackett-Luce).
    Riders with weight=0 are excluded from the result.
    """
    log_w = np.log(np.maximum(weights, 1e-10))
    gumbels = rng.gumbel(size=len(weights))
    noisy = log_w + gumbels
    noisy[weights <= 0] = -np.inf
    order_indices = np.argsort(-noisy)
    return [rider_ids[i] for i in order_indices if weights[i] > 0]


def _sample_times_behind(finish_order: list[str], stage_type: str, rng) -> dict[str, int]:
    """Sample seconds behind winner for each finisher by position."""
    times: dict[str, int] = {}
    for pos_idx, rid in enumerate(finish_order[1:], 2):
        if stage_type == "flat":
            secs = int(rng.integers(0, 8)) if pos_idx <= 30 else int(rng.integers(30, 300))
        elif stage_type == "hilly":
            if pos_idx <= 3:
                secs = int(rng.integers(0, 30))
            elif pos_idx <= 15:
                secs = int(rng.integers(10, 120))
            else:
                secs = int(rng.integers(60, 600))
        elif stage_type in ("mountain", "itt"):
            if pos_idx <= 3:
                secs = int(rng.integers(0, 120))
            elif pos_idx <= 10:
                secs = int(rng.integers(30, 300))
            else:
                secs = int(rng.integers(120, 600))
        else:
            secs = 0
        times[rid] = secs
    return times


# ── A1: simulate_stage_outcome ────────────────────────────────────────────────

def simulate_stage_outcome(
    stage: Stage,
    riders: list[Rider],
    probs: dict[str, RiderProb],
    rng,
) -> StageResult:
    """
    Simulate one coherent stage outcome for the full rider field.

    Steps:
      1. Sample DNF riders from p_dnf per rider
      2. Sample scenario (bunch_sprint / gc_day / breakaway / …)
      3. Weight each rider by p_top15 × scenario multiplier
      4. Sample finish order via Plackett-Luce (Gumbel-max trick)
      5. Assign sprint/KOM points, jersey winners, GC standings
      6. Return StageResult — compatible with score_rider()

    finish_order is capped at top-30 for performance (sufficient for
    etapebonus top-15 and team-bonus top-3 lookups).
    """
    n = len(riders)
    rider_ids = [r.holdet_id for r in riders]

    # Step 1: DNF mask
    p_dnf_arr = np.array([
        1.0 if r.status == "dns" else (
            probs[r.holdet_id].p_dnf if r.holdet_id in probs else 0.02
        )
        for r in riders
    ])
    dnf_mask: np.ndarray = rng.random(n) < p_dnf_arr
    for i, r in enumerate(riders):
        if r.status == "dns":
            dnf_mask[i] = True

    dnf_list = [rider_ids[i] for i in range(n) if dnf_mask[i] and riders[i].status != "dns"]
    dns_list = [rider_ids[i] for i in range(n) if riders[i].status == "dns"]

    # Step 2-4: Scenario → weights → Plackett-Luce finish order
    scenario = _sample_scenario(stage.stage_type, rng)
    weights = _build_weights(riders, probs, stage, scenario, dnf_mask)
    full_order = _plackett_luce(weights, rider_ids, rng)
    # Cap at top-30 for score_rider performance (etapebonus needs ≤15, team-bonus ≤3)
    finish_order = full_order[:30]

    # Step 5a: Times behind winner
    times_behind = _sample_times_behind(finish_order, stage.stage_type, rng)

    # Step 5b: Sprint/KOM points — assign to riders who placed in top-20
    sprint_point_winners: dict[str, list[int]] = {}
    kom_point_winners: dict[str, list[int]] = {}
    for rid in finish_order[:20]:
        rp = probs.get(rid)
        if rp is None:
            continue
        if rp.expected_sprint_points > 0:
            pts = int(rng.poisson(rp.expected_sprint_points))
            if pts > 0:
                sprint_point_winners[rid] = [pts]
        if rp.expected_kom_points > 0:
            pts = int(rng.poisson(rp.expected_kom_points))
            if pts > 0:
                kom_point_winners[rid] = [pts]

    # Step 5c: Jersey winners — sample retention for each jersey holder
    jersey_winners: dict[str, str] = {}
    for i, (rider, rid) in enumerate(zip(riders, rider_ids)):
        if dnf_mask[i] or not rider.jerseys:
            continue
        rp = probs.get(rid)
        if rp is None:
            continue
        for jersey, p_retain in rp.p_jersey_retain.items():
            if float(rng.random()) < p_retain and jersey not in jersey_winners:
                jersey_winners[jersey] = rid

    # Step 5d: GC standings — preserve current positions for non-DNF riders
    gc_riding = [
        (riders[i].gc_position, rider_ids[i])
        for i in range(n)
        if riders[i].gc_position is not None and not dnf_mask[i]
    ]
    gc_standings = [rid for _, rid in sorted(gc_riding, key=lambda x: x[0])]

    return StageResult(
        stage_number=stage.number,
        finish_order=finish_order,
        times_behind_winner=times_behind,
        sprint_point_winners=sprint_point_winners,
        kom_point_winners=kom_point_winners,
        jersey_winners=jersey_winners,
        most_aggressive=None,
        dnf_riders=dnf_list,
        dns_riders=dns_list,
        disqualified=[],
        ttt_team_order=None,
        gc_standings=gc_standings,
    )


# ── A2: simulate_team (stage-level, returns TeamSimResult) ────────────────────

def simulate_team(
    team: list[str],
    captain: str,
    stage: Stage,
    riders: list[Rider],
    probs: dict[str, RiderProb],
    n: int = 5_000,
    stages_remaining: int = 1,
    seed: Optional[int] = None,
) -> TeamSimResult:
    """
    Stage-level team Monte Carlo simulation.

    For each of n simulations:
      - simulate_stage_outcome() generates a coherent finish order
      - All 8 team riders are scored against that result
      - Captain bonus applied to the best-performing rider dynamically
      - Etapebonus credited once (not once per rider)

    Returns TeamSimResult with EV, p10, p50, p80, p95 at team level.

    Parameters
    ----------
    team    : list of 8 holdet_ids
    captain : pre-selected captain holdet_id (for reporting; captain bonus
              is applied dynamically to the best performer each sim)
    riders  : full rider field (needed for coherent stage simulation)
    probs   : probability dict for full field
    """
    rng = np.random.default_rng(seed)
    rider_map = {r.holdet_id: r for r in riders}
    all_riders_map = rider_map  # for team-bonus lookup in score_rider

    team_riders = [rider_map[rid] for rid in team if rid in rider_map]
    totals = np.empty(n, dtype=float)
    etabonuses = np.empty(n, dtype=float)

    assert captain in (r.holdet_id for r in team_riders) or not team_riders, \
        f"captain {captain!r} must be in declared squad"

    for sim_i in range(n):
        result = simulate_stage_outcome(stage, riders, probs, rng)

        sim_values: dict[str, float] = {}
        etabonus = 0
        for rider in team_riders:
            vd = score_rider(
                rider=rider,
                stage=stage,
                result=result,
                my_team=team,
                captain=captain,
                stages_remaining=stages_remaining,
                all_riders=all_riders_map,
            )
            sim_values[rider.holdet_id] = float(vd.total_rider_value_delta)
            etabonus = vd.etapebonus_bank_deposit  # same value for all; keep last

        captain_bonus = max(0.0, sim_values.get(captain, 0.0))
        totals[sim_i] = sum(sim_values.values()) + captain_bonus + etabonus
        etabonuses[sim_i] = etabonus

    return TeamSimResult(
        team_ids=list(team),
        captain_id=captain,
        expected_value=float(np.mean(totals)),
        percentile_10=float(np.percentile(totals, 10)),
        percentile_50=float(np.percentile(totals, 50)),
        percentile_80=float(np.percentile(totals, 80)),
        percentile_95=float(np.percentile(totals, 95)),
        etapebonus_ev=float(np.mean(etabonuses)),
        etapebonus_p95=float(np.percentile(etabonuses, 95)),
    )


# ── Legacy per-rider simulation (kept for backward compatibility) ──────────────

def _sample_finish_position(probs: RiderProb, rng) -> tuple[Optional[int], bool]:
    """
    Sample a finish position from RiderProb probability brackets.

    Returns (position, is_dnf):
      position 1..15 for named finish positions
      position 16 for outside top 15
      is_dnf=True, position=None for abandonment
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
    """Sample seconds behind winner based on stage type and position."""
    if position <= 1:
        return 0
    stype = stage.stage_type
    if stype == "flat":
        return int(rng.integers(0, 5)) if position <= 15 else int(rng.integers(30, 300))
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
    """Construct a minimal synthetic StageResult for a single per-rider trial."""
    rid = rider.holdet_id
    finish_order: list[str] = []
    if not is_dnf and position is not None:
        for slot in range(1, 20):
            if slot == position:
                finish_order.append(rid)
            else:
                finish_order.append(f"__dummy_{slot}__")
    else:
        finish_order = [f"__dummy_{i}__" for i in range(1, 20)]

    gc_standings: list[str] = []
    if not is_dnf and gc_position is not None:
        max_slots = max(gc_position + 5, 20)
        for slot in range(1, max_slots + 1):
            if slot == gc_position:
                gc_standings.append(rid)
            else:
                gc_standings.append(f"__dummy_gc_{slot}__")

    sprint_point_winners: dict[str, list[int]] = {}
    if sprint_pts > 0:
        sprint_point_winners[rid] = [sprint_pts]

    kom_point_winners: dict[str, list[int]] = {}
    if kom_pts > 0:
        kom_point_winners[rid] = [kom_pts]

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
        ttt_team_order=None,
        gc_standings=gc_standings,
    )


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
    Uses independent draws — does not capture etapebonus or team bonuses correctly.
    Kept for backward compatibility and fast per-rider EV estimates.
    """
    rng = np.random.default_rng(seed)
    deltas = np.empty(n_simulations, dtype=float)

    for i in range(n_simulations):
        position, is_dnf = _sample_finish_position(probs, rng)

        sprint_pts = 0
        kom_pts = 0
        if not is_dnf:
            if probs.expected_sprint_points > 0:
                sprint_pts = int(rng.poisson(probs.expected_sprint_points))
            if probs.expected_kom_points > 0:
                kom_pts = int(rng.poisson(probs.expected_kom_points))

        jersey_winners: dict[str, str] = {}
        if not is_dnf:
            for jersey, p_retain in probs.p_jersey_retain.items():
                if float(rng.random()) < p_retain:
                    jersey_winners[jersey] = rider.holdet_id

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


def simulate_all_riders(
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
    Simulate all riders independently and return a dict of holdet_id → SimResult.

    Each rider is simulated via simulate_rider() (independent draws).
    Riders without a RiderProb entry in probs are skipped.

    Returns results sorted descending by expected_value.
    """
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
