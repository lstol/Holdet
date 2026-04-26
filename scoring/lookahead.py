"""
scoring/lookahead.py — Identity-aware lookahead EV projection.

This is a READ-ONLY analytical layer. It MUST NOT:
  - mutate any input (base_probs, profiles, adjustments, stages)
  - write back to state.json or riders.json
  - modify ROLE_TOP15, calibration outputs, or rider profiles
  - select captains
  - influence the optimizer's decision logic directly

LOOKAHEAD MUST NOT SELECT CAPTAINS.
Captain selection is handled in the daily optimizer only.
This module is strictly EV projection — it returns per-rider EVs
for analytical purposes only.
"""
from __future__ import annotations

import copy
import statistics
from dataclasses import dataclass
from typing import Optional

from scoring.engine import Rider, Stage
from scoring.probabilities import (
    RiderProb, apply_rider_adjustments, apply_rider_profiles, _rider_type,
)
from scoring.simulator import simulate_all_riders


# ── Output schema ─────────────────────────────────────────────────────────────

@dataclass
class LookaheadResult:
    rider_id: str
    ev_total: float
    ev_by_stage: list[float]    # one float per stage in horizon
    volatility: float           # std(ev_by_stage); 0.0 for horizon=1
    consistency_risk: float     # 1 / profile.consistency (1.0 if no profile)

    @property
    def stages_simulated(self) -> int:
        return len(self.ev_by_stage)

    @property
    def ev_per_stage(self) -> float:
        if not self.ev_by_stage:
            return 0.0
        return self.ev_total / len(self.ev_by_stage)


# ── Core simulation ───────────────────────────────────────────────────────────

def simulate_lookahead(
    riders: list[Rider],
    stages: list[Stage],
    base_probs: dict[str, RiderProb],
    profiles: dict,                          # dict[str, RiderProfile]
    adjustments_by_stage: dict[int, dict[str, float]],  # stage.number → {rider_id: mult}
    horizon: int = 3,
    n_sim: int = 200,
) -> dict[str, LookaheadResult]:
    """
    Project per-rider EV over a multi-stage horizon.

    Rider profiles are structural bias signals, not learned parameters.
    Adjustments are stage-specific and non-bleeding — each stage starts
    from a deepcopy of base_probs. Stage N adjustments are never present
    when stage N+1 is simulated.

    Returns dict[rider_id → LookaheadResult], sorted by ev_total descending.
    """
    horizon_stages = stages[:horizon]
    ev_accumulator: dict[str, list[float]] = {r.holdet_id: [] for r in riders}

    for stage in horizon_stages:
        # Step A: fresh deepcopy — adjustments never bleed across stages
        stage_probs = copy.deepcopy(base_probs)

        # Step B: apply stage-specific adjustments only
        stage_adj = dict(adjustments_by_stage.get(stage.number, {}))
        if stage_adj:
            stage_probs = apply_rider_adjustments(stage_probs, stage_adj)

        # Step C: apply structural rider profiles
        if profiles:
            role_map = {r.holdet_id: _rider_type(r, stage) for r in riders}
            stage_probs = apply_rider_profiles(stage_probs, profiles, role_map)

        # Step D: simulate all riders — captain EXCLUDED
        sim_results = simulate_all_riders(
            riders=riders,
            stage=stage,
            probs=stage_probs,
            my_team=[],
            captain="",
            n_simulations=n_sim,
            stages_remaining=1,
            seed=stage.number,  # deterministic per stage, independent across runs
        )

        # Step E: accumulate EV per rider
        for r in riders:
            rid = r.holdet_id
            ev = sim_results[rid].expected_value if rid in sim_results else 0.0
            ev_accumulator[rid].append(ev)

    # Build results
    results: dict[str, LookaheadResult] = {}
    for r in riders:
        rid = r.holdet_id
        evs = ev_accumulator[rid]
        ev_total = sum(evs)
        volatility = statistics.stdev(evs) if len(evs) > 1 else 0.0

        # consistency_risk = 1 / profile.consistency (1.0 if no profile)
        profile = profiles.get(rid)
        consistency_risk = 1.0 / profile.consistency if profile else 1.0

        results[rid] = LookaheadResult(
            rider_id=rid,
            ev_total=ev_total,
            ev_by_stage=evs,
            volatility=volatility,
            consistency_risk=consistency_risk,
        )

    return dict(
        sorted(results.items(), key=lambda kv: kv[1].ev_total, reverse=True)
    )


