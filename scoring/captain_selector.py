"""
Captain selection — runs AFTER optimize(), never inside it.

Formula: score = EV + λ * p_win
  EV    = sim_results[rider_id].expected_value  (from optimizer per-rider sims)
  p_win = probs[rider_id].p_win                 (from final shaped probabilities)
  λ     = risk multiplier by mode

The optimizer's sim_results already reflect the fully-shaped probability distribution.
p_win is taken from probs (same shaped probs passed to optimize()) — not re-derived.
"""
from __future__ import annotations


LAMBDA: dict[str, float] = {
    "stable":     0.0,   # highest EV only — GC-style consistency
    "balanced":   0.5,   # mix of EV and stage-win upside
    "aggressive": 1.5,   # favour likely stage winners
}

PROFILE_VARIANCE_DEFAULT: dict[str, str] = {
    "anchor":     "stable",
    "balanced":   "balanced",
    "aggressive": "aggressive",
}


def select_captain(
    team: list[str],
    probs: dict,        # rider_id → RiderProb (final shaped probs)
    sim_results: dict,  # rider_id → SimResult (from optimizer)
    mode: str = "balanced",
) -> tuple[str, list[dict]]:
    """
    Returns (captain_id, candidates).

    candidates: top-5 riders by score, always returned regardless of mode.
    Each entry: {"rider_id": str, "ev": float, "p_win": float, "score": float}
    """
    lam = LAMBDA.get(mode, 0.5)
    eligible = [rid for rid in team if rid in sim_results and rid in probs]

    scored = []
    for rid in eligible:
        ev    = sim_results[rid].expected_value
        p_win = probs[rid].p_win
        score = ev + lam * p_win
        scored.append({"rider_id": rid, "ev": ev, "p_win": p_win, "score": score})

    scored.sort(key=lambda x: x["score"], reverse=True)
    captain_id = scored[0]["rider_id"] if scored else (team[0] if team else "")

    return captain_id, scored[:5]
