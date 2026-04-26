"""
scoring/decision_trace.py — Decision traceability layer (Session 22.5).

Provides:
  - DecisionTrace: per-rider ablation record (marginal effects, NOT additive)
  - ablation_run(): re-run simulate_all_riders with one shaping component toggled off
  - build_decision_traces(): compute all DecisionTraces for a brief call
  - build_contributors(): structured contributor breakdown for the /brief response
  - validate_contributor_label(): label validator (raises on invalid input)

Design invariant:
  Each adjustment field is a MARGINAL effect under isolated ablation.
  The fields do NOT sum to final_ev. Non-linear layer interactions mean
  additivity is mathematically false. Do NOT assert base_ev + sum(adjustments) == final_ev.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Optional

from scoring.engine import Stage
from scoring.probability_shaper import ProbabilityContext, apply_probability_shaping
from scoring.simulator import simulate_all_riders


# ── Allowed label values ──────────────────────────────────────────────────────

ALLOWED_EFFECT_ENUMS: set[str] = {"team_bonus", "transfer_gain", "role_penalty"}


# ── DecisionTrace ─────────────────────────────────────────────────────────────

@dataclass
class DecisionTrace:
    rider_id: str
    base_ev: float                # EV from raw priors (no shaping applied)
    probability_adjustment: float # EV_full - EV_no_prob_shaping (all layers off)
    variance_adjustment: float    # EV_full - EV_no_variance (Layer 3.5 off)
    intent_adjustment: float      # ALWAYS 0.0 — reserved for Session 23
    lookahead_adjustment: float   # 0.0 when enable_lookahead=False
    final_ev: float               # EV_full (same as optimizer input)


# ── Label validation ──────────────────────────────────────────────────────────

def validate_contributor_label(
    label: str,
    rider_names: set[str],
    scenario_keys: set[str],
) -> None:
    """Raise ValueError if label is not a known rider name, effect enum, or scenario key."""
    if label in rider_names:
        return
    if label in ALLOWED_EFFECT_ENUMS:
        return
    if label in scenario_keys:
        return
    raise ValueError(
        f"Invalid contributor label '{label}'. Must be a rider name, "
        f"one of {ALLOWED_EFFECT_ENUMS}, or a scenario key from scenario_priors."
    )


# ── Ablation runner ───────────────────────────────────────────────────────────

def ablation_run(
    riders: list,
    stage: Stage,
    raw_probs: dict,
    ctx: ProbabilityContext,
    component_flags: dict[str, bool],
    seed: int = 42,
) -> dict[str, float]:
    """
    Re-run simulate_all_riders() with one shaping component toggled off.
    Returns {rider_id: expected_value} for all riders.

    Does NOT re-run optimize() — ablation is at simulation level only.
    Captain logic is excluded entirely: my_team=[], captain="".

    component_flags keys (set True to disable that component):
      "variance"      → set variance_mode="balanced" (Layer 3.5 no-op)
      "intelligence"  → set intelligence_signals=None (Layer 3 off)
      "prob_shaping"  → skip apply_probability_shaping() entirely (raw priors)
      "lookahead"     → no-op in 22.5 (lookahead_adjustment is always 0.0)

    All context modifications use dataclasses.replace() — original ctx is never mutated.
    """
    skip_shaping = component_flags.get("prob_shaping", False)

    if skip_shaping:
        probs = raw_probs
    else:
        ctx_mod = ctx
        if component_flags.get("variance", False):
            ctx_mod = dataclasses.replace(ctx_mod, variance_mode="balanced")
        if component_flags.get("intelligence", False):
            ctx_mod = dataclasses.replace(ctx_mod, intelligence_signals=None)
        probs, _ = apply_probability_shaping(raw_probs, ctx_mod)

    results = simulate_all_riders(
        riders=riders,
        stage=stage,
        probs=probs,
        my_team=[],
        captain="",
        seed=seed,
    )
    return {rid: sr.expected_value for rid, sr in results.items()}


# ── Build all DecisionTraces ──────────────────────────────────────────────────

def build_decision_traces(
    riders: list,
    stage: Stage,
    raw_probs: dict,
    ctx: ProbabilityContext,
    ev_full: dict[str, float],
    seed: int = 42,
) -> dict[str, DecisionTrace]:
    """
    Compute DecisionTrace for all riders in ev_full. Returns {rider_id: DecisionTrace}.

    ev_full: {rider_id: expected_value} from fully-shaped simulate_all_riders call.
    Each adjustment is a marginal effect — ablations are independent, not composable.
    """
    # base_ev: raw priors, no shaping at all
    ev_base = ablation_run(riders, stage, raw_probs, ctx, {"prob_shaping": True}, seed=seed)
    # ev_no_variance: variance layer disabled (all other layers active)
    ev_no_variance = ablation_run(riders, stage, raw_probs, ctx, {"variance": True}, seed=seed)

    traces: dict[str, DecisionTrace] = {}
    for rid, final_ev in ev_full.items():
        traces[rid] = DecisionTrace(
            rider_id=rid,
            base_ev=ev_base.get(rid, 0.0),
            probability_adjustment=final_ev - ev_base.get(rid, 0.0),
            variance_adjustment=final_ev - ev_no_variance.get(rid, 0.0),
            intent_adjustment=0.0,       # reserved — Session 23 wires apply_intent_to_ev()
            lookahead_adjustment=0.0,    # 0.0 when enable_lookahead=False (Session 22.5 default)
            final_ev=final_ev,
        )
    return traces


# ── Contributor breakdown ─────────────────────────────────────────────────────

def build_contributors(
    my_team: list[str],
    sim_results: dict,
    rider_names: dict[str, str],
    scenario_stats: Optional[dict],
    scenario_priors: Optional[dict],
) -> dict:
    """
    Build contributor breakdown for the /brief response.

    rider_names: {rider_id: display_name}
    scenario_stats: from BALANCED profile's team_result (realized frequencies)
    scenario_priors: from request — if None, scenario_contributions is omitted entirely

    All labels are validated via validate_contributor_label() before inclusion.
    share is clip(ev, min=0) / sum(clipped_evs). Shares sum to 1.0 within 1e-6.
    """
    rider_name_set: set[str] = set(rider_names.values())
    scenario_keys: set[str] = set(scenario_priors.keys()) if scenario_priors else set()

    # ── Rider contributors: top 3 in squad by EV (clipped) ───────────────────
    squad_evs = [
        (rid, max(0.0, sim_results[rid].expected_value))
        for rid in my_team
        if rid in sim_results
    ]
    total_ev = sum(ev for _, ev in squad_evs)
    rider_contributors = []
    if total_ev > 0:
        squad_evs.sort(key=lambda x: x[1], reverse=True)
        for rid, ev in squad_evs[:3]:
            name = rider_names.get(rid, rid)
            validate_contributor_label(name, rider_name_set, scenario_keys)
            rider_contributors.append({
                "label": name,
                "share": round(ev / total_ev, 6),
            })
        # Re-normalise top-3 shares to 1.0
        top3_total = sum(c["share"] for c in rider_contributors)
        if top3_total > 0:
            for c in rider_contributors:
                c["share"] = round(c["share"] / top3_total, 6)

    result: dict = {"rider_contributors": rider_contributors}

    # ── Scenario contributions: only when scenario_priors is non-null ─────────
    if scenario_priors is not None and scenario_stats:
        scenario_total = sum(max(0.0, v) for v in scenario_stats.values())
        if scenario_total > 0:
            scenario_contributions = []
            for key, freq in sorted(scenario_stats.items(), key=lambda x: x[1], reverse=True):
                validate_contributor_label(key, rider_name_set, scenario_keys)
                scenario_contributions.append({
                    "label": key,
                    "share": round(max(0.0, freq) / scenario_total, 6),
                })
            # Normalise to exactly 1.0
            sc_total = sum(c["share"] for c in scenario_contributions)
            if sc_total > 0:
                for c in scenario_contributions:
                    c["share"] = round(c["share"] / sc_total, 6)
            result["scenario_contributions"] = scenario_contributions

    return result
