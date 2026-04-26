"""
api/server.py — Local FastAPI bridge.

The Next.js frontend calls this server (running on your laptop) to trigger
Python CLI actions without touching a terminal.

Run with:
    bash scripts/start_api.sh
    — or —
    python3.14 -m uvicorn api.server:app --host 127.0.0.1 --port 8000 --reload

Only runs locally. Never deployed to the cloud (Giro constraint).
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Optional

# Ensure project root is importable regardless of cwd
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import config
from ingestion.api import get_session, fetch_riders, fetch_my_team, save_riders, load_riders
from scoring.engine import (
    Rider, Stage, StageResult, SprintPoint, KOMPoint, score_rider,
)
from scoring.probabilities import generate_priors, save_probs, _rider_roles, _rider_type
from scoring.probability_shaper import ProbabilityContext, apply_probability_shaping
from scoring.simulator import simulate_all_riders, STAGE_SCENARIOS, _resolve_scenarios, simulate_team
from scoring.optimizer import optimize_all_profiles, suggest_profile, RiskProfile
from scoring.captain_selector import select_captain
from scoring.stage_intent import StageIntent, compute_stage_intent, apply_intelligence_signals, INTENT_FIELDS
from output.tracker import record_stage_accuracy, save_accuracy


# ── State helpers (mirror main.py) ────────────────────────────────────────────

def _load_state(path: str | None = None) -> dict:
    path = path or config.get_state_path()
    defaults: dict = {
        "current_stage": 1,
        "bank": 50_000_000,
        "my_team": [],
        "captain": None,
        "stages_completed": [],
        "rank": None,
        "total_participants": None,
    }
    if not os.path.exists(path):
        return defaults
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        for k, v in defaults.items():
            data.setdefault(k, v)
        return data
    except (json.JSONDecodeError, OSError):
        return defaults


def _save_state(state: dict, path: str | None = None) -> None:
    path = path or config.get_state_path()
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _load_stage(stage_number: int) -> Stage:
    stages_path = config.get_stages_path()
    if not os.path.exists(stages_path):
        raise HTTPException(status_code=404, detail=f"stages.json not found at {stages_path}")
    with open(stages_path, encoding="utf-8") as fh:
        stages_data = json.load(fh)

    stages_list: list = (
        stages_data if isinstance(stages_data, list)
        else stages_data.get("stages", list(stages_data.values()) if isinstance(stages_data, dict) else [])
    )
    for s in stages_list:
        if not isinstance(s, dict) or s.get("number") != stage_number:
            continue
        sprint_points = [
            SprintPoint(
                location=sp.get("location", ""),
                km_from_start=float(sp.get("km_from_start", 0)),
                points_available=sp.get("points_available", []),
                is_finish=sp.get("is_finish", False),
            )
            for sp in s.get("sprint_points", [])
        ]
        kom_points = [
            KOMPoint(
                location=kp.get("location", ""),
                km_from_start=float(kp.get("km_from_start", 0)),
                category=kp.get("category", "4"),
                points_available=kp.get("points_available", []),
            )
            for kp in s.get("kom_points", [])
        ]
        return Stage(
            number=s["number"],
            race=s.get("race", "giro_2026"),
            stage_type=s.get("stage_type", "flat"),
            distance_km=float(s.get("distance_km", 0)),
            is_ttt=s.get("is_ttt", False),
            start_location=s.get("start_location", ""),
            finish_location=s.get("finish_location", ""),
            sprint_points=sprint_points,
            kom_points=kom_points,
            notes=s.get("notes", ""),
        )
    raise HTTPException(status_code=404, detail=f"Stage {stage_number} not found")


def _load_profiles() -> dict:
    path = os.path.join("data", "rider_profiles.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _resolve_profiles(profiles_raw: dict, riders: list) -> dict:
    """Convert raw JSON dict to {holdet_id: RiderProfile}."""
    from scoring.rider_profiles import RiderProfile
    result: dict = {}
    for rider in riders:
        rid = rider.holdet_id
        raw = profiles_raw.get(rid) or profiles_raw.get(rider.name, {})
        if not raw:
            continue
        p = RiderProfile(
            rider_id=rid,
            sprint_bias=raw.get("sprint_bias", 1.0),
            gc_bias=raw.get("gc_bias", 1.0),
            climb_bias=raw.get("climb_bias", 1.0),
            consistency=raw.get("consistency", 1.0),
        )
        p.clamp()
        result[rid] = p
    return result


def _resolve_name(fragment: str, rider_map: dict[str, Rider]) -> str:
    fragment = fragment.strip()
    if fragment in rider_map:
        return fragment
    frag_lower = fragment.lower()
    matches = [rid for rid, r in rider_map.items() if frag_lower in r.name.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) == 0:
        raise HTTPException(status_code=422, detail=f"No rider matching '{fragment}'")
    names = ", ".join(rider_map[rid].name for rid in matches[:5])
    raise HTTPException(status_code=422, detail=f"Ambiguous '{fragment}': {names}")


def _resolve_list(fragments: list[str], rider_map: dict[str, Rider]) -> list[str]:
    return [_resolve_name(f, rider_map) for f in fragments if f.strip()]


def _serialize_profiles(profiles: dict, rider_map: dict[str, Rider]) -> dict:
    """Convert RiskProfile → ProfileRecommendation mapping to JSON-safe dict."""
    out: dict = {}
    for profile, rec in profiles.items():
        key = profile.value if hasattr(profile, "value") else str(profile)
        transfers = []
        for t in rec.transfers:
            td = asdict(t) if hasattr(t, "__dataclass_fields__") else dict(t)
            # Annotate with rider name if not already present
            if "rider_name" not in td and "rider_id" in td:
                r = rider_map.get(td["rider_id"])
                td["rider_name"] = r.name if r else td["rider_id"]
            transfers.append(td)
        captain_name = rider_map.get(rec.captain, Rider.__new__(Rider)).name if rec.captain in rider_map else rec.captain
        team_ev          = round(rec.team_result.expected_value)    if rec.team_result else None
        team_p10         = round(rec.team_result.percentile_10)     if rec.team_result else None
        team_p80         = round(rec.team_result.percentile_80)     if rec.team_result else None
        team_p95         = round(rec.team_result.percentile_95)     if rec.team_result else None
        etapebonus_ev    = round(rec.team_result.etapebonus_ev)     if rec.team_result else None
        etapebonus_p95   = round(rec.team_result.etapebonus_p95)    if rec.team_result else None
        out[key] = {
            "transfers": transfers,
            "captain": rec.captain,
            "captain_name": captain_name,
            "expected_value": rec.expected_value,
            "upside_90pct": rec.upside_90pct,
            "downside_10pct": rec.downside_10pct,
            "transfer_cost": rec.transfer_cost,
            "reasoning": rec.reasoning,
            "team_ev": team_ev,
            "team_p10": team_p10,
            "team_p80": team_p80,
            "team_p95": team_p95,
            "etapebonus_ev": etapebonus_ev,
            "etapebonus_p95": etapebonus_p95,
        }
    return out


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Holdet API Bridge", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "https://holdet.syndikatet.eu",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / Response models ─────────────────────────────────────────────────

class BriefRequest(BaseModel):
    stage: int
    look_ahead: int = 5       # used as stages_remaining for optimizer
    captain_override: Optional[str] = None  # holdet_id
    scenario_priors: Optional[dict] = None  # partial override of stage-type scenario weights
    intelligence_signals: Optional[dict] = None   # {"crosswind_risk": "high", ...}
    intelligence_reason: Optional[str] = None
    next_stage_type: Optional[str] = None         # reserved for Session 20
    variance_mode: str = "balanced"               # "stable" | "balanced" | "aggressive"


class TeamRequest(BaseModel):
    my_team: list[str]   # holdet_ids (exactly 8)
    captain: str         # holdet_id


class SettleRequest(BaseModel):
    stage: int
    finish_order: list[str]              # holdet_ids, best first (top 15+)
    dnf_riders: list[str] = []
    dns_riders: list[str] = []
    gc_standings: list[str] = []        # holdet_ids, leader first
    jersey_winners: dict[str, str] = {} # "yellow" → holdet_id
    most_aggressive: Optional[str] = None
    sprint_point_winners: dict[str, int] = {}  # holdet_id → total pts
    kom_point_winners: dict[str, int] = {}
    times_behind_winner: dict[str, int] = {}   # holdet_id → seconds
    ttt_team_order: Optional[list[str]] = None
    holdet_bank: Optional[float] = None  # actual bank for validation


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/status")
def get_status() -> dict:
    """Return current team, bank, rank, and DNS alerts."""
    state = _load_state()
    riders_path = config.get_riders_path()
    riders = load_riders(riders_path) if os.path.exists(riders_path) else []
    rider_map = {r.holdet_id: r for r in riders}

    my_team_ids: list[str] = state.get("my_team", [])
    team_riders = []
    dns_alerts = []
    for rid in my_team_ids:
        r = rider_map.get(rid)
        if r:
            team_riders.append({
                "holdet_id": r.holdet_id,
                "name": r.name,
                "team": r.team,
                "team_abbr": r.team_abbr,
                "value": r.value,
                "status": r.status,
                "is_captain": rid == state.get("captain"),
            })
            if r.status in ("dns", "dnf"):
                dns_alerts.append({"name": r.name, "status": r.status})

    return {
        "current_stage": state.get("current_stage"),
        "bank": state.get("bank"),
        "rank": state.get("rank"),
        "total_participants": state.get("total_participants"),
        "captain": state.get("captain"),
        "team": team_riders,
        "stages_completed": state.get("stages_completed", []),
        "dns_alerts": dns_alerts,
    }


@app.post("/ingest")
def post_ingest() -> dict:
    """Fetch latest riders + team from Holdet API. Updates state.json and riders.json."""
    try:
        session = get_session()
        game_id = config.get_game_id()
        fantasy_team_id = config.get_fantasy_team_id()
        cartridge = config.get_cartridge()
    except (EnvironmentError, PermissionError) as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    try:
        riders = fetch_riders(game_id, session=session)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Holdet API error: {exc}")

    riders_path = config.get_riders_path()
    save_riders(riders, riders_path)

    state = _load_state()
    rider_map = {r.holdet_id: r for r in riders}
    dns_alerts: list[dict] = []
    my_team_ids: list[str] = []

    try:
        team_data = fetch_my_team(fantasy_team_id, cartridge, session=session)
        lineup = team_data.get("lineup", [])
        for player in lineup:
            pid = str(player.get("id", ""))
            if pid in rider_map:
                my_team_ids.append(pid)
        state["my_team"] = my_team_ids
        state["captain"] = str(team_data.get("captain", "")) or None
        state["bank"] = team_data.get("bank", state["bank"])
    except PermissionError:
        pass  # Cookie expired — riders saved, team not updated

    for rid in my_team_ids:
        r = rider_map.get(rid)
        if r and r.status in ("dns", "dnf"):
            dns_alerts.append({"name": r.name, "status": r.status})

    _save_state(state)

    # Auto-sync to Supabase (best-effort)
    try:
        from scripts.sync_to_supabase import sync_all
        sync_all(race="giro_2026")
    except Exception:
        pass

    return {
        "riders_count": len(riders),
        "my_team_count": len(my_team_ids),
        "bank": state.get("bank"),
        "dns_alerts": dns_alerts,
    }


@app.post("/brief")
def post_brief(req: BriefRequest) -> dict:
    """
    Run the full briefing pipeline (model priors → simulator → optimizer).
    Returns 4-profile recommendation table + probability data.
    No interactive prompts — model probs only (no odds/manual adjustment).
    """
    riders_path = config.get_riders_path()
    if not os.path.exists(riders_path):
        raise HTTPException(status_code=404, detail="No riders.json — run /ingest first")

    riders = load_riders(riders_path)
    stage = _load_stage(req.stage)
    state = _load_state()

    my_team: list[str] = state.get("my_team", [])
    captain = req.captain_override or state.get("captain") or (my_team[0] if my_team else "")
    bank: float = state.get("bank", config.INITIAL_BUDGET)
    rank = state.get("rank")
    total = state.get("total_participants")
    stages_remaining = min(req.look_ahead, config.TOTAL_STAGES - req.stage + 1)

    rider_map = {r.holdet_id: r for r in riders}

    # Model probs — raw priors before shaping
    raw_probs = generate_priors(riders, stage)

    # Compute stage intent
    gc_positions_raw = state.get("gc_standings", {})
    if isinstance(gc_positions_raw, list):
        gc_positions = {rid: idx + 1 for idx, rid in enumerate(gc_positions_raw)}
    else:
        gc_positions = gc_positions_raw if isinstance(gc_positions_raw, dict) else {}
    intent = compute_stage_intent(stage, gc_positions, next_stage=None, riders=riders)
    if req.intelligence_signals:
        if not req.intelligence_reason:
            raise HTTPException(status_code=400, detail="intelligence_reason required when signals provided")
        intent = apply_intelligence_signals(intent, req.intelligence_signals)

    # Build shaping context — single source of truth for roles
    profiles_raw = _load_profiles()
    profiles = _resolve_profiles(profiles_raw, riders)
    role_map = {r.holdet_id: _rider_type(r, stage) for r in riders}
    adjustments = state.get("rider_adjustments", {}).get(str(req.stage), {})

    ctx = ProbabilityContext(
        stage=stage,
        rider_profiles=profiles,
        rider_roles=role_map,
        rider_adjustments=adjustments,
        odds_signal=None,
        intelligence_signals=req.intelligence_signals,
        user_expertise_weights=None,
        variance_mode=req.variance_mode or "balanced",
    )
    probs, prob_shaping_trace = apply_probability_shaping(raw_probs, ctx)

    # Simulate full field for optimizer
    all_sims = simulate_all_riders(
        riders=riders,
        stage=stage,
        probs=probs,
        my_team=my_team,
        captain=captain,
        stages_remaining=stages_remaining,
    )

    # Current team EV
    current_team_ev = sum(
        all_sims[rid].expected_value for rid in my_team if rid in all_sims
    )

    # Resolve scenario weights (normalized; used both for optimization and API response)
    resolved_scenarios = _resolve_scenarios(stage, req.scenario_priors)

    # Optimize all 4 profiles
    recommendations = optimize_all_profiles(
        riders=riders,
        my_team=my_team,
        stage=stage,
        probs=probs,
        sim_results=all_sims,
        bank=bank,
        rank=rank,
        total_participants=total,
        stages_remaining=stages_remaining,
        scenario_priors=req.scenario_priors,
        intent=intent,
    )

    # Captain selection (runs after optimizer — uses shaped probs + per-rider sim results)
    variance_mode = req.variance_mode or "balanced"
    captain_id, captain_candidates = select_captain(
        team=my_team,
        probs=probs,
        sim_results=all_sims,
        mode=variance_mode,
    )

    # Suggested profile
    suggested_profile = None
    suggested_reason = "No rank data — defaulting to BALANCED"
    if rank and total:
        sp, suggested_reason = suggest_profile(rank, total, stages_remaining)
        suggested_profile = sp.value if sp else None

    # Save probs to state
    probs_dict = {
        rid: {
            "p_win": rp.p_win, "p_top3": rp.p_top3,
            "p_top10": rp.p_top10, "p_top15": rp.p_top15,
            "p_dnf": rp.p_dnf, "source": rp.source,
        }
        for rid, rp in probs.items()
    }
    state.setdefault("probs_by_stage", {})[str(req.stage)] = probs_dict
    _save_state(state)

    # DNS alerts for team
    dns_alerts = [
        {"name": rider_map[rid].name, "status": rider_map[rid].status}
        for rid in my_team
        if rid in rider_map and rider_map[rid].status in ("dns", "dnf")
    ]

    # Team sim summary with full percentile set + roles (C1, B2)
    team_sims = [
        {
            "holdet_id": rid,
            "name": rider_map[rid].name if rid in rider_map else rid,
            "team_abbr": rider_map[rid].team_abbr if rid in rider_map else "",
            "roles": _rider_roles(rider_map[rid], stage, probs) if rid in rider_map else [],
            "expected_value":  round(all_sims[rid].expected_value),
            "percentile_10":   round(all_sims[rid].percentile_10),
            "percentile_50":   round(all_sims[rid].percentile_50),
            "percentile_80":   round(all_sims[rid].percentile_80),
            "percentile_90":   round(all_sims[rid].percentile_90),
            "percentile_95":   round(all_sims[rid].percentile_95),
            "p_positive":      round(all_sims[rid].p_positive, 3),
            "is_captain":      rid == captain,
            # Legacy compat
            "downside_10pct":  round(all_sims[rid].percentile_10),
            "upside_90pct":    round(all_sims[rid].percentile_90),
        }
        for rid in my_team if rid in all_sims
    ]

    # Extract scenario_stats from BALANCED profile's team_result (realized frequencies)
    balanced_result = recommendations.get(RiskProfile.BALANCED)
    realized_scenario_stats: dict = {}
    if balanced_result and balanced_result.team_result:
        realized_scenario_stats = balanced_result.team_result.scenario_stats

    resp: dict = {
        "stage_number": req.stage,
        "stage_type": stage.stage_type,
        "start_location": stage.start_location,
        "finish_location": stage.finish_location,
        "current_team_ev": round(current_team_ev),
        "stages_remaining": stages_remaining,
        "captain": captain,
        "captain_recommendation": {
            "rider_id": captain_id,
            "mode": variance_mode,
        },
        "captain_candidates": captain_candidates,
        "suggested_profile": suggested_profile,
        "suggested_profile_reason": suggested_reason,
        "profiles": _serialize_profiles(recommendations, rider_map),
        "team_sims": team_sims,
        "dns_alerts": dns_alerts,
        "scenario_priors": {k: round(v, 4) for k, v in resolved_scenarios.items()},
        "scenario_stats": {k: round(v, 4) for k, v in realized_scenario_stats.items()},
        "stage_intent": {f: getattr(intent, f) for f in INTENT_FIELDS},
        "prob_shaping_trace": prob_shaping_trace,
    }
    if not my_team:
        resp["team_note"] = "No team picked yet — showing best team to select from scratch."
    return resp


@app.post("/settle")
def post_settle(req: SettleRequest) -> dict:
    """
    Score all team riders for a completed stage. Updates bank + state.
    All rider references must be holdet_ids.
    """
    riders_path = config.get_riders_path()
    if not os.path.exists(riders_path):
        raise HTTPException(status_code=404, detail="No riders.json — run /ingest first")

    riders = load_riders(riders_path)
    stage = _load_stage(req.stage)
    state = _load_state()

    my_team: list[str] = state.get("my_team", [])
    captain = state.get("captain") or (my_team[0] if my_team else "")
    bank: float = state.get("bank", config.INITIAL_BUDGET)
    stages_remaining = config.TOTAL_STAGES - req.stage + 1
    all_riders_dict = {r.holdet_id: r for r in riders}
    rider_map = all_riders_dict

    # Build StageResult
    sprint_point_winners = {rid: [pts] for rid, pts in req.sprint_point_winners.items()}
    kom_point_winners = {rid: [pts] for rid, pts in req.kom_point_winners.items()}

    result = StageResult(
        stage_number=req.stage,
        finish_order=req.finish_order,
        times_behind_winner=req.times_behind_winner,
        sprint_point_winners=sprint_point_winners,
        kom_point_winners=kom_point_winners,
        jersey_winners=req.jersey_winners,
        most_aggressive=req.most_aggressive,
        dnf_riders=req.dnf_riders,
        dns_riders=req.dns_riders,
        disqualified=[],
        ttt_team_order=req.ttt_team_order,
        gc_standings=req.gc_standings,
    )

    # Score team riders
    rider_results = []
    total_bank_delta = 0.0
    etapebonus_credited = False

    for rid in my_team:
        r = rider_map.get(rid)
        if not r:
            continue
        vd = score_rider(
            rider=r,
            stage=stage,
            result=result,
            my_team=my_team,
            captain=captain,
            stages_remaining=stages_remaining,
            all_riders=all_riders_dict,
        )
        total_bank_delta += vd.captain_bank_deposit
        if not etapebonus_credited:
            total_bank_delta += vd.etapebonus_bank_deposit
            etapebonus_credited = True

        rider_results.append({
            "holdet_id": rid,
            "name": r.name,
            "is_captain": rid == captain,
            "stage_position_value": vd.stage_position_value,
            "gc_standing_value": vd.gc_standing_value,
            "jersey_bonus": vd.jersey_bonus,
            "sprint_kom_value": vd.sprint_kom_value,
            "late_arrival_penalty": vd.late_arrival_penalty,
            "dnf_penalty": vd.dnf_penalty,
            "dns_penalty": vd.dns_penalty,
            "team_bonus": vd.team_bonus,
            "ttt_value": vd.ttt_value,
            "total_rider_value_delta": vd.total_rider_value_delta,
            "captain_bank_deposit": vd.captain_bank_deposit,
            "etapebonus_bank_deposit": vd.etapebonus_bank_deposit if not etapebonus_credited else 0,
        })

    new_bank = bank + total_bank_delta

    # Brier tracking
    stage_probs_raw = state.get("probs_by_stage", {}).get(str(req.stage), {})
    brier_summary = None
    if stage_probs_raw:
        from scoring.probabilities import RiderProb
        stage_probs = {
            rid: RiderProb(
                rider_id=rid, stage_number=req.stage,
                p_win=d.get("p_win", 0.0), p_top3=d.get("p_top3", 0.0),
                p_top10=d.get("p_top10", 0.0), p_top15=d.get("p_top15", 0.0),
                p_dnf=d.get("p_dnf", 0.0), source=d.get("source", "model"),
            )
            for rid, d in stage_probs_raw.items()
        }
        accuracy_records = record_stage_accuracy(req.stage, stage_probs, result, state)
        state = save_accuracy(accuracy_records, state)
        if accuracy_records:
            avg_brier = sum(r.model_brier for r in accuracy_records) / len(accuracy_records)
            brier_summary = {"avg_model_brier": round(avg_brier, 4), "records": len(accuracy_records)}

    # Update state
    state["bank"] = new_bank
    state["current_stage"] = req.stage
    state["stages_completed"] = list(set(state.get("stages_completed", []) + [req.stage]))

    # Save result history for validate command
    stage_key = f"stage_{req.stage}"
    state.setdefault("result_history", {})[stage_key] = {
        "stage_number": result.stage_number,
        "finish_order": result.finish_order,
        "times_behind_winner": result.times_behind_winner,
        "sprint_point_winners": result.sprint_point_winners,
        "kom_point_winners": result.kom_point_winners,
        "jersey_winners": result.jersey_winners,
        "most_aggressive": result.most_aggressive,
        "dnf_riders": result.dnf_riders,
        "dns_riders": result.dns_riders,
        "disqualified": result.disqualified,
        "ttt_team_order": result.ttt_team_order,
        "gc_standings": result.gc_standings,
    }
    state.setdefault("value_snapshot", {})[stage_key] = {
        rid: rider_map[rid].value for rid in my_team if rid in rider_map
    }

    # Update rider values in riders.json
    for rid in my_team:
        r = rider_map.get(rid)
        if not r:
            continue
        vd = score_rider(
            rider=r, stage=stage, result=result, my_team=my_team,
            captain=captain, stages_remaining=stages_remaining, all_riders=all_riders_dict,
        )
        r.value += vd.total_rider_value_delta

    save_riders(riders, riders_path)
    _save_state(state)

    # Auto-sync to Supabase (best-effort)
    try:
        from scripts.sync_to_supabase import sync_all
        sync_all(race="giro_2026")
    except Exception:
        pass

    return {
        "stage": req.stage,
        "old_bank": bank,
        "new_bank": new_bank,
        "total_bank_delta": total_bank_delta,
        "rider_results": rider_results,
        "brier_summary": brier_summary,
    }


@app.post("/team")
def post_team(req: TeamRequest) -> dict:
    """Update my_team and captain in state.json, then sync to Supabase."""
    if len(req.my_team) != 8:
        raise HTTPException(status_code=422, detail=f"Team must have exactly 8 riders, got {len(req.my_team)}")
    if req.captain not in req.my_team:
        raise HTTPException(status_code=422, detail="Captain must be one of the 8 team riders")

    state = _load_state()
    state["my_team"] = req.my_team
    state["captain"] = req.captain
    _save_state(state)

    # Auto-sync
    try:
        from scripts.sync_to_supabase import sync_all
        sync_all(race="giro_2026")
    except Exception:
        pass

    return {"my_team": req.my_team, "captain": req.captain, "saved": True}


@app.post("/sync")
def post_sync() -> dict:
    """Push current state to Supabase."""
    try:
        from scripts.sync_to_supabase import sync_all
        result = sync_all(race="giro_2026")
        return {"synced": True, **(result or {})}
    except ImportError:
        raise HTTPException(status_code=500, detail="supabase-py not installed. Run: pip install supabase")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