# ── Ranking helpers ───────────────────────────────────────────────────────────

def rank_by_ev(results: dict[str, LookaheadResult]) -> list[LookaheadResult]:
    """Return results sorted by ev_total descending."""
    return sorted(results.values(), key=lambda r: r.ev_total, reverse=True)


def rank_by_volatility(results: dict[str, LookaheadResult]) -> list[LookaheadResult]:
    """Return results sorted by volatility descending (most volatile first — lottery picks)."""
    return sorted(results.values(), key=lambda r: r.volatility, reverse=True)


def rank_by_stability(results: dict[str, LookaheadResult]) -> list[LookaheadResult]:
    """Return results sorted by volatility ascending (most stable first — ANCHOR picks)."""
    return sorted(results.values(), key=lambda r: r.volatility)


# ── CLI formatter ─────────────────────────────────────────────────────────────

def format_lookahead_table(
    results: dict[str, LookaheadResult],
    riders: list[Rider],
    horizon: int,
    n_sim: int,
    my_team: Optional[list[str]] = None,
    top: int = 20,
) -> str:
    """
    Render per-rider lookahead EV table.

    LOOKAHEAD EV PROJECTION  (horizon=3 stages, n=200 sims/stage)
    Rank  Rider                  Team      EV Total  EV/Stage  Volatility  Cons.Risk
    ────────────────────────────────────────────────────────────────────────────────
    """
    rider_map = {r.holdet_id: r for r in riders}
    ranked = rank_by_ev(results)[:top]
    ranked_ids = {lr.rider_id for lr in ranked}

    def _fmt(v: float) -> str:
        sign = "+" if v >= 0 else ""
        return f"{sign}{v:,.0f}"

    stage_word = "stage" if horizon == 1 else "stages"
    lines = [
        f"LOOKAHEAD EV PROJECTION  (horizon={horizon} {stage_word}, n={n_sim} sims/stage)",
        f"{'Rank':>4}  {'Rider':<22} {'Team':<8} {'EV Total':>10} {'EV/Stage':>10} "
        f"{'Volatility':>11} {'Cons.Risk':>10}",
        "─" * 80,
    ]

    for rank, lr in enumerate(ranked, 1):
        r = rider_map.get(lr.rider_id)
        name = (r.name if r else lr.rider_id)[:22]
        team = (r.team_abbr if r else "?")[:8]
        in_team = bool(my_team) and lr.rider_id in my_team
        marker = "* " if in_team else "  "
        lines.append(
            f"{rank:>4}{marker}{name:<22} {team:<8} "
            f"{_fmt(lr.ev_total):>10} {_fmt(lr.ev_per_stage):>10} "
            f"{_fmt(lr.volatility):>11} {lr.consistency_risk:>10.2f}"
        )

    # Team riders outside the top-N block
    if my_team:
        outside = [
            results[rid] for rid in my_team
            if rid in results and rid not in ranked_ids
        ]
        if outside:
            lines.append("")
            lines.append("Your team riders (outside top range):")
            for lr in sorted(outside, key=lambda x: x.ev_total, reverse=True):
                r = rider_map.get(lr.rider_id)
                name = (r.name if r else lr.rider_id)[:22]
                lines.append(f"  *  {name:<22}  EV {_fmt(lr.ev_total):>10}")

    return "\n".join(lines)
