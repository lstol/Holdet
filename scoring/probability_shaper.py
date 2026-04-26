"""
scoring/probability_shaper.py — Unified probability shaping layer.

DESIGN INVARIANT (Session 21+):
  All probability intelligence lives here. The optimizer is a pure EV function
  that consumes shaped probabilities — it never produces them.

Layer order is strict:
  1. Stage-role compatibility multipliers  ← Carapaz fix
  2. Rider profiles (consistency, bias)
  3. Intelligence signals (rider-level overrides)
  4. Odds / market blending              (optional)
  5. User expertise overrides            (stub — Session 23)
  6. Normalization + clamp               (enforced after every layer)
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Optional

from scoring.engine import Stage
from scoring.probabilities import RiderProb, _clamp


# ── Stage-role compatibility multipliers ─────────────────────────────────────

STAGE_ROLE_MULTIPLIER: dict[str, dict[str, float]] = {
    #                   flat    hilly  mountain   itt     ttt
    "sprinter":    {"flat": 1.25, "hilly": 1.05, "mountain": 0.60, "itt": 0.70, "ttt": 0.80},
    "climber":     {"flat": 0.65, "hilly": 0.85, "mountain": 1.30, "itt": 0.80, "ttt": 0.80},
    "gc_contender":{"flat": 0.85, "hilly": 1.00, "mountain": 1.15, "itt": 1.10, "ttt": 1.10},
    "breakaway":   {"flat": 1.00, "hilly": 1.10, "mountain": 1.05, "itt": 0.70, "ttt": 0.70},
    "tt":          {"flat": 0.90, "hilly": 0.90, "mountain": 0.75, "itt": 1.40, "ttt": 1.40},
    "domestique":  {"flat": 1.00, "hilly": 1.00, "mountain": 1.00, "itt": 1.00, "ttt": 1.00},
}


# ── ProbabilityContext ────────────────────────────────────────────────────────

@dataclass
class ProbabilityContext:
    stage: Stage
    rider_profiles: dict                   # holdet_id → RiderProfile
    rider_roles: dict                      # holdet_id → RiderRole string
    rider_adjustments: dict                # holdet_id → multiplier float
    odds_signal: Optional[dict]            # holdet_id → p_win from market
    intelligence_signals: Optional[dict]   # e.g. {"crosswind_risk": "high"} or {holdet_id: {field: val}}
    user_expertise_weights: Optional[dict] = field(default=None)  # stub — Session 23


# ── Normalization helper ──────────────────────────────────────────────────────

def _normalize_rp(rp: RiderProb) -> None:
    """Clamp to [0,1] and re-enforce ordering invariant in-place."""
    rp.p_win   = _clamp(rp.p_win)
    rp.p_top3  = _clamp(rp.p_top3)
    rp.p_top10 = _clamp(rp.p_top10)
    rp.p_top15 = _clamp(rp.p_top15)
    # Ordering invariant: p_win ≤ p_top3 ≤ p_top10 ≤ p_top15
    rp.p_top3  = max(rp.p_win,   rp.p_top3)
    rp.p_top10 = max(rp.p_top3,  rp.p_top10)
    rp.p_top15 = max(rp.p_top10, rp.p_top15)


def _add_source(rp: RiderProb, tag: str) -> None:
    sources = set(rp.source.split("+"))
    sources.add(tag)
    rp.source = "+".join(sorted(sources))


# ── Unified shaping pipeline ──────────────────────────────────────────────────

def apply_probability_shaping(
    probs: dict[str, RiderProb],
    ctx: ProbabilityContext,
) -> tuple[dict[str, RiderProb], dict]:
    """
    Deterministic layered pipeline. Returns (new_probs, trace) — never mutates input.
    Layer order is strict and must not be reordered.

    trace keys: model, role, profile, intelligence, odds, user
    """
    result: dict[str, RiderProb] = copy.deepcopy(probs)
    stage_type = ctx.stage.stage_type

    # ── Layer 1: Stage-role compatibility ─────────────────────────────────────
    for rid, rp in result.items():
        role = ctx.rider_roles.get(rid, "domestique")
        mult_table = STAGE_ROLE_MULTIPLIER.get(role, STAGE_ROLE_MULTIPLIER["domestique"])
        mult = mult_table.get(stage_type, 1.0)
        rp.p_win   *= mult
        rp.p_top3  *= mult
        rp.p_top10 *= mult
        rp.p_top15 *= mult
        _normalize_rp(rp)

    # ── Layer 2: Rider profiles (consistency + role bias) ─────────────────────
    from scoring.probabilities import RiderRole
    profile_hit = 0
    for rid, rp in result.items():
        profile = ctx.rider_profiles.get(rid)
        if not profile:
            continue
        role = ctx.rider_roles.get(rid, "")
        if role == RiderRole.SPRINTER:
            rp.p_win *= profile.sprint_bias
        elif role == RiderRole.CLIMBER:
            rp.p_win *= profile.climb_bias
        elif role == RiderRole.GC_CONTENDER:
            rp.p_win *= profile.gc_bias
        rp.p_win   *= profile.consistency
        rp.p_top3  *= profile.consistency
        rp.p_top10 *= profile.consistency
        rp.p_top15 *= profile.consistency
        _normalize_rp(rp)
        _add_source(rp, "profile")
        profile_hit += 1

    # ── Layer 3: Intelligence signals (rider-level overrides) ─────────────────
    intel_hit = 0
    if ctx.intelligence_signals:
        for rid, rp in result.items():
            override = ctx.intelligence_signals.get(rid)
            if isinstance(override, dict):
                for fname in ("p_win", "p_top3", "p_top10", "p_top15"):
                    if fname in override:
                        setattr(rp, fname, _clamp(override[fname]))
                _normalize_rp(rp)
                _add_source(rp, "intelligence")
                intel_hit += 1

    # ── Layer 4: Rider confidence adjustments (expert multipliers) ────────────
    # NOTE: rider_adjustments from ctx replace apply_rider_adjustments() calls.
    from scoring.probabilities import MAX_RIDER_ADJUSTMENT
    adj_hit = 0
    for rid, raw_mult in ctx.rider_adjustments.items():
        rp = result.get(rid)
        if rp is None:
            continue
        mult = max(-MAX_RIDER_ADJUSTMENT, min(MAX_RIDER_ADJUSTMENT, raw_mult))
        for fname in ("p_win", "p_top3", "p_top10", "p_top15"):
            base = getattr(rp, fname)
            rp.manual_overrides[f"rca_{fname}"] = base
            setattr(rp, fname, _clamp(base * (1 + mult)))
        _normalize_rp(rp)
        _add_source(rp, "user")
        adj_hit += 1

    # ── Layer 5: Odds / market blending ──────────────────────────────────────
    odds_hit = 0
    if ctx.odds_signal:
        from scoring.odds import apply_odds_to_probs
        pre_sources = {rid: rp.source for rid, rp in result.items()}
        result = apply_odds_to_probs(result, ctx.odds_signal, {})
        for rid, rp in result.items():
            _normalize_rp(rp)
            if rp.source != pre_sources.get(rid, rp.source):
                odds_hit += 1

    # ── Layer 6 (stub): User expertise weights — Session 23 ───────────────────
    # ctx.user_expertise_weights reserved; no-op.

    # ── Final normalization pass ──────────────────────────────────────────────
    for rp in result.values():
        _normalize_rp(rp)

    trace = {
        "model": len(probs),
        "role": len(probs),   # all riders receive a role multiplier (even if 1.0)
        "profile": profile_hit,
        "intelligence": intel_hit,
        "odds": odds_hit,
        "user": adj_hit,
    }

    return result, trace
