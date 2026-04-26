"""
scoring/optimizer.py — Transfer and captain recommendations across all 4 risk profiles.

Profiles are defined by SQUAD COMPOSITION OBJECTIVE, not transfer count.
Transfer count is an output of the optimizer, never an input constraint.

Session 15: greedy swap loop is now wired to team-level simulation via
_eval_team() with memoization. n=500, seed=42 fixed within each optimize()
call for stable comparisons. Performance note: 400+ unique squad evaluations
at n=500 can take several minutes — acceptable for a pre-race decision tool.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from config import LAMBDA_TRANSFER
from scoring.engine import Rider, Stage
from scoring.probabilities import RiderProb
from scoring.simulator import SimResult, TeamSimResult, simulate_team
from scoring.stage_intent import StageIntent, compute_stage_intent, apply_intelligence_signals


# ── Enums ──────────────────────────────────────────────────────────────────────

class RiskProfile(Enum):
    ANCHOR     = "anchor"
    BALANCED   = "balanced"
    AGGRESSIVE = "aggressive"
    ALL_IN     = "all_in"


# ── Output schemas ─────────────────────────────────────────────────────────────

@dataclass
class TransferAction:
    action: str       # "sell" | "buy"
    rider_id: str
    rider_name: str
    value: int
    fee: int          # 0 for sells, 1% of value for buys
    reasoning: str


@dataclass
class ProfileRecommendation:
    profile: RiskProfile
    transfers: list      # list[TransferAction]
    captain: str         # holdet_id
    expected_value: float
    upside_90pct: float
    downside_10pct: float
    transfer_cost: int
    reasoning: str
    team_result: Optional[TeamSimResult] = field(default=None)
    lookahead_ev: Optional[float] = field(default=None)  # set when enable_lookahead=True (Session 21)


# ── Module-level eval caches ──────────────────────────────────────────────────

_eval_cache: dict = {}
_lookahead_cache: dict = {}  # separate cache for 2-stage results (Session 21)

# Lookahead constants (Session 20 infrastructure — activated in Session 21)
LOOKAHEAD_ALPHA: float = 0.85   # discount for next-stage EV
LOOKAHEAD_N: int = 200          # fast-sim count for N+1 lookahead

# Noise floor for improvement thresholds (≈ one position improvement at n=500).
# Prevents accepting random simulation noise as a meaningful gain.
# Tune after Giro validation data is available.
NOISE_FLOOR = 20_000


# SESSION 20 BOUNDARY — DO NOT WIRE BELOW THIS POINT UNTIL SESSION 20
# apply_intent_to_ev() and compute_transfer_penalty() are defined here
# but intentionally not called from _eval_team() or optimize().
# Wiring happens in Session 20 (lookahead optimizer).
# See: docs/MULTI_STAGE_ARCHITECTURE.md

def apply_intent_to_ev(base_ev: float, intent: StageIntent) -> float:
    """Scale EV by stage intent. win_priority boosts expected stage winners."""
    return base_ev * (1.0 + 0.3 * intent.win_priority)


def compute_transfer_penalty(fee: int, intent: StageIntent) -> float:
    """
    Scale transfer fee by intent.transfer_pressure.
    High pressure = transfer cost weighted more heavily in decision.
    Returns the adjusted penalty (always >= fee).
    """
    return fee * (1.0 + intent.transfer_pressure)


# ── Lookahead: 2-stage team evaluator (infrastructure for Session 21) ─────────

def evaluate_action_multistage(
    squad_ids: tuple,
    captain_id: str,
    stage: Stage,
    all_riders: list,
    probs: dict,
    next_stage: Stage,
    probs_n1: dict,
    intent_n1: Optional[StageIntent] = None,
    n: int = LOOKAHEAD_N,
    seed: int = 42,
    scenario_priors: Optional[dict] = None,
) -> float:
    """
    Two-stage combined EV: current stage + LOOKAHEAD_ALPHA × next stage.

    Stage N: team simulation at n sims.
    Stage N+1: fast lookahead at LOOKAHEAD_N sims, separate cache.
    Returns combined expected value for squad evaluation.

    Session 21 activates this via enable_lookahead=True in optimize().
    When enable_lookahead=False (default), _eval_team is called directly — no change.
    """
    global _lookahead_cache

    # Stage N: standard team evaluation
    result_n = _eval_team(
        squad_ids, captain_id, stage, all_riders, probs,
        n=n, seed=seed, scenario_priors=scenario_priors,
    )

    # Stage N+1: fast lookahead, separate cache keyed by stage number
    key_n1 = (tuple(sorted(squad_ids)), captain_id, next_stage.number)
    if key_n1 not in _lookahead_cache:
        _lookahead_cache[key_n1] = simulate_team(
            team=list(squad_ids),
            captain=captain_id,
            stage=next_stage,
            riders=all_riders,
            probs=probs_n1,
            n=LOOKAHEAD_N,
            seed=seed,
        )
    result_n1 = _lookahead_cache[key_n1]

    return result_n.expected_value + LOOKAHEAD_ALPHA * result_n1.expected_value


# ── Helpers ────────────────────────────────────────────────────────────────────

def _profile_metric(sim: SimResult, profile: RiskProfile) -> float:
    """Primary optimisation metric for this profile (per-rider SimResult)."""
    if profile == RiskProfile.ANCHOR:
        return sim.percentile_10
    elif profile == RiskProfile.BALANCED:
        return sim.expected_value
    elif profile == RiskProfile.AGGRESSIVE:
        return sim.percentile_80
    else:  # ALL_IN
        return sim.percentile_95


def _team_metric(result: TeamSimResult, profile: RiskProfile) -> float:
    """Primary optimisation metric for this profile (team TeamSimResult)."""
    if profile == RiskProfile.ANCHOR:
        return result.percentile_10
    elif profile == RiskProfile.BALANCED:
        return result.expected_value
    elif profile == RiskProfile.AGGRESSIVE:
        return result.percentile_80
    else:  # ALL_IN
        return result.percentile_95


def _eval_team(
    squad_ids: tuple,
    captain_id: str,
    stage: Stage,
    all_riders: list,
    probs: dict,
    n: int = 500,
    seed: int = 42,
    scenario_priors: Optional[dict] = None,
) -> TeamSimResult:
    """Evaluate a squad via team Monte Carlo simulation, with memoization."""
    key = (tuple(sorted(squad_ids)), captain_id)  # sort enforced here — caller order doesn't matter
    if key not in _eval_cache:
        _eval_cache[key] = simulate_team(
            team=list(squad_ids),
            captain=captain_id,
            stage=stage,
            riders=all_riders,
            probs=probs,
            n=n,
            seed=seed,
            scenario_priors=scenario_priors,
        )
    return _eval_cache[key]


def _is_gc_anchor(rider: Rider) -> bool:
    """True if rider is GC top-10 — protected under ANCHOR profile."""
    return rider.gc_position is not None and rider.gc_position <= 10


def _buy_fee(value: int) -> int:
    """1% purchase fee, rounded to nearest integer."""
    return max(0, round(value * 0.01))


def _count_teams(squad_ids: list, rider_map: dict) -> dict:
    """Return {team_abbr: count} for the given squad."""
    counts: dict = {}
    for sid in squad_ids:
        r = rider_map.get(sid)
        if r:
            counts[r.team_abbr] = counts.get(r.team_abbr, 0) + 1
    return counts


def _constraints_ok(
    squad_ids: list,
    rider_map: dict,
    budget: float,
    fee_for_buys: int = 0,
) -> bool:
    """Check team-count constraint (≤2 per team) and budget."""
    team_counts: dict = {}
    for sid in squad_ids:
        r = rider_map.get(sid)
        if r:
            tc = team_counts.get(r.team_abbr, 0) + 1
            if tc > 2:
                return False
            team_counts[r.team_abbr] = tc
    return budget >= fee_for_buys


def _build_candidates(eligible: dict, sim_results: dict) -> list:
    """
    Hybrid EV + p95 union candidate filter (A6).
    Returns up to ~50 rider ids from eligible active riders.
    """
    if len(eligible) <= 60:
        return list(eligible.keys())
    top_ev  = sorted(eligible, key=lambda r: sim_results[r].expected_value if r in sim_results else 0, reverse=True)[:25]
    top_p95 = sorted(eligible, key=lambda r: sim_results[r].percentile_95 if r in sim_results else 0, reverse=True)[:25]
    return list(set(top_ev) | set(top_p95))


def _try_double_swaps(
    active_squad: list,
    candidates: list,
    current_metric: float,
    current_result: TeamSimResult,
    profile: RiskProfile,
    stage: Stage,
    all_riders: list,
    probs: dict,
    rider_map: dict,
    sim_results: dict,
    remaining_budget: float,
    n: int = 500,
    n_attempts: int = 20,
    scenario_priors: Optional[dict] = None,
    intent: Optional[StageIntent] = None,
    eval_fn=None,  # reserved for Session 21 — pass evaluate_action_multistage when ready
) -> Optional[tuple]:
    """
    Random double-swap exploration after greedy convergence (A5).
    Returns (proposed_squad, new_captain) if 1%+ improvement found, else None.
    """
    threshold = max(0.01 * abs(current_metric), NOISE_FLOOR)
    eligible_outside = [c for c in candidates if c not in active_squad]
    if len(eligible_outside) < 2 or len(active_squad) < 2:
        return None

    for _ in range(n_attempts):
        try:
            sell_pair = random.sample(active_squad, 2)
            buy_pair  = random.sample(eligible_outside, 2)
        except ValueError:
            break

        # ANCHOR: never sell GC top-10 riders or jersey holders
        if profile == RiskProfile.ANCHOR:
            if any(
                _is_gc_anchor(rider_map[s]) or rider_map[s].jerseys
                for s in sell_pair if s in rider_map
            ):
                continue

        proposed = [
            buy_pair[sell_pair.index(r)] if r in sell_pair else r
            for r in active_squad
        ]

        # Check team constraint + budget
        buy_cost = sum(_buy_fee(rider_map[b].value) for b in buy_pair if b in rider_map)
        sell_credits = sum(rider_map[s].value for s in sell_pair if s in rider_map)
        new_budget = remaining_budget + sell_credits - sum(
            rider_map[b].value + _buy_fee(rider_map[b].value)
            for b in buy_pair if b in rider_map
        )
        if new_budget < 0:
            continue
        if not _constraints_ok(proposed, rider_map, new_budget):
            continue

        proposed_t = tuple(sorted(proposed))
        proposed_captain = _pick_captain(proposed, sim_results, profile, rider_map, intent=intent)
        result = _eval_team(proposed_t, proposed_captain, stage, all_riders, probs, n=n, seed=42, scenario_priors=scenario_priors)
        if _team_metric(result, profile) > current_metric + threshold:
            return (proposed, proposed_captain, sell_pair, buy_pair)

    return None


def _eval_swap(
    profile: RiskProfile,
    gain: float,
    buy_ev: float,
    sell_ev: float,
    fee: int,
    stages_remaining: int,
    current_metric: float = 0.0,
) -> Optional[float]:
    """
    Evaluate whether a swap is acceptable under the profile's transfer logic.
    Returns a score (higher = better) if acceptable, else None.

    With team-level simulation:
      buy_ev  = proposed team expected_value
      sell_ev = current team expected_value
      gain    = _team_metric(proposed) - _team_metric(current)

    Uses max(1% of current_metric, NOISE_FLOOR) as minimum meaningful gain
    to avoid accepting simulation noise as improvements.
    """
    noise_threshold = max(0.01 * abs(current_metric), NOISE_FLOOR)

    if profile == RiskProfile.ANCHOR:
        fee_per_stage = fee / max(stages_remaining, 1)
        effective_gain = gain - fee_per_stage
        if effective_gain < noise_threshold:
            return None
        return effective_gain

    elif profile == RiskProfile.BALANCED:
        ev_gain = buy_ev - sell_ev
        fee_threshold = fee / max(stages_remaining, 1)
        if ev_gain <= fee_threshold or gain < noise_threshold:
            return None
        return gain

    elif profile == RiskProfile.AGGRESSIVE:
        if gain < noise_threshold:
            return None
        ev_change = buy_ev - sell_ev
        if ev_change < -30_000 and gain < 80_000:
            return None
        return gain

    else:  # ALL_IN
        if gain < noise_threshold:
            return None
        return gain


def _pick_captain(
    squad_ids: list,
    sim_results: dict,
    profile: RiskProfile,
    rider_map: dict,
    intent: Optional[StageIntent] = None,
) -> str:
    """Select captain per profile rules using per-rider SimResult."""
    eligible_ids = [rid for rid in squad_ids if rid in sim_results]
    if not eligible_ids:
        return squad_ids[0] if squad_ids else ""

    if profile == RiskProfile.ANCHOR:
        return max(eligible_ids, key=lambda rid: sim_results[rid].percentile_10)

    elif profile == RiskProfile.BALANCED:
        # NOTE — SESSION 20 DOUBLE-COUNTING RISK:
        # win_priority biases captain toward high-p95 riders here.
        # If apply_intent_to_ev() is wired in Session 20 (scales all EVs by
        # win_priority), this term may need to be reduced or removed to avoid
        # counting win_priority twice. Revisit at Session 20 start.
        def balanced_score(rid):
            s = sim_results[rid]
            return s.expected_value + (intent.win_priority * s.percentile_95 * 0.1 if intent else 0)
        return max(eligible_ids, key=balanced_score)

    elif profile == RiskProfile.AGGRESSIVE:
        return max(eligible_ids, key=lambda rid: sim_results[rid].percentile_95)

    else:  # ALL_IN
        return max(eligible_ids, key=lambda rid: sim_results[rid].percentile_95)


def _build_reasoning(
    profile: RiskProfile,
    squad_ids: list,
    rider_map: dict,
    n_transfers: int,
    stage: Stage,
    intent: Optional[StageIntent] = None,
) -> str:
    descriptions = {
        RiskProfile.ANCHOR: (
            "Maximising floor value (p10). GC top-10 riders and jersey holders "
            "protected. {} transfer(s) made only where replacement strictly improves "
            "p10 outcome net of fee amortised over remaining stages."
        ),
        RiskProfile.BALANCED: (
            "Maximising total expected value. {} transfer(s) accepted where EV gain "
            "exceeds fee amortised over remaining stages. Best risk-adjusted captain."
        ),
        RiskProfile.AGGRESSIVE: (
            "Maximising {} upside (p80). {} transfer(s) targeting stage-type "
            "specialists. Accepted EV reduction ≥ -30k if p80 gain ≥ +80k."
        ),
        RiskProfile.ALL_IN: (
            "Conviction bet on {} scenario. {} transfer(s) optimising purely for "
            "p95 outcome. Fee payback is secondary concern."
        ),
    }
    if profile == RiskProfile.ANCHOR:
        base = descriptions[profile].format(n_transfers)
    elif profile == RiskProfile.BALANCED:
        base = descriptions[profile].format(n_transfers)
    elif profile == RiskProfile.AGGRESSIVE:
        base = descriptions[profile].format(stage.stage_type, n_transfers)
    else:
        base = descriptions[profile].format(stage.stage_type, n_transfers)

    if intent and intent.transfer_pressure >= 0.65:
        suffix = (
            f"High transfer pressure ({intent.transfer_pressure:.2f}) — "
            "stage context favours aggressive rotation today."
        )
        if not base.endswith("."):
            base += "."
        base += " " + suffix
    return base


# ── Core optimizer ────────────────────────────────────────────────────────────

def optimize(
    riders: list,
    my_team: list,
    stage: Stage,
    probs: dict,
    sim_results: dict,
    bank: float,
    risk_profile: RiskProfile,
    rank: Optional[int],
    total_participants: Optional[int],
    stages_remaining: int,
    n_sim: int = 500,
    scenario_priors: Optional[dict] = None,
    intent: Optional[StageIntent] = None,
    next_stage: Optional[Stage] = None,
    enable_lookahead: bool = False,          # Session 21: activate 2-stage eval
    probs_n1: Optional[dict] = None,         # Session 21: probs for next stage
    intent_n1: Optional[StageIntent] = None, # Session 21: intent for next stage
) -> ProfileRecommendation:
    """
    Find the optimal squad for the given risk profile.

    Algorithm (Session 15):
      1. Forced sells: remove DNS/DNF riders, collect credits.
      2. Fill: pad squad to 8 with best-metric eligible riders.
      3. Build candidate pool: hybrid EV+p95 union (~50 riders, A6).
      4. Greedy swaps: team-level simulation via _eval_team() with memoization.
      5. Double-swap exploration: random 2-for-2 after greedy convergence (A5).
      6. Captain: selected per profile using per-rider SimResult (A3).
      7. Final team_result: TeamSimResult stored on ProfileRecommendation (A7).

    n_sim : simulations per team evaluation (default 500). Lower values (50–200)
            trade accuracy for speed during optimization.
    enable_lookahead : Session 21 — routes evaluation through evaluate_action_multistage.
    """
    global _eval_cache, _lookahead_cache
    _eval_cache.clear()
    _lookahead_cache.clear()

    # _eval closure: routes to single-stage or 2-stage evaluator.
    # When enable_lookahead=False (default): identical to previous _eval_team calls.
    # When enable_lookahead=True + next_stage + probs_n1 set: uses evaluate_action_multistage.
    # Session 21 wires the greedy/double-swap loops to use _eval instead of _eval_team.
    def _eval(squad_t: tuple, captain_id: str) -> float:
        if enable_lookahead and next_stage is not None and probs_n1 is not None:
            return evaluate_action_multistage(
                squad_t, captain_id, stage, riders, probs,
                next_stage, probs_n1, intent_n1, n=n_sim,
            )
        result = _eval_team(squad_t, captain_id, stage, riders, probs, n=n_sim, seed=42,
                            scenario_priors=scenario_priors)
        return _team_metric(result, risk_profile)

    _ = _eval  # referenced by Session 21 — suppress unused-variable warnings

    rider_map: dict = {r.holdet_id: r for r in riders}

    eligible: dict = {
        rid: r
        for rid, r in rider_map.items()
        if r.status == "active" and rid in sim_results
    }

    transfers: list = []
    remaining_budget = float(bank)

    # ── Step 1: forced sells ─────────────────────────────────────────────────
    active_squad: list = []
    for rid in my_team:
        r = rider_map.get(rid)
        if r is None:
            continue
        if r.status != "active":
            remaining_budget += r.value
            transfers.append(TransferAction(
                action="sell",
                rider_id=rid,
                rider_name=r.name,
                value=r.value,
                fee=0,
                reasoning=f"Forced sell: rider status='{r.status}'",
            ))
        elif rid in eligible:
            active_squad.append(rid)

    # Snapshot state after forced sells — used for diff-based transfer reporting.
    input_squad: list = list(active_squad)
    forced_sell_transfers: list = list(transfers)

    # ── Step 2: fill squad to 8 ──────────────────────────────────────────────
    if len(active_squad) < 8:
        team_counts = _count_teams(active_squad, rider_map)
        already_in: set = set(active_squad)
        building_from_scratch = len(active_squad) == 0

        def _cheapest_n_eligible(n: int, exclude: set, tc: dict) -> int:
            costs: list = []
            tc_copy = dict(tc)
            for _, r in sorted(eligible.items(), key=lambda x: x[1].value):
                if len(costs) >= n:
                    break
                if r.holdet_id in exclude:
                    continue
                if tc_copy.get(r.team_abbr, 0) >= 2:
                    continue
                costs.append(r.value + _buy_fee(r.value))
                tc_copy[r.team_abbr] = tc_copy.get(r.team_abbr, 0) + 1
            return sum(costs)

        def _fill_from(candidates_list: list, budget_aware: bool = False) -> None:
            nonlocal remaining_budget
            for buy_id, buy_rider in candidates_list:
                if len(active_squad) >= 8:
                    break
                if buy_id in already_in:
                    continue
                if team_counts.get(buy_rider.team_abbr, 0) >= 2:
                    continue
                fee = _buy_fee(buy_rider.value)
                cost = buy_rider.value + fee
                if remaining_budget < cost:
                    continue
                if budget_aware:
                    slots_left = 8 - len(active_squad) - 1
                    if slots_left > 0:
                        min_cost_remaining = _cheapest_n_eligible(
                            slots_left, already_in | {buy_id}, team_counts
                        )
                        if remaining_budget - cost < min_cost_remaining:
                            continue
                active_squad.append(buy_id)
                already_in.add(buy_id)
                remaining_budget -= cost
                team_counts[buy_rider.team_abbr] = team_counts.get(buy_rider.team_abbr, 0) + 1

        _fill_from(
            sorted(
                [(rid, r) for rid, r in eligible.items() if rid not in already_in],
                key=lambda x: _profile_metric(sim_results[x[0]], risk_profile),
                reverse=True,
            ),
            budget_aware=building_from_scratch,
        )

        if len(active_squad) < 8:
            _fill_from(sorted(
                [(rid, r) for rid, r in eligible.items() if rid not in already_in],
                key=lambda x: x[1].value,
            ))

    # ── Step 3: build candidate pool (A6) ───────────────────────────────────
    candidates = _build_candidates(eligible, sim_results)

    # ── Step 4: greedy swap — team-level (A4) ────────────────────────────────
    current_captain = _pick_captain(active_squad, sim_results, risk_profile, rider_map, intent=intent)
    current_result = _eval_team(
        tuple(sorted(active_squad)), current_captain, stage, riders, probs, n=n_sim, seed=42,
        scenario_priors=scenario_priors,
    )
    current_metric = _team_metric(current_result, risk_profile)

    for _iter in range(20):
        best_swap = None
        best_score = 0.0

        for sell_id in list(active_squad):
            sell_rider = rider_map.get(sell_id)
            if sell_rider is None:
                continue

            if risk_profile == RiskProfile.ANCHOR:
                if _is_gc_anchor(sell_rider) or sell_rider.jerseys:
                    continue

            after_sell_budget = remaining_budget + sell_rider.value
            team_counts_without = _count_teams(
                [sid for sid in active_squad if sid != sell_id], rider_map
            )

            for buy_id in candidates:
                if buy_id in active_squad:
                    continue
                buy_rider = rider_map.get(buy_id)
                if buy_rider is None:
                    continue
                if team_counts_without.get(buy_rider.team_abbr, 0) >= 2:
                    continue

                fee = _buy_fee(buy_rider.value)
                if after_sell_budget < buy_rider.value + fee:
                    continue

                proposed = tuple(sorted(
                    [buy_id if r == sell_id else r for r in active_squad]
                ))
                proposed_captain = _pick_captain(list(proposed), sim_results, risk_profile, rider_map, intent=intent)
                proposed_result = _eval_team(
                    proposed, proposed_captain, stage, riders, probs, n=n_sim, seed=42,
                    scenario_priors=scenario_priors,
                )
                gain = _team_metric(proposed_result, risk_profile) - current_metric

                score = _eval_swap(
                    profile=risk_profile,
                    gain=gain,
                    buy_ev=proposed_result.expected_value,
                    sell_ev=current_result.expected_value,
                    fee=fee,
                    stages_remaining=stages_remaining,
                    current_metric=current_metric,
                )
                if score is None or score <= best_score:
                    continue

                best_score = score
                best_swap = (sell_id, buy_id, fee)

        if best_swap is None:
            break

        sell_id, buy_id, fee = best_swap
        sell_rider = rider_map[sell_id]
        buy_rider = eligible[buy_id]

        remaining_budget += sell_rider.value
        remaining_budget -= buy_rider.value + fee
        active_squad.remove(sell_id)
        active_squad.append(buy_id)

        current_captain = _pick_captain(active_squad, sim_results, risk_profile, rider_map, intent=intent)
        current_result = _eval_team(
            tuple(sorted(active_squad)), current_captain, stage, riders, probs, n=n_sim, seed=42,
            scenario_priors=scenario_priors,
        )
        current_metric = _team_metric(current_result, risk_profile)

    # ── Step 5: double-swap exploration (A5) ─────────────────────────────────
    double_result = _try_double_swaps(
        active_squad=active_squad,
        candidates=candidates,
        current_metric=current_metric,
        current_result=current_result,
        profile=risk_profile,
        stage=stage,
        all_riders=riders,
        probs=probs,
        rider_map=rider_map,
        sim_results=sim_results,
        remaining_budget=remaining_budget,
        n=n_sim,
        scenario_priors=scenario_priors,
        intent=intent,
    )
    if double_result is not None:
        proposed_squad, proposed_captain, sell_pair, buy_pair = double_result
        for s, b in zip(sell_pair, buy_pair):
            sell_rider = rider_map[s]
            buy_rider  = rider_map[b]
            fee = _buy_fee(buy_rider.value)
            remaining_budget += sell_rider.value
            remaining_budget -= buy_rider.value + fee
        active_squad = proposed_squad
        current_captain = proposed_captain
        current_result = _eval_team(
            tuple(sorted(active_squad)), current_captain, stage, riders, probs, n=n_sim, seed=42,
            scenario_priors=scenario_priors,
        )

    # ── Fallback: last-resort fill if still < 8 ──────────────────────────────
    if len(active_squad) < 8:
        logging.warning(
            "optimizer: squad has only %d riders after fill; topping up with cheapest eligible (budget=%.0f)",
            len(active_squad), remaining_budget,
        )
        fb_team_counts = _count_teams(active_squad, rider_map)
        for buy_id, buy_rider in sorted(eligible.items(), key=lambda x: x[1].value):
            if len(active_squad) >= 8:
                break
            if buy_id in active_squad:
                continue
            if fb_team_counts.get(buy_rider.team_abbr, 0) >= 2:
                continue
            fee = _buy_fee(buy_rider.value)
            if remaining_budget < buy_rider.value + fee:
                continue
            active_squad.append(buy_id)
            remaining_budget -= buy_rider.value + fee
            fb_team_counts[buy_rider.team_abbr] = fb_team_counts.get(buy_rider.team_abbr, 0) + 1

    if len(active_squad) < 8:
        logging.warning("Emergency fill triggered — could not reach 8 riders normally")
        em_team_counts = _count_teams(active_squad, rider_map)
        remaining = sorted(
            [
                (rid, r) for rid, r in eligible.items()
                if rid not in active_squad
                and em_team_counts.get(r.team_abbr, 0) < 2
            ],
            key=lambda x: x[1].value,
        )
        for rid, r in remaining:
            if len(active_squad) >= 8:
                break
            active_squad.append(rid)
            em_team_counts[r.team_abbr] = em_team_counts.get(r.team_abbr, 0) + 1
        if len(active_squad) < 8:
            logging.warning(
                "optimizer: could only fill %d/8 slots — insufficient eligible riders",
                len(active_squad),
            )

    # ── Diff-based transfer reporting ────────────────────────────────────────
    # Compute what was sold/bought relative to input_squad (state after forced sells).
    # This prevents phantom sells of riders the user never owned (Stage 1 scenario).
    sold_ids = [rid for rid in input_squad if rid not in active_squad]
    bought_ids = [rid for rid in active_squad if rid not in input_squad]

    optimization_transfers: list = []
    for sid in sold_ids:
        r = rider_map[sid]
        optimization_transfers.append(TransferAction(
            action="sell",
            rider_id=sid,
            rider_name=r.name,
            value=r.value,
            fee=0,
            reasoning="Optimization swap",
        ))
    for bid in bought_ids:
        r = rider_map[bid]
        fee = _buy_fee(r.value)
        optimization_transfers.append(TransferAction(
            action="buy",
            rider_id=bid,
            rider_name=r.name,
            value=r.value,
            fee=fee,
            reasoning="Optimization swap",
        ))

    transfers = forced_sell_transfers + optimization_transfers

    # ── Step 6: captain + final team result (A7) ─────────────────────────────
    captain_id = _pick_captain(active_squad, sim_results, risk_profile, rider_map, intent=intent)

    # Final team result — n_sim sims with fixed seed for the accepted squad
    team_result = _eval_team(
        tuple(sorted(active_squad)), captain_id, stage, riders, probs, n=n_sim, seed=42,
        scenario_priors=scenario_priors,
    )

    # Legacy per-rider aggregate metrics (kept for backward compatibility)
    total_ev = sum(
        sim_results[rid].expected_value for rid in active_squad if rid in sim_results
    )
    total_p90 = sum(
        sim_results[rid].percentile_90 for rid in active_squad if rid in sim_results
    )
    total_p10 = sum(
        sim_results[rid].percentile_10 for rid in active_squad if rid in sim_results
    )
    total_cost = sum(t.fee for t in transfers if t.action == "buy")
    n_transfers = sum(1 for t in transfers if t.action == "buy")

    return ProfileRecommendation(
        profile=risk_profile,
        transfers=transfers,
        captain=captain_id,
        expected_value=total_ev,
        upside_90pct=total_p90,
        downside_10pct=total_p10,
        transfer_cost=total_cost,
        reasoning=_build_reasoning(risk_profile, active_squad, rider_map, n_transfers, stage, intent=intent),
        team_result=team_result,
    )


# ── optimize_all_profiles ─────────────────────────────────────────────────────

def optimize_all_profiles(
    riders: list,
    my_team: list,
    stage: Stage,
    probs: dict,
    sim_results: dict,
    bank: float,
    rank: Optional[int],
    total_participants: Optional[int],
    stages_remaining: int,
    n_sim: int = 500,
    scenario_priors: Optional[dict] = None,
    intent: Optional[StageIntent] = None,
    next_stage: Optional[Stage] = None,
    enable_lookahead: bool = False,
    probs_n1: Optional[dict] = None,
    intent_n1: Optional[StageIntent] = None,
) -> dict:
    """Run all 4 profiles in one pass. Returns dict[RiskProfile, ProfileRecommendation]."""
    return {
        profile: optimize(
            riders=riders,
            my_team=my_team,
            stage=stage,
            probs=probs,
            sim_results=sim_results,
            bank=bank,
            risk_profile=profile,
            rank=rank,
            total_participants=total_participants,
            stages_remaining=stages_remaining,
            n_sim=n_sim,
            scenario_priors=scenario_priors,
            intent=intent,
            next_stage=next_stage,
            enable_lookahead=enable_lookahead,
            probs_n1=probs_n1,
            intent_n1=intent_n1,
        )
        for profile in RiskProfile
    }


# ── suggest_profile ───────────────────────────────────────────────────────────

def suggest_profile(
    rank: int,
    total: int,
    stages_remaining: int,
    target_rank: int = 100,
) -> tuple:
    """
    Returns (RiskProfile, plain_english_reason).

    Logic:
      top 0.1%           → ANCHOR   "protect elite position"
      top 1%             → BALANCED "controlled hunting"
      stages_remaining<5 → ALL_IN   "running out of time"
      gap > stages*80k   → AGGRESSIVE "gap too large for safe play"
      else               → BALANCED "standard situation"
    """
    top_pct = rank / max(total, 1)
    gap = max(0, rank - target_rank)

    if top_pct < 0.001:
        return (
            RiskProfile.ANCHOR,
            (
                f"Top {top_pct * 100:.3f}% — elite position. "
                "Maximise floor, never give up GC riders."
            ),
        )
    if top_pct < 0.01:
        return (
            RiskProfile.BALANCED,
            (
                f"Top {top_pct * 100:.2f}% — controlled hunting. "
                "Take EV-positive transfers; avoid high-variance swings."
            ),
        )
    if stages_remaining < 5:
        return (
            RiskProfile.ALL_IN,
            (
                f"Only {stages_remaining} stage(s) left, rank {rank:,}. "
                "Running out of time — conviction bet is the only path."
            ),
        )
    if gap > stages_remaining * 80_000:
        return (
            RiskProfile.AGGRESSIVE,
            (
                f"Gap of {gap:,} to target with {stages_remaining} stage(s) left. "
                "Safe play cannot close this — push for ceiling outcomes."
            ),
        )
    return (
        RiskProfile.BALANCED,
        (
            f"Rank {rank:,}, gap {gap:,} to target, {stages_remaining} stage(s) left. "
            "Standard situation — maximise expected value."
        ),
    )


# ── Briefing table formatter ──────────────────────────────────────────────────

def format_briefing_table(
    recommendations: dict,
    rider_map: dict,
    stage: Stage,
) -> str:
    """
    Render the 4-profile side-by-side briefing table.

    recommendations: dict[RiskProfile, ProfileRecommendation]
    rider_map: dict[holdet_id, Rider]
    """
    profiles = [
        RiskProfile.ANCHOR,
        RiskProfile.BALANCED,
        RiskProfile.AGGRESSIVE,
        RiskProfile.ALL_IN,
    ]
    headers = ["ANCHOR", "BALANCED", "AGGRESSIVE", "ALL-IN"]

    def fmt_k(v: float) -> str:
        sign = "+" if v >= 0 else ""
        return f"{sign}{v / 1000:.0f}k"

    def captain_name(rec: ProfileRecommendation) -> str:
        r = rider_map.get(rec.captain)
        if r is None:
            return rec.captain[:10]
        parts = r.name.split()
        if len(parts) >= 2:
            return f"{parts[-1][:8]} {parts[0][0]}."
        return r.name[:10]

    def transfer_count(rec: ProfileRecommendation) -> int:
        return sum(1 for t in rec.transfers if t.action == "buy")

    col_w = 11
    label_w = 20

    lines = [
        f"Stage {stage.number} — {stage.stage_type.upper()} "
        f"({stage.distance_km:.0f} km)",
        "=" * (label_w + col_w * 4),
        f"{'':>{label_w}}" + "".join(f"{h:>{col_w}}" for h in headers),
        "-" * (label_w + col_w * 4),
    ]

    rows = [
        ("Captain:", lambda rec: captain_name(rec)),
        ("Expected value:", lambda rec: fmt_k(rec.expected_value)),
        ("Upside (90pct):", lambda rec: fmt_k(rec.upside_90pct)),
        ("Downside (10pct):", lambda rec: fmt_k(rec.downside_10pct)),
        ("Transfers needed:", lambda rec: str(transfer_count(rec))),
        ("Transfer cost:", lambda rec: fmt_k(-rec.transfer_cost)),
        (
            "Net EV after fees:",
            lambda rec: fmt_k(rec.expected_value - rec.transfer_cost),
        ),
    ]

    for label, fn in rows:
        values = [fn(recommendations[p]) for p in profiles]
        lines.append(f"{label:<{label_w}}" + "".join(f"{v:>{col_w}}" for v in values))

    lines.append("=" * (label_w + col_w * 4))
    return "\n".join(lines)
