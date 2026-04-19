"""
scoring/odds.py — Bookmaker odds → normalised implied probabilities.

Converts decimal odds to probability inputs for the interactive_adjust()
workflow, providing pre-filled starting values rather than flat model priors.
"""
from __future__ import annotations

from typing import Optional

from scoring.engine import Rider, Stage
from scoring.probabilities import RiderProb, _clamp, _find_rider


# ── Core conversion functions ─────────────────────────────────────────────────

def decimal_to_implied(odds: float) -> float:
    """Convert decimal odds to raw implied probability: 1 / odds."""
    return 1.0 / odds


def normalise(implied: dict[str, float]) -> dict[str, float]:
    """
    Remove bookmaker overround by dividing each implied prob by the sum.
    Returns same keys, normalised to sum to 1.0.
    """
    total = sum(implied.values())
    if total == 0.0:
        return dict(implied)
    return {k: v / total for k, v in implied.items()}


def odds_to_p_win(raw_odds: dict[str, float]) -> dict[str, float]:
    """
    Convert {rider_name_fragment: decimal_odds} → normalised {fragment: p_win}.
    """
    implied = {k: decimal_to_implied(v) for k, v in raw_odds.items()}
    return normalise(implied)


def h2h_to_prob(
    rider_a: str, odds_a: float,
    rider_b: str, odds_b: float,
) -> dict[str, float]:
    """
    Convert a head-to-head market into individual win probabilities.
    Returns {rider_a: prob_a, rider_b: prob_b}, normalised to sum to 1.0.
    """
    implied = {
        rider_a: decimal_to_implied(odds_a),
        rider_b: decimal_to_implied(odds_b),
    }
    return normalise(implied)


# ── Apply odds to existing probs dict ─────────────────────────────────────────

def apply_odds_to_probs(
    probs: dict[str, RiderProb],
    p_win_map: dict[str, float],
    riders_by_id: dict[str, Rider],
) -> dict[str, RiderProb]:
    """
    Apply a {rider_name_fragment: p_win} map onto an existing probs dict.

    For each matched rider:
    - Sets rp.p_win to the supplied value
    - Derives p_top3 = clamp(p_win / 0.35)
    - Derives p_top10 = clamp(p_top3 / 0.30)
    - Derives p_top15 = clamp(p_top10 / 0.65)
    - Sets source = "odds", model_confidence = 0.8
    - Records override in rp.manual_overrides for each field touched

    Riders not in p_win_map are unchanged.
    """
    for fragment, p_win in p_win_map.items():
        rid = _find_rider(fragment, probs, riders_by_id)
        if rid is None:
            continue

        rp = probs[rid]
        p_win_clamped  = _clamp(p_win)
        p_top3  = _clamp(p_win_clamped / 0.35)
        p_top10 = _clamp(p_top3 / 0.30)
        p_top15 = _clamp(p_top10 / 0.65)

        rp.p_win   = round(p_win_clamped, 4)
        rp.p_top3  = round(p_top3, 4)
        rp.p_top10 = round(p_top10, 4)
        rp.p_top15 = round(p_top15, 4)

        rp.source = "odds"
        rp.model_confidence = 0.8

        rp.manual_overrides["p_win"]   = rp.p_win
        rp.manual_overrides["p_top3"]  = rp.p_top3
        rp.manual_overrides["p_top10"] = rp.p_top10
        rp.manual_overrides["p_top15"] = rp.p_top15

    return probs


# ── Interactive CLI odds input ────────────────────────────────────────────────

def cli_odds_input(
    probs: dict[str, RiderProb],
    stage: Stage,
    riders: list[Rider],
    _input_fn=input,
) -> dict[str, RiderProb]:
    """
    Interactive CLI that collects bookmaker odds before handing off to
    interactive_adjust().

    Outright format:   <rider fragment> <decimal odds>
                       e.g.  milan 4.50
    H2H format:        h2h <rider_a fragment> <odds_a> vs <rider_b fragment> <odds_b>
                       e.g.  h2h milan 1.80 vs girmay 2.10
    Type 'done' to apply collected odds.
    Type 'skip' to use model priors unchanged.
    """
    riders_by_id = {r.holdet_id: r for r in riders}

    print()
    print(f"  STAGE {stage.number} — ODDS INPUT")
    print("  ─────────────────────────────────────────────────────────")
    print("  Enter outright win odds as: <rider fragment> <decimal odds>")
    print("  Enter H2H odds as:          h2h <rider_a fragment> <odds_a> vs <rider_b fragment> <odds_b>")
    print("  Type 'done' when finished. Type 'skip' to use model priors only.")
    print()

    outright: dict[str, float] = {}  # fragment → decimal odds
    h2h_pairs: list[tuple[str, float, str, float]] = []

    while True:
        try:
            raw = _input_fn("  odds> ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not raw:
            continue

        parts = raw.split()

        if parts[0].lower() == "skip":
            return probs

        if parts[0].lower() == "done":
            break

        # H2H: h2h <rider_a> <odds_a> vs <rider_b> <odds_b>
        if parts[0].lower() == "h2h":
            try:
                # Find 'vs' separator
                vs_idx = [p.lower() for p in parts].index("vs")
                # rider_a block: parts[1 .. vs_idx-1], odds_a: parts[vs_idx-1]
                # rider_b block: parts[vs_idx+1 .. -1], odds_b: parts[-1]
                odds_a = float(parts[vs_idx - 1])
                rider_a = " ".join(parts[1:vs_idx - 1])
                odds_b = float(parts[-1])
                rider_b = " ".join(parts[vs_idx + 1:-1])
                if not rider_a or not rider_b:
                    raise ValueError("empty rider fragment")
                h2h_pairs.append((rider_a, odds_a, rider_b, odds_b))
            except (ValueError, IndexError):
                print("  Invalid format. Try: h2h milan 1.80 vs girmay 2.10")
            continue

        # Outright: <rider fragment> <decimal odds>
        # Last token is the odds, everything before is the fragment
        if len(parts) < 2:
            print("  Invalid format. Try: milan 4.50")
            continue
        try:
            odds = float(parts[-1])
            fragment = " ".join(parts[:-1])
            outright[fragment] = odds
        except ValueError:
            print("  Invalid format. Try: milan 4.50")
            continue

    # Apply collected odds
    if outright:
        p_win_map = odds_to_p_win(outright)
        probs = apply_odds_to_probs(probs, p_win_map, riders_by_id)

    for rider_a, odds_a, rider_b, odds_b in h2h_pairs:
        pair_map = h2h_to_prob(rider_a, odds_a, rider_b, odds_b)
        # H2H gives relative probs — apply each independently as p_win overrides
        probs = apply_odds_to_probs(probs, pair_map, riders_by_id)

    return probs
