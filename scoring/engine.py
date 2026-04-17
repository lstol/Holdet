"""
scoring/engine.py — Pure scoring engine for Holdet fantasy cycling.

All scoring logic references rule numbers from RULES.md.
Pure function: no I/O, no side effects, no global state.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


# ── Scoring tables ────────────────────────────────────────────────────────────

# RULES.md 2.1 — Stage finish position values (non-TTT only)
STAGE_POSITION_TABLE: dict[int, int] = {
    1: 200_000, 2: 150_000, 3: 130_000, 4: 120_000, 5: 110_000,
    6: 100_000, 7: 95_000,  8: 90_000,  9: 85_000,  10: 80_000,
    11: 70_000, 12: 55_000, 13: 40_000, 14: 30_000, 15: 15_000,
}

# RULES.md 2.2 — GC standing values (every stage, including TTT)
GC_STANDING_TABLE: dict[int, int] = {
    1: 100_000, 2: 90_000, 3: 80_000, 4: 70_000, 5: 60_000,
    6: 50_000,  7: 40_000, 8: 30_000, 9: 20_000, 10: 10_000,
}

# RULES.md 2.3 — Jersey bonus values (to holder at END of stage, not entrant)
JERSEY_VALUES: dict[str, int] = {
    "yellow":   25_000,
    "green":    25_000,
    "polkadot": 25_000,
    "white":    15_000,
}
# RULES.md 2.3 — Most Aggressive (red number), awarded once per stage
MOST_AGGRESSIVE_VALUE = 50_000

# RULES.md 3.1 — Team bonus per top-3 finish position
TEAM_BONUS_TABLE: dict[int, int] = {1: 60_000, 2: 30_000, 3: 20_000}

# RULES.md 3.3 — Stage depth bonus (Etapebonus) by number of my riders in top 15
ETAPEBONUS_TABLE: dict[int, int] = {
    0: 0,      1: 4_000,   2: 8_000,   3: 15_000, 4: 35_000,
    5: 65_000, 6: 120_000, 7: 220_000, 8: 400_000,
}

# RULES.md 4 — TTT placement value per rider (to all active riders from that team)
TTT_PLACEMENT_TABLE: dict[int, int] = {
    1: 200_000, 2: 150_000, 3: 100_000, 4: 50_000, 5: 25_000,
}


# ── Data schemas ──────────────────────────────────────────────────────────────

@dataclass
class Rider:
    holdet_id: str
    person_id: str
    team_id: str
    name: str
    team: str           # full name e.g. "Team Visma | Lease a Bike"
    team_abbr: str
    value: int
    start_value: int
    points: int
    status: str         # "active" | "dns" | "dnf" | "disqualified"
    gc_position: Optional[int]
    jerseys: list        # jerseys held entering the stage (informational only)
    in_my_team: bool
    is_captain: bool


@dataclass
class SprintPoint:
    location: str
    km_from_start: float
    points_available: list
    is_finish: bool


@dataclass
class KOMPoint:
    location: str
    km_from_start: float
    category: str        # "HC" | "1" | "2" | "3" | "4"
    points_available: list


@dataclass
class Stage:
    number: int
    race: str            # "giro_2026" | "tdf_2026"
    stage_type: str      # "flat" | "hilly" | "mountain" | "itt" | "ttt"
    distance_km: float
    is_ttt: bool
    start_location: str
    finish_location: str
    sprint_points: list = field(default_factory=list)
    kom_points: list = field(default_factory=list)
    notes: str = ""


@dataclass
class StageResult:
    stage_number: int
    finish_order: list                   # holdet_ids in finish order (top 15+)
    times_behind_winner: dict            # holdet_id → seconds behind winner
    sprint_point_winners: dict           # holdet_id → [pts at each sprint]
    kom_point_winners: dict              # holdet_id → [pts at each KOM]
    jersey_winners: dict                 # "yellow"|"green"|"polkadot"|"white" → holdet_id
    most_aggressive: Optional[str]       # holdet_id
    dnf_riders: list                     # holdet_ids
    dns_riders: list                     # holdet_ids
    disqualified: list                   # holdet_ids
    ttt_team_order: Optional[list]       # team names in placement order (TTT only)
    gc_standings: list                   # holdet_ids in GC order after stage


@dataclass
class ValueDelta:
    rider_id: str
    # ── Rider value components (change rider's market value) ──
    stage_position_value: int    # RULES.md 2.1
    gc_standing_value: int       # RULES.md 2.2
    jersey_bonus: int            # RULES.md 2.3
    sprint_kom_value: int        # RULES.md 2.4
    late_arrival_penalty: int    # RULES.md 2.5
    dnf_penalty: int             # RULES.md 2.6
    dns_penalty: int             # RULES.md 2.6
    team_bonus: int              # RULES.md 3.1
    ttt_value: int               # RULES.md 4
    total_rider_value_delta: int
    # ── Bank components (go to bank, not rider value) ──
    captain_bank_deposit: int    # RULES.md 3.2 — mirrors positive rider growth
    etapebonus_bank_deposit: int # RULES.md 3.3 — stage depth bonus; team-level,
                                 #   same value returned for every rider; callers
                                 #   must sum it only ONCE per stage.
    total_bank_delta: int


# ── Helper functions ──────────────────────────────────────────────────────────

def _get_position(item: str, ordered_list: list) -> Optional[int]:
    """Return 1-based position in list, or None if not found."""
    try:
        return ordered_list.index(item) + 1
    except ValueError:
        return None


def late_arrival_penalty(seconds_late: int) -> int:
    """
    RULES.md 2.5 — Late arrival: truncated minutes × −3,000, cap −90,000.

    Truncation example: 4 min 54 sec = 294 sec = 4 full minutes = −12,000
    (NOT −15,000 — floor division, never round).
    Cap: 30 minutes = −90,000 maximum.
    """
    if seconds_late <= 0:
        return 0
    # RULES.md 2.5 — truncate, not round
    minutes_late = seconds_late // 60
    return max(-90_000, minutes_late * -3_000)


# ── Main scoring function ─────────────────────────────────────────────────────

def score_rider(
    rider: Rider,
    stage: Stage,
    result: StageResult,
    my_team: list,          # all 8 holdet_ids currently on team
    captain: str,           # holdet_id of designated captain
    stages_remaining: int,  # stages left including this one, for DNS calc
    all_riders: Optional[dict] = None,  # holdet_id → Rider, needed for team bonus
) -> ValueDelta:
    """
    Pure scoring function. Returns complete value breakdown for one rider in one stage.

    Parameters
    ----------
    all_riders : dict[str, Rider] | None
        Full rider lookup needed to determine the real-world team of top-3
        finishers (RULES.md 3.1 team bonus). If None, team_bonus is always 0.

    Notes
    -----
    etapebonus_bank_deposit is a team-level calculation: the same value is
    returned in every call for riders on the same team / stage. Callers must
    accumulate it only ONCE per stage when updating the bank balance.
    """
    rid = rider.holdet_id

    # ── Status flags ──────────────────────────────────────────────────────────
    is_dnf = rid in result.dnf_riders
    is_dns = rid in result.dns_riders
    is_dq  = rid in result.disqualified
    did_finish = not is_dnf and not is_dns and not is_dq

    # ── RULES.md 2.1 — Stage finish position (non-TTT, finished riders only) ─
    stage_position_value = 0
    if not stage.is_ttt and did_finish:
        pos = _get_position(rid, result.finish_order)
        if pos is not None:
            stage_position_value = STAGE_POSITION_TABLE.get(pos, 0)

    # ── RULES.md 2.2 — GC standing (every stage including TTT) ───────────────
    # DNS riders are deactivated and have no GC position
    gc_standing_value = 0
    if not is_dns:
        gc_pos = _get_position(rid, result.gc_standings)
        if gc_pos is not None:
            gc_standing_value = GC_STANDING_TABLE.get(gc_pos, 0)

    # ── RULES.md 2.3 — Jersey bonuses ─────────────────────────────────────────
    # CRITICAL: bonus goes to rider who HOLDS jersey at END of stage.
    # A rider who wears yellow all day but loses it at the finish gets ZERO.
    jersey_bonus = 0
    for jersey, holder_id in result.jersey_winners.items():
        if holder_id == rid:
            jersey_bonus += JERSEY_VALUES.get(jersey, 0)
    # Most Aggressive is a separate field (not in jersey_winners dict)
    if result.most_aggressive == rid:
        jersey_bonus += MOST_AGGRESSIVE_VALUE

    # ── RULES.md 2.4 — Sprint & KOM points (+3,000 per point, always ≥ 0) ────
    # DNF riders still earn sprint/KOM points accumulated before abandonment
    sprint_pts = sum(result.sprint_point_winners.get(rid, []))
    kom_pts    = sum(result.kom_point_winners.get(rid, []))
    sprint_kom_value = (sprint_pts + kom_pts) * 3_000

    # ── RULES.md 2.5 — Late arrival penalty (non-TTT, finished riders only) ──
    # Truncated minutes × −3,000, cap −90,000. Does NOT apply on TTT.
    lap = 0
    if not stage.is_ttt and did_finish:
        seconds_late = result.times_behind_winner.get(rid, 0)
        lap = late_arrival_penalty(seconds_late)
    _late_arrival_penalty = lap

    # ── RULES.md 2.6 — DNF / Disqualification penalty (−50,000 once) ─────────
    # DNF: still earns sprint/KOM from before abandonment, no team bonus
    # Disqualified: treated same as DNF
    dnf_penalty = 0
    if is_dnf or is_dq:
        dnf_penalty = -50_000

    # ── RULES.md 2.6 — DNS penalty (−100,000 × stages_remaining) ─────────────
    # ACTION RULE: sell any DNS rider immediately — costs every remaining stage
    dns_penalty = 0
    if is_dns:
        dns_penalty = -100_000 * stages_remaining

    # ── RULES.md 3.1 — Team bonus / Holdbonus (non-TTT, active riders only) ──
    # Triggered when any real-world team rider finishes 1st/2nd/3rd.
    # ALL your active riders from that team receive the bonus.
    # DNF riders on that stage do NOT receive the team bonus.
    team_bonus = 0
    if not stage.is_ttt and did_finish and all_riders is not None:
        for pos in (1, 2, 3):
            if pos <= len(result.finish_order):
                finisher_id = result.finish_order[pos - 1]
                if finisher_id == rid:
                    # Rider's own finish is already counted in stage_position_value
                    continue
                finisher = all_riders.get(finisher_id)
                if finisher is not None and finisher.team == rider.team:
                    team_bonus += TEAM_BONUS_TABLE[pos]

    # ── RULES.md 4 — TTT value (TTT stages, active riders only) ──────────────
    # Awarded to ALL active riders from that real-world team based on placement.
    # 6th place and below = 0. No late arrival penalty on TTT.
    ttt_value = 0
    if stage.is_ttt and did_finish and result.ttt_team_order:
        ttt_pos = _get_position(rider.team, result.ttt_team_order)
        if ttt_pos is not None:
            ttt_value = TTT_PLACEMENT_TABLE.get(ttt_pos, 0)

    # ── Total rider value delta ───────────────────────────────────────────────
    total_rider_value_delta = (
        stage_position_value
        + gc_standing_value
        + jersey_bonus
        + sprint_kom_value
        + _late_arrival_penalty
        + dnf_penalty
        + dns_penalty
        + team_bonus
        + ttt_value
    )

    # ── RULES.md 3.2 — Captain bank deposit ──────────────────────────────────
    # Positive value growth → same amount deposited to bank.
    # Negative days: NOT amplified — captain_bank_deposit is never negative.
    captain_bank_deposit = 0
    if rid == captain:
        captain_bank_deposit = max(0, total_rider_value_delta)

    # ── RULES.md 3.3 — Stage depth bonus / Etapebonus (non-TTT, bank only) ──
    # Count how many of my 8 riders finish in top 15. Nonlinear bank deposit.
    # NOTE: this team-level value is identical across all score_rider calls for
    # the same stage. Callers must credit the bank ONCE, not once per rider.
    etapebonus_bank_deposit = 0
    if not stage.is_ttt:
        top_15 = set(result.finish_order[:15])
        count = sum(1 for team_rid in my_team if team_rid in top_15)
        etapebonus_bank_deposit = ETAPEBONUS_TABLE.get(count, 0)

    total_bank_delta = captain_bank_deposit + etapebonus_bank_deposit

    return ValueDelta(
        rider_id=rid,
        stage_position_value=stage_position_value,
        gc_standing_value=gc_standing_value,
        jersey_bonus=jersey_bonus,
        sprint_kom_value=sprint_kom_value,
        late_arrival_penalty=_late_arrival_penalty,
        dnf_penalty=dnf_penalty,
        dns_penalty=dns_penalty,
        team_bonus=team_bonus,
        ttt_value=ttt_value,
        total_rider_value_delta=total_rider_value_delta,
        captain_bank_deposit=captain_bank_deposit,
        etapebonus_bank_deposit=etapebonus_bank_deposit,
        total_bank_delta=total_bank_delta,
    )
