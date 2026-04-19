"""
main.py — CLI orchestrator for the Holdet fantasy cycling tool.

Commands:
    python3 main.py ingest --stage N    fetch riders + team from API
    python3 main.py brief  --stage N    generate pre-stage briefing
    python3 main.py settle --stage N    record stage results, score riders
    python3 main.py status              show team, bank, rank
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime
from typing import Optional

import config
from ingestion.api import fetch_riders, fetch_my_team, save_riders, load_riders
from scoring.engine import (
    Rider, Stage, StageResult, SprintPoint, KOMPoint, score_rider,
)
from scoring.probabilities import generate_priors, interactive_adjust, save_probs
from scoring.simulator import simulate_team
from scoring.optimizer import (
    optimize_all_profiles, suggest_profile, format_briefing_table,
)
from output.report import BriefingOutput, format_briefing, format_status
from output.tracker import (
    record_stage_accuracy, format_brier_summary, save_accuracy,
)


# ── State helpers ─────────────────────────────────────────────────────────────

def _load_state(path: str) -> dict:
    """Load state.json or return fresh defaults if file does not exist."""
    defaults = {
        "current_stage": 0,
        "bank": config.INITIAL_BUDGET,
        "rank": None,
        "total_participants": None,
        "my_team": [],          # list of holdet_ids
        "captain": None,        # holdet_id
        "stages_completed": [],
        "probs_by_stage": {},
    }
    if not os.path.exists(path):
        return defaults
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        # Merge: defaults fill missing keys so old state files stay compatible
        for k, v in defaults.items():
            data.setdefault(k, v)
        return data
    except (json.JSONDecodeError, OSError):
        return defaults


def _save_state(state: dict, path: str) -> None:
    """Atomic write: write to .tmp then replace, so state is never half-written."""
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


# ── Stage loader ──────────────────────────────────────────────────────────────

def _load_stage(stages_path: str, stage_number: int) -> Stage:
    """
    Load a Stage from data/stages.json.

    stages.json must be a list of stage objects keyed by 'number'.
    """
    if not os.path.exists(stages_path):
        raise FileNotFoundError(
            f"Stages file not found: {stages_path}\n"
            "Create data/stages.json with stage definitions."
        )
    with open(stages_path, encoding="utf-8") as fh:
        stages_data = json.load(fh)

    # Support list, {"stages": [...]} dict, or legacy dict-of-values
    if isinstance(stages_data, list):
        stages_list = stages_data
    elif isinstance(stages_data, dict) and "stages" in stages_data:
        stages_list = stages_data["stages"]
    elif isinstance(stages_data, dict):
        stages_list = [v for v in stages_data.values() if isinstance(v, dict)]
    else:
        raise ValueError(f"Unexpected stages.json format: {type(stages_data)}")

    for s in stages_list:
        if not isinstance(s, dict):
            continue
        if s.get("number") == stage_number:
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

    raise ValueError(f"Stage {stage_number} not found in {stages_path}")


# ── Name resolver helpers ─────────────────────────────────────────────────────

def _resolve_name(fragment: str, rider_map: dict) -> str:
    """
    Resolve a name fragment or holdet_id to a holdet_id.

    Case-insensitive partial match against rider names.
    Raises ValueError on no match or ambiguous match.
    """
    fragment = fragment.strip()

    # Direct ID match
    if fragment in rider_map:
        return fragment

    frag_lower = fragment.lower()
    matches = [
        rid for rid, r in rider_map.items()
        if frag_lower in r.name.lower()
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) == 0:
        raise ValueError(f"No rider found matching '{fragment}'")
    names = ", ".join(rider_map[rid].name for rid in matches)
    raise ValueError(f"Ambiguous: '{fragment}' matches multiple riders: {names}")


def _resolve_list(raw: str, rider_map: dict) -> list:
    """
    Parse comma-separated rider fragments to a list of holdet_ids.
    'none', '', 'n/a' → empty list.
    """
    raw = raw.strip()
    if not raw or raw.lower() in ("none", "n/a", "-"):
        return []
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return [_resolve_name(p, rider_map) for p in parts]


def _resolve_kv_list(raw: str, rider_map: dict) -> dict:
    """
    Parse 'rider:pts, rider:pts, ...' to dict of holdet_id → pts.
    'none', '' → {}.
    """
    raw = raw.strip()
    if not raw or raw.lower() in ("none", "n/a", "-"):
        return {}
    result = {}
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            frag, pts_str = item.rsplit(":", 1)
            rid = _resolve_name(frag.strip(), rider_map)
            result[rid] = int(pts_str.strip())
        else:
            rid = _resolve_name(item, rider_map)
            result[rid] = 0
    return result


# ── Validation log ────────────────────────────────────────────────────────────

def _log_mismatch(stage_number: int, rider_name: str, field: str,
                  expected: int, actual: int, notes: str = "") -> None:
    """Append a validation mismatch to tests/validation_log.md."""
    log_path = "tests/validation_log.md"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    line = (
        f"| {ts} | Stage {stage_number} | {rider_name} | {field} "
        f"| {expected:+,} | {actual:+,} | {actual - expected:+,} | {notes} |\n"
    )
    header_needed = not os.path.exists(log_path)
    with open(log_path, "a", encoding="utf-8") as fh:
        if header_needed:
            fh.write(
                "# Validation Log\n\n"
                "| Timestamp | Stage | Rider | Field | Engine | Actual | Delta | Notes |\n"
                "|-----------|-------|-------|-------|--------|--------|-------|-------|\n"
            )
        fh.write(line)


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_ingest(args) -> None:
    """Fetch riders and team from Holdet API, update state."""
    cookie = config.get_cookie()
    game_id = config.get_game_id()
    fantasy_team_id = config.get_fantasy_team_id()
    cartridge = config.get_cartridge()
    riders_path = config.get_riders_path()
    state_path = config.get_state_path()

    print(f"Fetching riders for game {game_id}...")
    riders = fetch_riders(game_id, cookie)
    print(f"  {len(riders)} riders fetched.")

    save_riders(riders, riders_path)
    print(f"  Saved to {riders_path}")

    state = _load_state(state_path)
    state["current_stage"] = args.stage

    print("Fetching your team...")
    try:
        team_data = fetch_my_team(fantasy_team_id, cartridge, cookie)
        lineup = team_data.get("lineup", [])
        captain_raw = team_data.get("captain", "")
        bank = team_data.get("bank", 0)

        # Map lineup player ids to holdet_ids
        rider_map = {r.holdet_id: r for r in riders}
        my_team_ids = []
        for player in lineup:
            pid = str(player.get("id", ""))
            if pid in rider_map:
                my_team_ids.append(pid)

        state["my_team"] = my_team_ids
        state["captain"] = str(captain_raw) if captain_raw else None
        state["bank"] = bank

        print(f"\nYour team ({len(my_team_ids)} riders):")
        dns_alerts = []
        for rid in my_team_ids:
            r = rider_map.get(rid)
            if r:
                cap_marker = " [CAPTAIN]" if rid == state["captain"] else ""
                dns_marker = " *** DNS ***" if r.status == "dns" else ""
                print(f"  {r.name} ({r.team_abbr}) {r.value/1e6:.2f}M{cap_marker}{dns_marker}")
                if r.status == "dns":
                    stages_left = config.TOTAL_STAGES - args.stage + 1
                    penalty = stages_left * -100_000
                    dns_alerts.append(
                        f"  ALERT: {r.name} is DNS — penalty {penalty:,} "
                        f"({stages_left} stages remaining)"
                    )
        print(f"  Bank: {bank/1e6:.2f}M")

        if dns_alerts:
            print("\n" + "\n".join(dns_alerts))

    except PermissionError as exc:
        print(f"\nWarning: Could not fetch team — {exc}", file=sys.stderr)
        print("Riders saved. Update HOLDET_COOKIE in .env and re-run ingest.", file=sys.stderr)

    _save_state(state, state_path)
    print(f"\nState saved to {state_path}")


def cmd_brief(args) -> None:
    """Generate pre-stage briefing across all 4 risk profiles."""
    riders_path = config.get_riders_path()
    stages_path = config.get_stages_path()
    state_path = config.get_state_path()

    riders = load_riders(riders_path)
    stage = _load_stage(stages_path, args.stage)
    state = _load_state(state_path)

    my_team = state.get("my_team", [])
    captain = state.get("captain") or (my_team[0] if my_team else "")
    bank = state.get("bank", config.INITIAL_BUDGET)
    rank = state.get("rank")
    total = state.get("total_participants")
    stages_remaining = config.TOTAL_STAGES - args.stage + 1

    rider_map = {r.holdet_id: r for r in riders}

    print(f"\nStage {args.stage}: {stage.start_location} → {stage.finish_location} "
          f"({stage.stage_type}, {stage.distance_km:.0f}km)")
    if stage.is_ttt:
        print("  *** TTT ***")

    # 1. Generate model priors
    probs = generate_priors(riders, stage)

    # 2. Interactive adjustment
    probs = interactive_adjust(probs, stage, riders)

    # 3. Simulate team only (fast preview)
    team_riders = [r for r in riders if r.holdet_id in my_team]
    team_sims = simulate_team(
        riders=team_riders,
        stage=stage,
        probs=probs,
        my_team=my_team,
        captain=captain,
        stages_remaining=stages_remaining,
    )

    # 4. Simulate all riders for optimizer
    all_sims = simulate_team(
        riders=riders,
        stage=stage,
        probs=probs,
        my_team=my_team,
        captain=captain,
        stages_remaining=stages_remaining,
    )

    # 5. Optimize all profiles
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
    )

    # 6. Build BriefingOutput and render with format_briefing
    current_team_ev = sum(
        team_sims[rid].expected_value for rid in my_team if rid in team_sims
    )
    suggested_profile = None
    suggested_reason = "No rank data — defaulting to BALANCED"
    if rank and total:
        suggested_profile, suggested_reason = suggest_profile(rank, total, stages_remaining)

    briefing_output = BriefingOutput(
        stage=stage,
        my_team=my_team,
        captain=captain,
        riders=riders,
        probs=probs,
        current_team_ev=current_team_ev,
        suggested_profile=suggested_profile,
        suggested_profile_reason=suggested_reason,
        profiles=recommendations,
    )
    print("\n" + format_briefing(briefing_output, state))

    # 7. Save probs to state for audit trail
    probs_dict = {
        rid: {
            "p_win": rp.p_win,
            "p_top3": rp.p_top3,
            "p_top10": rp.p_top10,
            "p_top15": rp.p_top15,
            "p_dnf": rp.p_dnf,
            "source": rp.source,
        }
        for rid, rp in probs.items()
    }
    state["probs_by_stage"][str(args.stage)] = probs_dict
    _save_state(state, state_path)
    print(f"\nProbs saved to {state_path}")


def cmd_settle(args) -> None:
    """
    Record stage results interactively, score all team riders, update bank.

    Prompts for:
      1. Finish order (top 15, comma-separated name fragments)
      2. DNF riders
      3. DNS riders
      4. GC standings (top 10, comma-separated)
      5. Yellow jersey winner
      6. Green jersey winner
      7. Polkadot jersey winner
      8. White jersey winner
      9. Most aggressive (red number)
     10. Sprint point winners (rider:pts, ...)
     11. KOM point winners (rider:pts, ...)
     12. TTT team placement order (TTT only)
     13. Your Holdet rank after this stage
    """
    riders_path = config.get_riders_path()
    stages_path = config.get_stages_path()
    state_path = config.get_state_path()

    riders = load_riders(riders_path)
    stage = _load_stage(stages_path, args.stage)
    state = _load_state(state_path)

    my_team = state.get("my_team", [])
    captain = state.get("captain") or (my_team[0] if my_team else "")
    bank = state.get("bank", config.INITIAL_BUDGET)
    stages_remaining = config.TOTAL_STAGES - args.stage + 1

    rider_map = {r.holdet_id: r for r in riders}

    print(f"\nSettling Stage {args.stage}: {stage.start_location} → {stage.finish_location}")
    print("Enter rider names as partial fragments (e.g. 'ving' for Vingegaard). 'none' for empty.\n")

    def ask(prompt: str) -> str:
        return input(f"  {prompt}: ").strip()

    # Finish order — top 15
    while True:
        raw = ask("Top-15 finish order (comma-separated, best first)")
        try:
            finish_order = _resolve_list(raw, rider_map)
            break
        except ValueError as exc:
            print(f"  Error: {exc} — try again")

    # DNF riders
    while True:
        raw = ask("DNF riders (comma-separated, or 'none')")
        try:
            dnf_riders = _resolve_list(raw, rider_map)
            break
        except ValueError as exc:
            print(f"  Error: {exc} — try again")

    # DNS riders
    while True:
        raw = ask("DNS riders (comma-separated, or 'none')")
        try:
            dns_riders = _resolve_list(raw, rider_map)
            break
        except ValueError as exc:
            print(f"  Error: {exc} — try again")

    # GC standings top 10
    while True:
        raw = ask("GC standings top 10 (comma-separated, leader first, or 'none')")
        try:
            gc_standings = _resolve_list(raw, rider_map)
            break
        except ValueError as exc:
            print(f"  Error: {exc} — try again")

    # Jersey winners
    jersey_winners: dict[str, str] = {}
    for jersey in ("yellow", "green", "polkadot", "white"):
        while True:
            raw = ask(f"{jersey.capitalize()} jersey winner (or 'none')")
            raw = raw.strip()
            if not raw or raw.lower() in ("none", "n/a", "-"):
                break
            try:
                rid = _resolve_name(raw, rider_map)
                jersey_winners[jersey] = rid
                break
            except ValueError as exc:
                print(f"  Error: {exc} — try again")

    # Most aggressive
    most_aggressive = None
    while True:
        raw = ask("Most aggressive rider (red number, or 'none')")
        raw = raw.strip()
        if not raw or raw.lower() in ("none", "n/a", "-"):
            break
        try:
            most_aggressive = _resolve_name(raw, rider_map)
            break
        except ValueError as exc:
            print(f"  Error: {exc} — try again")

    # Sprint point winners
    while True:
        raw = ask("Sprint point winners (rider:pts, ..., or 'none')")
        try:
            sprint_winners_flat = _resolve_kv_list(raw, rider_map)
            # Wrap each as list for engine schema
            sprint_point_winners = {rid: [pts] for rid, pts in sprint_winners_flat.items()}
            break
        except ValueError as exc:
            print(f"  Error: {exc} — try again")

    # KOM point winners
    while True:
        raw = ask("KOM point winners (rider:pts, ..., or 'none')")
        try:
            kom_winners_flat = _resolve_kv_list(raw, rider_map)
            kom_point_winners = {rid: [pts] for rid, pts in kom_winners_flat.items()}
            break
        except ValueError as exc:
            print(f"  Error: {exc} — try again")

    # TTT team order (only for TTT stages)
    ttt_team_order = None
    if stage.is_ttt:
        raw = ask("TTT team placement order (team names, comma-separated)")
        ttt_team_order = [t.strip() for t in raw.split(",") if t.strip()] or None

    # Times behind winner (seconds) — only for riders with finish position
    times_behind: dict[str, int] = {}
    team_in_top15 = [rid for rid in my_team if rid in finish_order]
    if team_in_top15 and not stage.is_ttt:
        print("\n  Time gaps (seconds behind winner) for your finishers:")
        for rid in team_in_top15:
            pos = finish_order.index(rid) + 1
            if pos == 1:
                times_behind[rid] = 0
                continue
            while True:
                raw = ask(f"    {rider_map[rid].name} (pos {pos}) — seconds behind winner (0 = same group)")
                try:
                    times_behind[rid] = int(raw)
                    break
                except ValueError:
                    print("    Enter a number")

    # Build StageResult once
    result = StageResult(
        stage_number=args.stage,
        finish_order=finish_order,
        times_behind_winner=times_behind,
        sprint_point_winners=sprint_point_winners,
        kom_point_winners=kom_point_winners,
        jersey_winners=jersey_winners,
        most_aggressive=most_aggressive,
        dnf_riders=dnf_riders,
        dns_riders=dns_riders,
        disqualified=[],
        ttt_team_order=ttt_team_order,
        gc_standings=gc_standings,
    )

    # Score all team riders
    all_riders_dict = {r.holdet_id: r for r in riders}
    print(f"\nScoring {len(my_team)} team riders...\n")

    total_rider_delta = 0
    total_bank_delta = 0
    etapebonus_credited = False

    for rid in my_team:
        r = rider_map.get(rid)
        if r is None:
            print(f"  WARNING: holdet_id {rid} not found in riders.json — skipping")
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

        cap_mark = " [CAPTAIN]" if rid == captain else ""
        print(f"  {r.name}{cap_mark}:")
        print(f"    Stage pos:   {vd.stage_position_value:>+10,}")
        print(f"    GC standing: {vd.gc_standing_value:>+10,}")
        if vd.jersey_bonus:
            print(f"    Jersey:      {vd.jersey_bonus:>+10,}")
        if vd.sprint_kom_value:
            print(f"    Sprint/KOM:  {vd.sprint_kom_value:>+10,}")
        if vd.late_arrival_penalty:
            print(f"    Late arriv:  {vd.late_arrival_penalty:>+10,}")
        if vd.dnf_penalty:
            print(f"    DNF:         {vd.dnf_penalty:>+10,}")
        if vd.dns_penalty:
            print(f"    DNS:         {vd.dns_penalty:>+10,}")
        if vd.team_bonus:
            print(f"    Team bonus:  {vd.team_bonus:>+10,}")
        if vd.ttt_value:
            print(f"    TTT:         {vd.ttt_value:>+10,}")
        print(f"    TOTAL:       {vd.total_rider_value_delta:>+10,}")
        if vd.captain_bank_deposit:
            print(f"    → Bank (captain): {vd.captain_bank_deposit:>+10,}")
        if not etapebonus_credited and vd.etapebonus_bank_deposit != 0:
            print(f"    → Bank (etapebonus): {vd.etapebonus_bank_deposit:>+10,}")

        total_rider_delta += vd.total_rider_value_delta
        total_bank_delta += vd.captain_bank_deposit
        if not etapebonus_credited:
            total_bank_delta += vd.etapebonus_bank_deposit
            etapebonus_credited = True
        print()

    new_bank = bank + total_bank_delta
    print(f"  Bank: {bank/1e6:.3f}M → {new_bank/1e6:.3f}M  ({total_bank_delta:+,})")

    # Validate against Holdet site
    print("\n  Validation (optional — press Enter to skip):")
    holdet_bank_raw = input("  Your Holdet bank after this stage (e.g. 51234567, or Enter to skip): ").strip()
    if holdet_bank_raw:
        try:
            holdet_bank = int(holdet_bank_raw.replace(",", "").replace(".", ""))
            engine_delta = total_bank_delta
            actual_delta = holdet_bank - bank
            if abs(engine_delta - actual_delta) > 1000:
                _log_mismatch(args.stage, "BANK", "bank_delta", engine_delta, actual_delta,
                              "Bank total mismatch")
                print(f"  MISMATCH logged: engine {engine_delta:+,} vs actual {actual_delta:+,}")
            else:
                print(f"  Bank matches Holdet ✓  ({actual_delta:+,})")
        except ValueError:
            print("  Could not parse bank value — skipping validation")

    # Update rank
    rank_raw = input("  Your Holdet rank after this stage (or Enter to skip): ").strip()
    if rank_raw:
        try:
            state["rank"] = int(rank_raw.replace(",", ""))
        except ValueError:
            pass

    # Brier score tracking
    stage_probs_raw = state.get("probs_by_stage", {}).get(str(args.stage), {})
    if stage_probs_raw:
        from scoring.probabilities import RiderProb
        stage_probs = {
            rid: RiderProb(
                rider_id=rid,
                stage_number=args.stage,
                p_win=d.get("p_win", 0.0),
                p_top3=d.get("p_top3", 0.0),
                p_top10=d.get("p_top10", 0.0),
                p_top15=d.get("p_top15", 0.0),
                p_dnf=d.get("p_dnf", 0.0),
                source=d.get("source", "model"),
            )
            for rid, d in stage_probs_raw.items()
        }
        accuracy_records = record_stage_accuracy(args.stage, stage_probs, result, state)
        state = save_accuracy(accuracy_records, state)
        print("\n" + format_brier_summary(accuracy_records))
    else:
        print("\n  (No saved probs for this stage — skipping Brier tracking)")

    # Update state
    state["bank"] = new_bank
    state["current_stage"] = args.stage
    state["stages_completed"] = list(set(state.get("stages_completed", []) + [args.stage]))

    # Update rider values in riders.json
    for rid in my_team:
        r = rider_map.get(rid)
        if r is None:
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
        r.value += vd.total_rider_value_delta

    save_riders(riders, riders_path)
    _save_state(state, state_path)
    print(f"\nState saved. Bank: {new_bank/1e6:.3f}M")


def cmd_status(args) -> None:
    """Show current team, bank, rank, and DNS alerts."""
    riders_path = config.get_riders_path()
    state_path = config.get_state_path()

    state = _load_state(state_path)
    riders = load_riders(riders_path) if os.path.exists(riders_path) else []
    print("\n" + format_status(state, riders))


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Holdet fantasy cycling tool",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ingest
    p_ingest = sub.add_parser("ingest", help="Fetch riders and team from Holdet API")
    p_ingest.add_argument("--stage", type=int, required=True, help="Current stage number")

    # brief
    p_brief = sub.add_parser("brief", help="Generate pre-stage briefing")
    p_brief.add_argument("--stage", type=int, required=True, help="Upcoming stage number")

    # settle
    p_settle = sub.add_parser("settle", help="Record stage results and update state")
    p_settle.add_argument("--stage", type=int, required=True, help="Stage number to settle")

    # status
    sub.add_parser("status", help="Show current team, bank, and rank")

    args = parser.parse_args()

    if args.command == "ingest":
        cmd_ingest(args)
    elif args.command == "brief":
        cmd_brief(args)
    elif args.command == "settle":
        cmd_settle(args)
    elif args.command == "status":
        cmd_status(args)


if __name__ == "__main__":
    main()
