#!/usr/bin/env python3.14
"""
scripts/sync_to_supabase.py — Sync local state to Supabase.

Reads local JSON files and upserts to Supabase. Call after each:
  python3 main.py ingest  → then python3 scripts/sync_to_supabase.py --race giro_2026
  python3 main.py brief   → then python3 scripts/sync_to_supabase.py --race giro_2026
  python3 main.py settle  → then python3 scripts/sync_to_supabase.py --race giro_2026

Requires env vars (in .env or environment):
  SUPABASE_URL          https://xcmyypnywmqdofukkvga.supabase.co
  SUPABASE_SERVICE_KEY  service_role key — bypasses RLS

user_id is read from state.json["user_id"]. Set once during setup:
  python3 scripts/sync_to_supabase.py --set-user-id <uuid>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent

RIDERS_JSON = ROOT / "data" / "riders.json"
STAGES_JSON = ROOT / "data" / "stages.json"
STATE_JSON  = ROOT / "data" / "state.json"


def _load_env() -> None:
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def _supabase_client():
    try:
        from supabase import create_client
    except ImportError:
        print("WARNING: supabase-py not installed. Run: pip install supabase")
        return None

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")
    if not url or not key:
        print("WARNING: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
        return None
    return create_client(url, key)


def _load_json(path: Path) -> dict | list | None:
    if not path.exists():
        print(f"  WARNING: {path} not found — skipping")
        return None
    with open(path) as f:
        return json.load(f)


def sync_stages(client, race: str) -> int:
    raw = _load_json(STAGES_JSON)
    if raw is None:
        return 0
    stages_list = raw.get("stages", raw) if isinstance(raw, dict) else raw

    rows = []
    for s in stages_list:
        rows.append({
            "race":              race,
            "number":            s.get("number"),
            "stage_type":        s.get("stage_type", "flat"),
            "date":              s.get("date"),
            "distance_km":       s.get("distance_km"),
            "start_location":    s.get("start_location"),
            "finish_location":   s.get("finish_location"),
            "profile_score":     s.get("profile_score"),
            "ps_final_25k":      s.get("ps_final_25k"),
            "gradient_final_km": s.get("gradient_final_km"),
            "vertical_meters":   s.get("vertical_meters"),
            "sprint_points":     json.dumps(s.get("sprint_points", [])),
            "kom_points":        json.dumps(s.get("kom_points", [])),
            "notes":             s.get("notes", ""),
            "image_url":         s.get("image_url"),
        })

    if rows:
        client.table("stages").upsert(rows, on_conflict="race,number").execute()
    return len(rows)


def sync_riders(client, race: str, user_id: str) -> int:
    raw = _load_json(RIDERS_JSON)
    if raw is None:
        return 0
    riders_list = raw if isinstance(raw, list) else raw.get("riders", [])

    rows = []
    for r in riders_list:
        rows.append({
            "user_id":    user_id,
            "race":       race,
            "holdet_id":  r.get("holdet_id"),
            "person_id":  r.get("person_id"),
            "team_id":    r.get("team_id"),
            "name":       r.get("name"),
            "team":       r.get("team"),
            "team_abbr":  r.get("team_abbr"),
            "value":      r.get("value"),
            "start_value": r.get("start_value"),
            "points":     r.get("points", 0),
            "status":     r.get("status", "active"),
            "gc_position": r.get("gc_position"),
            "jerseys":    json.dumps(r.get("jerseys", [])),
            "in_my_team": r.get("in_my_team", False),
            "is_captain": r.get("is_captain", False),
        })

    if rows:
        client.table("riders").upsert(
            rows, on_conflict="user_id,race,holdet_id"
        ).execute()
    return len(rows)


def sync_game_state(client, race: str, user_id: str, state: dict) -> bool:
    row = {
        "user_id":          user_id,
        "race":             race,
        "current_stage":    state.get("current_stage", 1),
        "total_stages":     state.get("total_stages", 21),
        "my_team":          json.dumps(state.get("my_team", [])),
        "captain":          state.get("captain"),
        "bank":             state.get("bank", 50_000_000),
        "initial_budget":   state.get("initial_budget", 50_000_000),
        "stages_completed": json.dumps(state.get("stages_completed", [])),
        "my_rank":          state.get("rank"),
        "total_participants": state.get("total_participants"),
    }
    client.table("game_state").upsert(
        [row], on_conflict="user_id,race"
    ).execute()
    return True


def sync_prob_snapshots(client, race: str, user_id: str, state: dict) -> int:
    prob_history = state.get("prob_history", {})
    rows = []
    for stage_key, stage_probs in prob_history.items():
        stage_number = int(stage_key.replace("stage_", ""))
        for rider_id, rp in stage_probs.items():
            rows.append({
                "user_id":          user_id,
                "race":             race,
                "stage_number":     stage_number,
                "rider_id":         rider_id,
                "p_win":            rp.get("p_win"),
                "p_top3":           rp.get("p_top3"),
                "p_top10":          rp.get("p_top10"),
                "p_top15":          rp.get("p_top15"),
                "p_dnf":            rp.get("p_dnf"),
                "source":           rp.get("source", "model"),
                "model_confidence": rp.get("model_confidence"),
                "manual_overrides": json.dumps(rp.get("manual_overrides", {})),
            })

    if rows:
        client.table("prob_snapshots").upsert(
            rows, on_conflict="user_id,race,stage_number,rider_id"
        ).execute()
    return len(rows)


def sync_value_history(client, race: str, user_id: str, state: dict) -> int:
    value_history = state.get("value_history", {})
    rows = []
    for stage_key, stage_values in value_history.items():
        stage_number = int(stage_key.replace("stage_", ""))
        for rider_id, delta in stage_values.items():
            rows.append({
                "user_id":      user_id,
                "race":         race,
                "stage_number": stage_number,
                "rider_id":     rider_id,
                "delta_json":   json.dumps(delta),
            })

    if rows:
        client.table("value_history").upsert(
            rows, on_conflict="user_id,race,stage_number,rider_id"
        ).execute()
    return len(rows)


def sync_brier_history(client, race: str, user_id: str, state: dict) -> int:
    brier_history = state.get("brier_history", [])
    rows = []
    for rec in brier_history:
        rows.append({
            "user_id":      user_id,
            "race":         race,
            "stage_number": rec.get("stage"),
            "rider_id":     rec.get("rider_id"),
            "event":        rec.get("event"),
            "model_prob":   rec.get("model_prob"),
            "manual_prob":  rec.get("manual_prob"),
            "actual":       rec.get("actual"),
            "model_brier":  rec.get("model_brier"),
            "manual_brier": rec.get("manual_brier"),
        })

    if rows:
        client.table("brier_history").upsert(
            rows, on_conflict="user_id,race,stage_number,rider_id,event"
        ).execute()
    return len(rows)


def main() -> None:
    _load_env()

    parser = argparse.ArgumentParser(description="Sync local state to Supabase")
    parser.add_argument("--race", default="giro_2026", help="Race identifier")
    parser.add_argument("--set-user-id", metavar="UUID",
                        help="Write user_id into state.json and exit")
    args = parser.parse_args()

    # ── set-user-id helper ────────────────────────────────────────────────────
    if args.set_user_id:
        state: dict = {}
        if STATE_JSON.exists():
            with open(STATE_JSON) as f:
                state = json.load(f)
        state["user_id"] = args.set_user_id
        with open(STATE_JSON, "w") as f:
            json.dump(state, f, indent=2)
        print(f"user_id set to {args.set_user_id} in {STATE_JSON}")
        return

    # ── load state ────────────────────────────────────────────────────────────
    state_raw = _load_json(STATE_JSON)
    if state_raw is None:
        state_raw = {}

    user_id = state_raw.get("user_id")
    if not user_id:
        print("WARNING: state.json has no user_id. Run:")
        print("  python3 scripts/sync_to_supabase.py --set-user-id <uuid>")
        print("Get your UUID from Supabase Auth > Users after signing up.")
        sys.exit(0)

    # ── connect ───────────────────────────────────────────────────────────────
    client = _supabase_client()
    if client is None:
        sys.exit(0)  # warning already printed — don't break CLI workflow

    # ── sync ──────────────────────────────────────────────────────────────────
    try:
        n_stages  = sync_stages(client, args.race)
        n_riders  = sync_riders(client, args.race, user_id)
        sync_game_state(client, args.race, user_id, state_raw)
        n_probs   = sync_prob_snapshots(client, args.race, user_id, state_raw)
        n_values  = sync_value_history(client, args.race, user_id, state_raw)
        n_brier   = sync_brier_history(client, args.race, user_id, state_raw)

        parts = [f"{n_stages} stages", f"{n_riders} riders"]
        if n_probs:  parts.append(f"{n_probs} prob snapshots")
        if n_values: parts.append(f"{n_values} value history rows")
        if n_brier:  parts.append(f"{n_brier} brier records")
        print(f"Synced: {', '.join(parts)}")

    except Exception as e:
        print(f"WARNING: Supabase sync failed: {e}")
        sys.exit(0)  # graceful — don't break the CLI workflow


def sync_all(race: str = "giro_2026") -> dict:
    """
    Programmatic entry point for the API server.
    Returns a summary dict. Raises on hard errors; returns {} on soft failures.
    """
    _load_env()
    state_raw = _load_json(STATE_JSON)
    if state_raw is None:
        return {}
    user_id = state_raw.get("user_id")
    if not user_id:
        return {}
    client = _supabase_client()
    if client is None:
        return {}
    n_stages  = sync_stages(client, race)
    n_riders  = sync_riders(client, race, user_id)
    sync_game_state(client, race, user_id, state_raw)
    n_probs   = sync_prob_snapshots(client, race, user_id, state_raw)
    n_values  = sync_value_history(client, race, user_id, state_raw)
    n_brier   = sync_brier_history(client, race, user_id, state_raw)
    return {
        "stages": n_stages, "riders": n_riders, "prob_snapshots": n_probs,
        "value_history": n_values, "brier_history": n_brier,
    }


if __name__ == "__main__":
    main()
