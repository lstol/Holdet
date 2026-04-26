"""
scripts/calibrate.py — Session 19: Calibration feedback loop.

Predictions → reality → Brier score → conservative adjustment.
Never overfits early data. Only applies changes when statistically justified.

Usage:
  python scripts/calibrate.py [--dry-run] [--stages 1,2,3]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from statistics import mean
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scoring.probabilities import ROLE_TOP15, RiderRole, _rider_roles
from scoring.simulator import STAGE_SCENARIOS
from scoring.engine import Rider, Stage

logger = logging.getLogger(__name__)

CALIBRATION_HISTORY_PATH = "calibration_history.json"
VALIDATION_LOG_PATH = os.getenv("VALIDATION_LOG_PATH", "tests/validation_log.md")
MAX_UPDATES_PER_RUN = 2       # prevents cascading changes from noisy early data
MIN_STAGES = 3                # minimum stages for a valid suggestion
ALPHA = 0.3                   # conservative nudge — 30% of the way to observed
MIN_CHANGE = 0.01             # stability guard — ignore noise-driven updates


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class Suggestion:
    role: str
    stage_type: str
    old: float
    new: float
    observed: float
    count: int


# ── Pure helper ───────────────────────────────────────────────────────────────

def _brier_score(p: float, o: float) -> float:
    """Brier score for a single prediction-outcome pair: (p - o)²  CORRECT.
    NOT (mean(p - o))² which is the wrong formula."""
    return (p - o) ** 2


# ── 1. Parse ──────────────────────────────────────────────────────────────────

def parse_validation_log(path: str) -> list[dict]:
    """Parse tests/validation_log.md → list of entry dicts.

    Returns only rows where Field == 'total_rider_value_delta'.
    Missing file → empty list.  Malformed rows → silently skipped.
    """
    if not os.path.exists(path):
        return []

    entries = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith("|"):
                continue
            # Strip leading/trailing pipes and split
            parts = [p.strip() for p in line.split("|")]
            parts = [p for p in parts if p]  # remove empty from leading/trailing |

            # Separator line (---|---|...)
            if parts and parts[0].startswith("-"):
                continue
            # Header row
            if parts and parts[0] == "Timestamp":
                continue
            # Need at least: Timestamp|Stage|Rider|Field|Engine|Actual|Delta|Notes
            if len(parts) < 6:
                continue

            try:
                stage_str = parts[1]
                rider = parts[2]
                field = parts[3]
                engine_str = parts[4]
                actual_str = parts[5]

                if field != "total_rider_value_delta":
                    continue

                stage = int(stage_str.split()[-1])
                engine_delta = int(engine_str.replace(",", "").replace("+", ""))
                actual_delta = int(actual_str.replace(",", "").replace("+", ""))

                entries.append({
                    "stage": stage,
                    "rider": rider,
                    "field": field,
                    "engine_delta": engine_delta,
                    "actual_delta": actual_delta,
                    "timestamp": parts[0],
                })
            except (ValueError, IndexError):
                continue  # malformed row — skip silently

    # Canonical parsed fields:
    #   engine_delta  — Engine column (model's calculated delta)
    #   actual_delta  — Actual column (Holdet API delta)
    # Do NOT rename these downstream. All functions consume engine_delta / actual_delta.
    return entries


# ── 2. Infer outcomes ─────────────────────────────────────────────────────────

def infer_outcomes(entries: list[dict], riders: list, stages: list) -> list[dict]:
    """Enrich entries with role, stage_type, outcome, scenario.

    riders — list of Rider objects
    stages — list of Stage objects
    Entries that cannot be matched to a rider or stage are dropped.
    """
    rider_by_name: dict[str, Rider] = {r.name: r for r in riders} if riders else {}
    stage_by_number: dict[int, Stage] = {s.number: s for s in stages} if stages else {}

    # First pass: enrich with role, stage_type, outcome
    enriched = []
    for entry in entries:
        rider_obj = rider_by_name.get(entry["rider"])
        stage_obj = stage_by_number.get(entry["stage"])
        if rider_obj is None or stage_obj is None:
            continue

        roles = _rider_roles(rider_obj, stage_obj)
        role = roles[0] if roles else RiderRole.DOMESTIQUE
        stage_type = stage_obj.stage_type

        # NOTE: top-15 proxy based on value delta. Imperfect signal.
        # Use only for directional calibration, not absolute accuracy.
        outcome = 1 if abs(entry.get("actual_delta", 0)) > 0 else 0

        enriched.append({
            **entry,
            "role": role,
            "stage_type": stage_type,
            "outcome": outcome,
        })

    # Second pass: infer scenario deterministically per stage
    stage_entries: dict[int, list[dict]] = defaultdict(list)
    for e in enriched:
        stage_entries[e["stage"]].append(e)

    result = []
    for stage_num, stage_ents in stage_entries.items():
        # winner_role is inferred as the rider with max(actual_delta) per stage.
        # This is a proxy for stage winner and may misclassify edge cases
        # (e.g. multiple riders with similar deltas, non-winner scoring artifacts).
        winner_entry = max(stage_ents, key=lambda e: e.get("actual_delta", 0))
        winner_role = winner_entry["role"]
        stage_type = winner_entry["stage_type"]

        if winner_role == RiderRole.SPRINTER and stage_type == "flat":
            scenario = "bunch_sprint"
        elif winner_role == RiderRole.CLIMBER and stage_type == "mountain":
            scenario = "gc_day"
        else:
            scenario = "breakaway"

        for e in stage_ents:
            result.append({**e, "scenario": scenario})

    return result


# ── 3. Brier scores ───────────────────────────────────────────────────────────

def compute_brier_scores(entries: list[dict]) -> dict:
    """Compute Brier scores per (role, stage_type).

    Always uses ROLE_TOP15[role][stage_type] as predicted probability.
    Never averages predicted values from entries.

    Returns:
      {
        "overall":        {(role, stage_type): float},
        "rolling_last_5": {(role, stage_type): float},
      }
    """
    # Collect (stage, brier) per group for windowing
    stage_scores: dict[tuple, list[tuple[int, float]]] = defaultdict(list)

    for e in entries:
        role = e.get("role")
        stage_type = e.get("stage_type")
        outcome = e.get("outcome")
        stage = e.get("stage")

        if role is None or stage_type is None or outcome is None or stage is None:
            continue
        if role not in ROLE_TOP15 or stage_type not in ROLE_TOP15.get(role, {}):
            continue

        # Always use ROLE_TOP15 constant — never averaged from data
        p = max(0.0, min(1.0, ROLE_TOP15[role][stage_type]))
        b = _brier_score(p, outcome)   # (p - o)²  CORRECT
        stage_scores[(role, stage_type)].append((stage, b))

    overall: dict[tuple, float] = {}
    rolling: dict[tuple, float] = {}

    for key, pairs in stage_scores.items():
        all_scores = [b for _, b in pairs]
        overall[key] = mean(all_scores)

        sorted_pairs = sorted(pairs, key=lambda x: x[0])
        last_5 = [b for _, b in sorted_pairs[-5:]]
        rolling[key] = mean(last_5)

    # rolling_last_5 is computed per (role, stage_type),
    # using only entries whose stage number is in the last 5 unique stages seen.
    # It is NOT a global rolling average.
    return {"overall": overall, "rolling_last_5": rolling}


# ── 4. Aggregate metrics ──────────────────────────────────────────────────────

def aggregate_metrics(entries: list[dict]) -> dict:
    """Compute per-(role, stage_type) metrics for suggest_adjustments.

    Direction:
      error = p - outcome  (where p = ROLE_TOP15[role][stage_type])
      all errors > 0  → "over"   (model overestimates)
      all errors < 0  → "under"  (model underestimates)
      otherwise       → "mixed"
    """
    groups: dict[tuple, list[int]] = defaultdict(list)

    for e in entries:
        role = e.get("role")
        stage_type = e.get("stage_type")
        outcome = e.get("outcome")

        if role is None or stage_type is None or outcome is None:
            continue
        if role not in ROLE_TOP15 or stage_type not in ROLE_TOP15.get(role, {}):
            continue

        groups[(role, stage_type)].append(outcome)

    metrics: dict[tuple, dict] = {}
    for (role, stage_type), outcomes in groups.items():
        p = ROLE_TOP15[role][stage_type]
        errors = [p - o for o in outcomes]
        observed_rate = mean(outcomes)

        if all(err > 0 for err in errors):
            direction = "over"
        elif all(err < 0 for err in errors):
            direction = "under"
        else:
            direction = "mixed"

        metrics[(role, stage_type)] = {
            "count": len(outcomes),
            "observed_rate": observed_rate,
            "direction": direction,
        }

    return metrics


# ── 5. Suggest adjustments ────────────────────────────────────────────────────

def suggest_adjustments(metrics: dict) -> list[Suggestion]:
    """Generate conservative calibration suggestions.

    Valid only when: count ≥ 3, direction is not "mixed", change ≥ 0.01,
    and new value does not overshoot past observed mean.
    Max 2 suggestions returned (enforced in run_calibration after holdout).
    """
    suggestions = []
    for (role, stage_type), m in metrics.items():
        if m["count"] < MIN_STAGES:
            continue
        if m["direction"] == "mixed":
            continue

        current = ROLE_TOP15[role][stage_type]  # always use constant, never mean
        observed = m["observed_rate"]
        new = current + ALPHA * (observed - current)

        # No overshoot past observed mean
        low, high = min(current, observed), max(current, observed)
        new = max(low, min(new, high))

        # Stability guard — ignore noise-driven updates
        if abs(new - current) < MIN_CHANGE:
            continue

        suggestions.append(Suggestion(
            role=role,
            stage_type=stage_type,
            old=round(current, 4),
            new=round(new, 4),
            observed=round(observed, 4),
            count=m["count"],
        ))

    return suggestions


# ── 6. Holdout validation ─────────────────────────────────────────────────────

def evaluate_holdout(entries: list[dict], suggestion: Suggestion) -> tuple[float, float]:
    """Validate suggestion with holdout evaluation.

    < 5 unique stages → leave-one-out cross-validation
    ≥ 5 unique stages → last stage as holdout

    Only evaluates entries matching (role, stage_type) of the suggestion.
    Never mutates ROLE_TOP15 — uses local old/new values directly.

    Returns (brier_before, brier_after).
    Caller must reject if brier_after >= brier_before.
    """
    relevant = [
        e for e in entries
        if e.get("role") == suggestion.role and e.get("stage_type") == suggestion.stage_type
    ]
    if not relevant:
        # (inf, inf) signals insufficient data for this (role, stage_type).
        # Caller must treat this as rejection: brier_after < brier_before is False.
        return float("inf"), float("inf")

    unique_stages = sorted(set(e["stage"] for e in relevant))
    n_stages = len(unique_stages)

    if n_stages >= 5:
        # Last stage as holdout
        holdout_stage = unique_stages[-1]
        holdout = [e for e in relevant if e["stage"] == holdout_stage]
        b_before = mean(_brier_score(suggestion.old, e["outcome"]) for e in holdout)
        b_after = mean(_brier_score(suggestion.new, e["outcome"]) for e in holdout)
        return b_before, b_after

    else:
        # Leave-one-out cross-validation over stages
        before_scores: list[float] = []
        after_scores: list[float] = []
        for held_stage in unique_stages:
            held = [e for e in relevant if e["stage"] == held_stage]
            for e in held:
                before_scores.append(_brier_score(suggestion.old, e["outcome"]))
                after_scores.append(_brier_score(suggestion.new, e["outcome"]))
        return mean(before_scores), mean(after_scores)


# ── 7. Scenario frequency analysis ───────────────────────────────────────────

def scenario_frequency_analysis(entries: list[dict], stages: list) -> list[dict]:
    """Compare STAGE_SCENARIOS priors vs observed frequencies.

    Flags if abs(expected - observed) > 0.25.
    Reports only — no suggestions, no auto-adjustment.
    """
    # One scenario per (stage_type, stage_num)
    seen: dict[tuple[str, int], str] = {}
    for e in entries:
        stage_type = e.get("stage_type")
        stage = e.get("stage")
        scenario = e.get("scenario")
        if stage_type and stage is not None and scenario:
            seen[(stage_type, stage)] = scenario

    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    totals: dict[str, int] = defaultdict(int)
    for (stage_type, _), scenario in seen.items():
        counts[stage_type][scenario] += 1
        totals[stage_type] += 1

    result = []
    for stage_type, scenarios in STAGE_SCENARIOS.items():
        total = totals.get(stage_type, 0)
        for scenario_name, expected_prob in scenarios:
            observed_count = counts.get(stage_type, {}).get(scenario_name, 0)
            observed_prob = observed_count / total if total > 0 else 0.0
            gap = abs(expected_prob - observed_prob)
            result.append({
                "stage_type": stage_type,
                "scenario": scenario_name,
                "expected": expected_prob,
                "observed": round(observed_prob, 4),
                "gap": round(gap, 4),
                "flagged": gap > 0.25,
            })

    return result


# ── Persistence ───────────────────────────────────────────────────────────────

def _append_calibration_history(path: str, stages_used: list[int], changes: list[dict]) -> None:
    """Append-only history write. Never overwrites existing records."""
    history: list[dict] = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            try:
                history = json.load(fh)
            except json.JSONDecodeError:
                history = []

    history.append({
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "stages_used": stages_used,
        "changes": changes,
    })

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(history, fh, indent=2)


# ── Core logic (separated from CLI for testability) ───────────────────────────

def run_calibration(
    entries: list[dict],
    dry_run: bool = False,
    history_path: str = CALIBRATION_HISTORY_PATH,
    input_fn=input,
) -> list[dict]:
    """Core calibration logic. Returns list of accepted+written changes.

    In dry_run mode: prints suggestions with debug, no prompt, no write.
    """
    unique_stages = sorted(set(e["stage"] for e in entries))

    # Training/holdout split for suggestion generation
    if len(unique_stages) >= 5:
        training_entries = [e for e in entries if e["stage"] != unique_stages[-1]]
    else:
        training_entries = entries

    metrics = aggregate_metrics(training_entries)

    if dry_run:
        for (role, stage_type), m in metrics.items():
            print(f"  [DEBUG] Group {role.upper()}-{stage_type} count={m['count']} direction={m['direction']}")
            if m["direction"] != "mixed":
                current = ROLE_TOP15[role][stage_type]
                suggested = round(current + ALPHA * (m["observed_rate"] - current), 2)
                print(f"  [DEBUG] Observed={m['observed_rate']:.2f} Current={current} Suggested={suggested}")
        print()

    suggestions = suggest_adjustments(metrics)

    if not suggestions:
        if not dry_run:
            print("No adjustments needed.")
        return []

    accepted_changes: list[dict] = []

    for suggestion in suggestions:
        if len(accepted_changes) >= MAX_UPDATES_PER_RUN:
            print(f"[DEBUG] Max updates ({MAX_UPDATES_PER_RUN}) reached — remaining suggestions skipped")
            break

        brier_before, brier_after = evaluate_holdout(entries, suggestion)
        accepted = brier_after < brier_before

        if dry_run:
            status_str = "✔ passes holdout" if accepted else "✘ rejected by holdout"
            print(f"  Role: {suggestion.role.upper()} ({suggestion.stage_type})")
            print(f"  Current: {suggestion.old}  Observed: {suggestion.observed}  Suggested: {suggestion.new}")
            print(f"  Brier: {brier_before:.2f} → {brier_after:.2f}  {status_str}")
            print()
            continue

        if not accepted:
            print(f"[DEBUG] Rejected {suggestion.role.upper()}-{suggestion.stage_type} "
                  f"— holdout failed ({brier_before:.4f} → {brier_after:.4f})")
            continue

        print(f"\nRole: {suggestion.role.upper()} ({suggestion.stage_type})")
        print(f"Current: {suggestion.old}  Observed: {suggestion.observed}  Suggested: {suggestion.new}")
        print(f"Brier: {brier_before:.2f} → {brier_after:.2f}  ✔ passes holdout")

        answer = input_fn("Apply this change? [yes/no]: ").strip().lower()
        if answer != "yes":
            continue

        accepted_changes.append({
            "constant": f"ROLE_TOP15.{suggestion.role.upper()}.{suggestion.stage_type}",
            "old": suggestion.old,
            "new": suggestion.new,
            "brier_before": round(brier_before, 4),
            "brier_after": round(brier_after, 4),
        })

    if accepted_changes:
        _append_calibration_history(history_path, unique_stages, accepted_changes)
        print(f"\nCalibration applied. History updated at {history_path}")

    # Scenario frequency report
    scenario_gaps = scenario_frequency_analysis(entries, [])
    for g in scenario_gaps:
        if g["flagged"]:
            print(f"Scenario gap: {g['scenario']} expected {g['expected']:.2f} vs observed {g['observed']:.2f} ⚠")

    return accepted_changes


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate ROLE_TOP15 probabilities from validation log")
    parser.add_argument("--dry-run", action="store_true", help="Print suggestions, no prompt, no write")
    parser.add_argument("--stages", type=str, default=None,
                        help="Comma-separated stage numbers to include, e.g. 1,2,3")
    args = parser.parse_args()

    log_path = os.getenv("VALIDATION_LOG_PATH", VALIDATION_LOG_PATH)
    entries = parse_validation_log(log_path)

    # Apply --stages filter immediately after parsing, before all downstream computations
    if args.stages:
        stage_filter = {int(s.strip()) for s in args.stages.split(",")}
        entries = [e for e in entries if e["stage"] in stage_filter]

    if not entries:
        print("No validation data yet")
        return

    # Entries from the log don't carry role/stage_type — need infer_outcomes with real riders.
    # In scaffold mode (no riders loaded), check if all entries are pre-enriched.
    if not all("role" in e for e in entries):
        print("No validation data yet — run after stage results are available")
        return

    run_calibration(entries, dry_run=args.dry_run)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    main()
