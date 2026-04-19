"""
scoring/probabilities.py — Probability layer for Holdet decision support.

Generates model priors per rider/stage, supports CLI manual adjustment,
and persists/loads probability snapshots via state.json.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Optional

from scoring.engine import Rider, Stage


# ── RiderProb dataclass ───────────────────────────────────────────────────────

@dataclass
class RiderProb:
    rider_id: str
    stage_number: int
    p_win: float
    p_top3: float
    p_top10: float
    p_top15: float
    p_dnf: float
    p_jersey_retain: dict = field(default_factory=dict)   # jersey_name → float
    expected_sprint_points: float = 0.0
    expected_kom_points: float = 0.0
    source: str = "model"
    model_confidence: float = 0.6
    manual_overrides: dict = field(default_factory=dict)  # field → value


# ── Base probability tables ───────────────────────────────────────────────────

BASE_TOP15: dict[str, dict[str, float]] = {
    "flat":     {"sprinter": 0.35, "climber": 0.05, "gc": 0.08, "tt": 0.10,
                 "specialist": 0.15, "domestique": 0.04, "all": 0.12},
    "hilly":    {"sprinter": 0.10, "climber": 0.18, "gc": 0.15, "tt": 0.10,
                 "specialist": 0.22, "domestique": 0.04, "all": 0.12},
    "mountain": {"sprinter": 0.02, "climber": 0.25, "gc": 0.45, "tt": 0.12,
                 "specialist": 0.12, "domestique": 0.03, "all": 0.08},
    "itt":      {"sprinter": 0.08, "climber": 0.08, "gc": 0.20, "tt": 0.40,
                 "specialist": 0.14, "domestique": 0.04, "all": 0.12},
    "ttt":      {"sprinter": 0.50, "climber": 0.50, "gc": 0.50, "tt": 0.50,
                 "specialist": 0.50, "domestique": 0.50, "all": 0.50},
}

# Jersey retention probability by stage type
JERSEY_RETAIN: dict[str, dict[str, float]] = {
    "flat":     {"yellow": 0.85, "green": 0.60, "polkadot": 0.80, "white": 0.80},
    "hilly":    {"yellow": 0.70, "green": 0.50, "polkadot": 0.65, "white": 0.70},
    "mountain": {"yellow": 0.40, "green": 0.35, "polkadot": 0.50, "white": 0.45},
    "itt":      {"yellow": 0.55, "green": 0.50, "polkadot": 0.75, "white": 0.55},
    "ttt":      {"yellow": 0.70, "green": 0.65, "polkadot": 0.75, "white": 0.70},
}


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def _rider_type(rider: Rider, stage: Stage) -> str:
    """
    Classify rider by value bracket and stage type.

    > 8M  → "gc" on mountain/hilly (GC contenders), "sprinter" on flat/itt
    > 5M  → "specialist" (punchers, breakaway riders)
    < 3M  → "domestique" (low-value support riders)
    else  → "all"
    """
    v = rider.value
    if v > 8_000_000:
        return "gc" if stage.stage_type in ("mountain", "hilly") else "sprinter"
    if v > 5_000_000:
        return "specialist"
    if v < 3_000_000:
        return "domestique"
    return "all"


# ── Core probability generation ───────────────────────────────────────────────

def generate_priors(
    riders: list[Rider],
    stage: Stage,
) -> dict[str, RiderProb]:
    """
    Generate model probability estimates from stage type + rider data.
    Returns dict of holdet_id → RiderProb with source="model".
    """
    stage_type = stage.stage_type
    has_sprint = bool(stage.sprint_points)
    has_kom = bool(stage.kom_points)
    base_table = BASE_TOP15.get(stage_type, BASE_TOP15["flat"])

    result: dict[str, RiderProb] = {}

    for rider in riders:
        rid = rider.holdet_id

        # DNS riders: certain abandonment, no other probability
        if rider.status == "dns":
            result[rid] = RiderProb(
                rider_id=rid,
                stage_number=stage.number,
                p_win=0.0,
                p_top3=0.0,
                p_top10=0.0,
                p_top15=0.0,
                p_dnf=1.0,
                p_jersey_retain={},
                expected_sprint_points=0.0,
                expected_kom_points=0.0,
                source="model",
                model_confidence=1.0,
            )
            continue

        rtype = _rider_type(rider, stage)
        p_top15 = _clamp(base_table.get(rtype, base_table.get("all", 0.12)))

        # Derive hierarchy using fixed ratios
        p_top10 = _clamp(p_top15 * 0.65)
        p_top3  = _clamp(p_top10 * 0.30)
        p_win   = _clamp(p_top3  * 0.35)

        # DNF base rate — higher for mountain/itt, lower for flat
        dnf_base = {"flat": 0.01, "hilly": 0.02, "mountain": 0.03,
                    "itt": 0.01, "ttt": 0.01}.get(stage_type, 0.02)
        p_dnf = _clamp(dnf_base)

        # Jersey retention
        jersey_table = JERSEY_RETAIN.get(stage_type, JERSEY_RETAIN["flat"])
        p_jersey_retain = {j: jersey_table.get(j, 0.5) for j in rider.jerseys}

        # Sprint / KOM expectations — zero unless stage has those points defined
        exp_sprint = 0.0
        exp_kom = 0.0
        if has_sprint:
            # Crude: sprinters and flat riders get a non-zero expectation
            if stage_type in ("flat", "hilly"):
                exp_sprint = round(p_top15 * 3.0, 2)
        if has_kom:
            if stage_type in ("mountain", "hilly"):
                exp_kom = round(p_top15 * 2.0, 2)

        result[rid] = RiderProb(
            rider_id=rid,
            stage_number=stage.number,
            p_win=round(p_win, 4),
            p_top3=round(p_top3, 4),
            p_top10=round(p_top10, 4),
            p_top15=round(p_top15, 4),
            p_dnf=round(p_dnf, 4),
            p_jersey_retain=p_jersey_retain,
            expected_sprint_points=exp_sprint,
            expected_kom_points=exp_kom,
            source="model",
            model_confidence=0.6,
        )

    return result


# ── CLI adjustment interface ──────────────────────────────────────────────────

_FIELD_MAP = {
    "win":    "p_win",
    "top3":   "p_top3",
    "top10":  "p_top10",
    "top15":  "p_top15",
    "dnf":    "p_dnf",
    "sprint": "expected_sprint_points",
    "kom":    "expected_kom_points",
}

_PCT_FIELDS = {"p_win", "p_top3", "p_top10", "p_top15", "p_dnf"}


def _format_prob(v: float, field_name: str) -> str:
    if field_name in _PCT_FIELDS:
        return f"{v*100:.0f}%"
    return f"{v:.1f}"


def _find_rider(fragment: str, probs: dict[str, RiderProb],
                riders_by_id: dict[str, Rider]) -> Optional[str]:
    """Return holdet_id of first rider whose name contains fragment (case-insensitive)."""
    frag = fragment.lower()
    for rid, rp in probs.items():
        rider = riders_by_id.get(rid)
        name = rider.name if rider else rid
        if frag in name.lower():
            return rid
    return None


def _display_table(probs: dict[str, RiderProb], stage: Stage,
                   riders_by_id: dict[str, Rider]) -> None:
    stage_label = stage.stage_type.capitalize()
    dist = f"{stage.distance_km:.0f}km"
    print()
    print("──────────────────────────────────────────────────────────")
    print(f"  STAGE {stage.number} — PROBABILITY REVIEW  [{stage_label}, {dist}]")
    print("──────────────────────────────────────────────────────────")
    print(f"  {'#':>2}  {'Rider':<22} {'Team':<8} {'Win%':>5} {'Top3':>5} {'Top15':>6} "
          f"{'DNF':>5} {'SpKOM':>6} {'Conf':>5}  Src")
    print("  " + "─" * 77)

    for i, (rid, rp) in enumerate(probs.items(), 1):
        rider = riders_by_id.get(rid)
        name = rider.name if rider else rid
        team = rider.team_abbr if rider else "?"
        # Truncate long names
        name_disp = name[:22]
        src = rp.source[:3]
        if rp.source == "adjusted":
            src = "adj*"
        sp_kom = rp.expected_sprint_points + rp.expected_kom_points
        print(
            f"  {i:>2}  {name_disp:<22} {team:<8} "
            f"{rp.p_win*100:>4.0f}%  {rp.p_top3*100:>4.0f}%  {rp.p_top15*100:>5.0f}%  "
            f"{rp.p_dnf*100:>4.0f}%  {sp_kom:>5.1f}  {rp.model_confidence:>4.1f}   {src}"
        )
    print()


def _show_rider(rid: str, rp: RiderProb, rider: Optional[Rider]) -> None:
    name = rider.name if rider else rid
    print(f"\n  {name} — full probability detail:")
    print(f"    p_win:   {rp.p_win:.4f}")
    print(f"    p_top3:  {rp.p_top3:.4f}")
    print(f"    p_top10: {rp.p_top10:.4f}")
    print(f"    p_top15: {rp.p_top15:.4f}")
    print(f"    p_dnf:   {rp.p_dnf:.4f}")
    print(f"    jersey:  {rp.p_jersey_retain}")
    print(f"    sprint:  {rp.expected_sprint_points}")
    print(f"    kom:     {rp.expected_kom_points}")
    print(f"    source:  {rp.source}  confidence: {rp.model_confidence}")
    if rp.manual_overrides:
        print(f"    overrides: {rp.manual_overrides}")
    print()


def interactive_adjust(
    probs: dict[str, RiderProb],
    stage: Stage,
    riders: Optional[list[Rider]] = None,
    _input_fn=input,  # injectable for testing
) -> dict[str, RiderProb]:
    """
    CLI interface: display prob table, accept manual adjustments,
    return updated probs with source="adjusted" and audit trail.
    """
    riders_by_id: dict[str, Rider] = {}
    if riders:
        for r in riders:
            riders_by_id[r.holdet_id] = r

    # Keep original priors for reset
    import copy
    original_probs = copy.deepcopy(probs)
    current_probs = copy.deepcopy(probs)

    _display_table(current_probs, stage, riders_by_id)

    print("  Commands:")
    print("    <rider fragment> <field> <value>   e.g. \"milan win 35\" or \"ving dnf 5\"")
    print("    done                               accept all and save")
    print("    show <rider fragment>              show full prob detail for one rider")
    print("    reset <rider fragment>             reset to model priors")
    print()

    while True:
        try:
            raw = _input_fn("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not raw:
            continue

        parts = raw.split()

        if parts[0].lower() == "done":
            break

        if parts[0].lower() == "show" and len(parts) >= 2:
            frag = " ".join(parts[1:])
            rid = _find_rider(frag, current_probs, riders_by_id)
            if rid is None:
                print(f"  No rider found matching '{frag}'")
            else:
                _show_rider(rid, current_probs[rid], riders_by_id.get(rid))
            continue

        if parts[0].lower() == "reset" and len(parts) >= 2:
            frag = " ".join(parts[1:])
            rid = _find_rider(frag, current_probs, riders_by_id)
            if rid is None:
                print(f"  No rider found matching '{frag}'")
            else:
                current_probs[rid] = copy.deepcopy(original_probs[rid])
                print(f"  Reset to model priors.")
            continue

        # <fragment> <field> <value>
        if len(parts) < 3:
            print("  Usage: <rider fragment> <field> <value>  or  done")
            continue

        # Rider fragment may be multiple words before the field keyword
        # Try to parse: last two tokens are field+value, rest is rider fragment
        field_key = None
        value_str = None
        rider_frag = None
        for i in range(len(parts) - 2, 0, -1):
            candidate_field = parts[i].lower()
            if candidate_field in _FIELD_MAP:
                field_key = candidate_field
                value_str = parts[i + 1]
                rider_frag = " ".join(parts[:i])
                break

        if field_key is None:
            print(f"  Unknown field. Valid fields: {', '.join(_FIELD_MAP)}")
            continue

        rid = _find_rider(rider_frag, current_probs, riders_by_id)
        if rid is None:
            print(f"  No rider found matching '{rider_frag}'")
            continue

        try:
            raw_value = float(value_str)
        except ValueError:
            print(f"  Invalid value: {value_str}")
            continue

        attr = _FIELD_MAP[field_key]
        if attr in _PCT_FIELDS:
            new_value = _clamp(raw_value / 100.0)
        else:
            new_value = max(0.0, raw_value)

        rp = current_probs[rid]
        old_value = getattr(rp, attr)
        setattr(rp, attr, round(new_value, 4))
        rp.manual_overrides[attr] = round(new_value, 4)
        rp.source = "adjusted"

        rider = riders_by_id.get(rid)
        name = rider.name if rider else rid
        print(f"  Updated {name}: {field_key} {old_value*100 if attr in _PCT_FIELDS else old_value:.1f}"
              f" → {new_value*100 if attr in _PCT_FIELDS else new_value:.1f}"
              + ("%" if attr in _PCT_FIELDS else ""))

    return current_probs


# ── Persistence ───────────────────────────────────────────────────────────────

def save_probs(
    probs: dict[str, RiderProb],
    stage_number: int,
    state_path: str,
) -> None:
    """Save probs to state.json under prob_history['stage_N']."""
    state: dict = {}
    if os.path.exists(state_path) and os.path.getsize(state_path) > 0:
        with open(state_path, "r") as f:
            state = json.load(f)

    state.setdefault("prob_history", {})
    key = f"stage_{stage_number}"
    state["prob_history"][key] = {
        rid: asdict(rp) for rid, rp in probs.items()
    }

    with open(state_path, "w") as f:
        json.dump(state, f, indent=2)


def load_probs(
    stage_number: int,
    state_path: str,
) -> Optional[dict[str, RiderProb]]:
    """Returns None if stage not yet in state."""
    if not os.path.exists(state_path):
        return None

    with open(state_path, "r") as f:
        state = json.load(f)

    key = f"stage_{stage_number}"
    raw = state.get("prob_history", {}).get(key)
    if raw is None:
        return None

    result: dict[str, RiderProb] = {}
    for rid, d in raw.items():
        result[rid] = RiderProb(**d)
    return result
