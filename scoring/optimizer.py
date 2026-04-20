"""
scoring/optimizer.py — Transfer and captain recommendations across all 4 risk profiles.

Profiles are defined by SQUAD COMPOSITION OBJECTIVE, not transfer count.
Transfer count is an output of the optimizer, never an input constraint.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from scoring.engine import Rider, Stage
from scoring.probabilities import RiderProb
from scoring.simulator import SimResult


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


# ── Helpers ────────────────────────────────────────────────────────────────────

def _profile_metric(sim: SimResult, profile: RiskProfile) -> float:
    """Primary optimisation metric for this profile."""
    if profile == RiskProfile.ANCHOR:
        return sim.percentile_10
    elif profile == RiskProfile.BALANCED:
        return sim.expected_value
    elif profile == RiskProfile.AGGRESSIVE:
        return sim.percentile_80
    else:  # ALL_IN
        return sim.percentile_95


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


def _eval_swap(
    profile: RiskProfile,
    gain: float,
    buy_ev: float,
    sell_ev: float,
    fee: int,
    stages_remaining: int,
) -> Optional[float]:
    """
    Evaluate whether a swap is acceptable under the profile's transfer logic.
    Returns a score (higher = better) if acceptable, else None.
    """
    if profile == RiskProfile.ANCHOR:
        # Only transfer if replacement strictly improves p10 net of fee amortised
        fee_per_stage = fee / max(stages_remaining, 1)
        effective_gain = gain - fee_per_stage
        if effective_gain <= 0:
            return None
        return effective_gain

    elif profile == RiskProfile.BALANCED:
        # Transfer if EV gain exceeds fee / stages_remaining
        ev_gain = buy_ev - sell_ev
        threshold = fee / max(stages_remaining, 1)
        if ev_gain <= threshold:
            return None
        return gain  # rank by primary metric gain

    elif profile == RiskProfile.AGGRESSIVE:
        # Accept if primary metric (p80) improves; allow up to -30k EV if p80 gain >= 80k
        if gain <= 0:
            return None
        ev_change = buy_ev - sell_ev
        if ev_change < -30_000 and gain < 80_000:
            return None
        return gain

    else:  # ALL_IN
        # Optimise purely for p95; fee payback is secondary
        if gain <= 0:
            return None
        return gain


def _pick_captain(
    squad_ids: list,
    sim_results: dict,
    profile: RiskProfile,
    rider_map: dict,
) -> str:
    """Select captain per profile rules."""
    eligible_ids = [rid for rid in squad_ids if rid in sim_results]
    if not eligible_ids:
        return squad_ids[0] if squad_ids else ""

    if profile == RiskProfile.ANCHOR:
        # Highest EV rider
        return max(eligible_ids, key=lambda rid: sim_results[rid].expected_value)

    elif profile == RiskProfile.BALANCED:
        # Best EV/std_dev ratio (Sharpe-like)
        def sharpe(rid: str) -> float:
            s = sim_results[rid]
            if s.std_dev <= 0:
                return s.expected_value
            return s.expected_value / s.std_dev
        return max(eligible_ids, key=sharpe)

    elif profile == RiskProfile.AGGRESSIVE:
        # Highest p90
        return max(eligible_ids, key=lambda rid: sim_results[rid].percentile_90)

    else:  # ALL_IN
        # Highest p95
        return max(eligible_ids, key=lambda rid: sim_results[rid].percentile_95)


def _build_reasoning(
    profile: RiskProfile,
    squad_ids: list,
    rider_map: dict,
    n_transfers: int,
    stage: Stage,
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
        return descriptions[profile].format(n_transfers)
    elif profile == RiskProfile.BALANCED:
        return descriptions[profile].format(n_transfers)
    elif profile == RiskProfile.AGGRESSIVE:
        return descriptions[profile].format(stage.stage_type, n_transfers)
    else:
        return descriptions[profile].format(stage.stage_type, n_transfers)


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
) -> ProfileRecommendation:
    """
    Find the optimal squad for the given risk profile.

    Algorithm:
      1. Forced sells: remove DNS/DNF riders from squad, collect credits.
      2. Fill: pad squad to 8 with best-metric eligible riders if needed.
      3. Greedy swaps: iteratively find the best single swap that improves the
         profile metric and passes the profile's transfer acceptance logic.
      4. Captain: select per profile rules.

    Constraints (always enforced):
      - Exactly 8 riders in squad
      - Max 2 riders from same team_abbr
      - Total spend net of sell credits ≤ bank
      - No DNS/DNF riders bought
      - Captain is one of the 8 active riders
    """
    rider_map: dict = {r.holdet_id: r for r in riders}

    # Eligible riders: active status + pre-computed sim result available
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

    # ── Step 2: fill squad to 8 ──────────────────────────────────────────────
    if len(active_squad) < 8:
        team_counts = _count_teams(active_squad, rider_map)
        already_in: set = set(active_squad)

        def _fill_from(candidates: list) -> None:
            nonlocal remaining_budget
            for buy_id, buy_rider in candidates:
                if len(active_squad) >= 8:
                    break
                if buy_id in already_in:
                    continue
                if team_counts.get(buy_rider.team_abbr, 0) >= 2:
                    continue
                fee = _buy_fee(buy_rider.value)
                if remaining_budget < buy_rider.value + fee:
                    continue
                active_squad.append(buy_id)
                already_in.add(buy_id)
                remaining_budget -= buy_rider.value + fee
                team_counts[buy_rider.team_abbr] = team_counts.get(buy_rider.team_abbr, 0) + 1
                transfers.append(TransferAction(
                    action="buy",
                    rider_id=buy_id,
                    rider_name=buy_rider.name,
                    value=buy_rider.value,
                    fee=fee,
                    reasoning="Fill squad slot",
                ))

        # Pass 1: best profile metric first (may skip expensive riders when budget is tight)
        _fill_from(sorted(
            [(rid, r) for rid, r in eligible.items() if rid not in already_in],
            key=lambda x: _profile_metric(sim_results[x[0]], risk_profile),
            reverse=True,
        ))

        # Pass 2: if still short (budget exhausted expensive riders), fill with cheapest
        if len(active_squad) < 8:
            _fill_from(sorted(
                [(rid, r) for rid, r in eligible.items() if rid not in already_in],
                key=lambda x: x[1].value,
            ))

    # ── Step 3: greedy swap optimisation ────────────────────────────────────
    for _iter in range(20):  # at most 20 swaps per optimisation pass
        best_swap = None
        best_score = 0.0  # must improve strictly above 0 to trigger

        for sell_id in list(active_squad):
            sell_rider = rider_map.get(sell_id)
            if sell_rider is None:
                continue

            # ANCHOR: never sell GC top-10 or jersey holders
            if risk_profile == RiskProfile.ANCHOR:
                if _is_gc_anchor(sell_rider) or sell_rider.jerseys:
                    continue

            sell_metric = (
                _profile_metric(sim_results[sell_id], risk_profile)
                if sell_id in sim_results else 0.0
            )
            sell_ev = sim_results[sell_id].expected_value if sell_id in sim_results else 0.0

            after_sell_budget = remaining_budget + sell_rider.value
            team_counts_without = _count_teams(
                [sid for sid in active_squad if sid != sell_id], rider_map
            )

            for buy_id, buy_rider in eligible.items():
                if buy_id in active_squad:
                    continue
                if team_counts_without.get(buy_rider.team_abbr, 0) >= 2:
                    continue

                fee = _buy_fee(buy_rider.value)
                if after_sell_budget < buy_rider.value + fee:
                    continue

                buy_metric = _profile_metric(sim_results[buy_id], risk_profile)
                buy_ev = sim_results[buy_id].expected_value
                gain = buy_metric - sell_metric

                score = _eval_swap(
                    profile=risk_profile,
                    gain=gain,
                    buy_ev=buy_ev,
                    sell_ev=sell_ev,
                    fee=fee,
                    stages_remaining=stages_remaining,
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

        transfers.extend([
            TransferAction(
                action="sell",
                rider_id=sell_id,
                rider_name=sell_rider.name,
                value=sell_rider.value,
                fee=0,
                reasoning=f"Sold to improve {risk_profile.value} metric",
            ),
            TransferAction(
                action="buy",
                rider_id=buy_id,
                rider_name=buy_rider.name,
                value=buy_rider.value,
                fee=fee,
                reasoning=f"{risk_profile.value} metric gain {best_score:,.0f}",
            ),
        ])

    # ── Fallback: last-resort fill if still < 8 after all steps ─────────────
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
            transfers.append(TransferAction(
                action="buy",
                rider_id=buy_id,
                rider_name=buy_rider.name,
                value=buy_rider.value,
                fee=fee,
                reasoning="Last-resort fill: cheapest eligible rider",
            ))
        if len(active_squad) < 8:
            logging.warning(
                "optimizer: could only fill %d/8 slots — insufficient budget or eligible riders",
                len(active_squad),
            )

    # ── Step 4: captain + aggregate metrics ──────────────────────────────────
    captain_id = _pick_captain(active_squad, sim_results, risk_profile, rider_map)

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
        reasoning=_build_reasoning(risk_profile, active_squad, rider_map, n_transfers, stage),
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
        # "Vingegaard J." style
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
