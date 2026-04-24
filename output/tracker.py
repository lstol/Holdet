"""
output/tracker.py — Brier score tracking for probability calibration.

Public API:
    record_stage_accuracy(stage_number, probs, actuals, state) -> list[ProbAccuracy]
    format_brier_summary(accuracy_records) -> str
    save_accuracy(records, state) -> dict
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional


# ── Valid inferred scenarios ──────────────────────────────────────────────────

VALID_SCENARIOS: frozenset = frozenset({
    "bunch_sprint",
    "reduced_sprint",
    "breakaway",
    "gc_day",
    "itt",
    "",       # empty string = not yet filled in (acceptable at settle time)
    None,     # None = not yet filled in
})


# ── ProbAccuracy schema ───────────────────────────────────────────────────────

@dataclass
class ProbAccuracy:
    stage: int
    rider_id: str
    event: str              # "win" | "top3" | "top15" | "dnf"
    model_prob: float
    manual_prob: Optional[float]
    actual: float           # 1.0 or 0.0
    model_brier: float      # (model_prob - actual)²
    manual_brier: Optional[float]   # (manual_prob - actual)² or None


# ── record_stage_accuracy ─────────────────────────────────────────────────────

def record_stage_accuracy(
    stage_number: int,
    probs: dict,            # dict[holdet_id, RiderProb]
    actuals: object,        # StageResult
    state: dict,
) -> list[ProbAccuracy]:
    """
    Compute ProbAccuracy for each team rider after a stage settles.

    For each rider × event (win, top3, top15, dnf):
      - model_prob: from probs[rider_id]
      - manual_prob: same value if source=="adjusted", else None
      - actual: 1.0 or 0.0 derived from StageResult
      - model_brier: (model_prob - actual)²
      - manual_brier: (manual_prob - actual)² if manual_prob else None
    """
    my_team: list = state.get("my_team", [])
    records: list[ProbAccuracy] = []

    for rid in my_team:
        rp = probs.get(rid)
        if rp is None:
            continue

        finish_order: list = getattr(actuals, "finish_order", [])
        dnf_riders: list = getattr(actuals, "dnf_riders", [])

        # Derive actuals from StageResult
        position = (finish_order.index(rid) + 1) if rid in finish_order else None
        is_win   = 1.0 if position == 1 else 0.0
        is_top3  = 1.0 if (position is not None and position <= 3) else 0.0
        is_top15 = 1.0 if (position is not None and position <= 15) else 0.0
        is_dnf   = 1.0 if rid in dnf_riders else 0.0

        for event, model_p, actual_v in [
            ("win",   rp.p_win,   is_win),
            ("top3",  rp.p_top3,  is_top3),
            ("top15", rp.p_top15, is_top15),
            ("dnf",   rp.p_dnf,   is_dnf),
        ]:
            manual_p: Optional[float] = None
            if rp.source == "adjusted":
                manual_p = model_p   # model_p already holds the adjusted value

            model_brier = (model_p - actual_v) ** 2
            manual_brier = (manual_p - actual_v) ** 2 if manual_p is not None else None

            records.append(ProbAccuracy(
                stage=stage_number,
                rider_id=rid,
                event=event,
                model_prob=model_p,
                manual_prob=manual_p,
                actual=actual_v,
                model_brier=model_brier,
                manual_brier=manual_brier,
            ))

    return records


# ── format_brier_summary ──────────────────────────────────────────────────────

def format_brier_summary(accuracy_records: list[ProbAccuracy]) -> str:
    """
    Print per-stage and season summary.

    Example output:
      Stage 1 Brier: model=0.142, manual=0.118 ✓ (you beat the model)
      Season (1 stage): model avg=0.142, manual avg=0.118
      You beat the model on 1/1 stages
    """
    if not accuracy_records:
        return "No accuracy records to summarise."

    # Group by stage
    stages: dict[int, list[ProbAccuracy]] = {}
    for rec in accuracy_records:
        stages.setdefault(rec.stage, []).append(rec)

    lines: list[str] = []
    beat_count = 0
    total_stages = len(stages)
    season_model_scores: list[float] = []
    season_manual_scores: list[float] = []

    for stage_num in sorted(stages):
        recs = stages[stage_num]
        model_scores = [r.model_brier for r in recs]
        manual_scores = [r.manual_brier for r in recs if r.manual_brier is not None]

        stage_model_avg = sum(model_scores) / len(model_scores) if model_scores else 0.0
        season_model_scores.extend(model_scores)

        if manual_scores:
            stage_manual_avg = sum(manual_scores) / len(manual_scores)
            season_manual_scores.extend(manual_scores)
            beat = stage_manual_avg < stage_model_avg
            beat_str = " ✓ (you beat the model)" if beat else ""
            if beat:
                beat_count += 1
            lines.append(
                f"Stage {stage_num} Brier: "
                f"model={stage_model_avg:.3f}, "
                f"manual={stage_manual_avg:.3f}"
                f"{beat_str}"
            )
        else:
            lines.append(
                f"Stage {stage_num} Brier: "
                f"model={stage_model_avg:.3f} (no manual overrides)"
            )

    # Season summary
    lines.append("")
    season_model_avg = (
        sum(season_model_scores) / len(season_model_scores)
        if season_model_scores else 0.0
    )
    stage_word = "stage" if total_stages == 1 else "stages"

    if season_manual_scores:
        season_manual_avg = sum(season_manual_scores) / len(season_manual_scores)
        lines.append(
            f"Season ({total_stages} {stage_word}): "
            f"model avg={season_model_avg:.3f}, "
            f"manual avg={season_manual_avg:.3f}"
        )
        lines.append(f"You beat the model on {beat_count}/{total_stages} stages")
    else:
        lines.append(
            f"Season ({total_stages} {stage_word}): "
            f"model avg={season_model_avg:.3f} (no manual overrides)"
        )

    return "\n".join(lines)


# ── save_accuracy ─────────────────────────────────────────────────────────────

def compute_stage_brier(accuracy_records: list[ProbAccuracy]) -> dict:
    """
    Compute average Brier scores for p_win and p_top15 from a stage's accuracy records.

    Returns a dict with keys: brier_p_win, brier_p_top15, n_riders_scored.

    NOTE: Brier score computed on team riders only (n ≈ 8).
    Small sample — treat as directional signal, not calibration ground truth.
    Full-field scoring is deferred to Session 19.
    """
    win_scores = [r.model_brier for r in accuracy_records if r.event == "win"]
    top15_scores = [r.model_brier for r in accuracy_records if r.event == "top15"]
    return {
        "brier_p_win": sum(win_scores) / len(win_scores) if win_scores else float("nan"),
        "brier_p_top15": sum(top15_scores) / len(top15_scores) if top15_scores else float("nan"),
        "n_riders_scored": len(win_scores),
    }


def save_calibration_history(
    stage: int,
    date: str,
    stage_type: str,
    inferred_scenario: Optional[str],
    brier_p_win: float,
    brier_p_top15: float,
    n_riders_scored: int,
    notes: str = "",
    stage_result_type: Optional[str] = None,
    path: str = "data/calibration_history.json",
) -> None:
    """Append one stage calibration entry to calibration_history.json.

    scope is always "team_only" — distinguishes from future full-field Brier (Session 19).
    inferred_scenario must be a VALID_SCENARIOS member or ValueError is raised.
    """
    if inferred_scenario not in VALID_SCENARIOS:
        raise ValueError(
            f"Invalid inferred_scenario {inferred_scenario!r}. "
            f"Valid values: {sorted(s for s in VALID_SCENARIOS if s)}"
        )
    entry = {
        "stage": stage,
        "date": date,
        "stage_type": stage_type,
        "inferred_scenario": inferred_scenario,
        "brier_p_win": round(brier_p_win, 6),
        "brier_p_top15": round(brier_p_top15, 6),
        "n_riders_scored": n_riders_scored,
        "notes": notes,
        "scope": "team_only",
        "stage_result_type": stage_result_type,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    import warnings
    history: list = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                history = json.load(fh)
        except (json.JSONDecodeError, OSError):
            pass

    if any(e.get("stage") == stage for e in history):
        warnings.warn(
            f"Calibration entry for stage {stage} already exists. "
            "Appending anyway for audit trail — check for accidental re-run.",
            stacklevel=2,
        )
        entry["notes"] = (entry.get("notes") or "") + " [RERUN]"

    history.append(entry)
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(history, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def save_accuracy(records: list[ProbAccuracy], state: dict) -> dict:
    """
    Append ProbAccuracy records to state["brier_history"] and return updated state.

    Preserves all other state keys. Creates brier_history if absent.
    """
    history: list = state.get("brier_history", [])
    for rec in records:
        history.append(asdict(rec))
    state["brier_history"] = history
    return state
