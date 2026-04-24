# Session 17 Summary — Live Validation + Calibration Baseline + Hardening

**Date:** 2026-04-24
**Tests:** 455/455 passing (was 429 at start of session, +26 total across all patches)
**Branch:** main

---

## Overview

Session 17 covers three phases of work, all completed before Stage 1 runs:

1. **Core build** — `roles` command, `calibration_history.json`, 11 tests
2. **Patch 1** — Relative error flag, systemic summary, API snapshots, calibration hardening, 7 tests
3. **Patch 2** — Snapshot input alignment, calibration idempotency, ownership scale guard, structured validation JSON, 8 tests

Parts A and B (live data) are blocked on Stage 1 finishing (May 9, 2026).

---

## Part 1 — Core build

### `roles` command (`main.py`)

Read-only command that shows how the simulator classifies every rider for a given stage.

```bash
python3 main.py roles --stage N
```

**Output:**
```
Stage 3 (mountain) — Rider Role Classification
────────────────────────────────────────────────────────────────────────────────
Rider                  Roles                  p_top15        Value  Scenario mult
Vingegaard GC#1 [C]    gc_contender, climber     0.35   17,500,000  3.50 (gc_day)
Merlier                sprinter                  0.45   13,200,000  0.20 (gc_day)
```

- Calls `_rider_roles(rider, stage, probs)` per rider
- Dominant scenario = highest-prior scenario for the stage type
- Scenario multiplier = `max(mult_table[role] for role in roles)` under dominant scenario
- Sort: value desc, then p_top15 desc
- Shows `GC#N` and `[C]` markers

### Calibration history (`output/tracker.py`)

**`compute_stage_brier(accuracy_records)`** — averages Brier scores for `p_win` and `p_top15` events independently.

**`save_calibration_history(...)`** — appends to `data/calibration_history.json`:
```json
{
  "stage": 1, "date": "2026-05-09", "stage_type": "flat",
  "inferred_scenario": "bunch_sprint",
  "brier_p_win": 0.043, "brier_p_top15": 0.118,
  "n_riders_scored": 8, "scope": "team_only",
  "recorded_at": "2026-05-09T18:42:00+00:00",
  "stage_result_type": null
}
```

**`calibration` CLI command** — read-only table of history:
```bash
python3 main.py calibration
```

Wired into `cmd_settle` — writes a calibration entry automatically after each stage.

---

## Part 2 — Patch 1: Validation robustness + calibration hardening

### Validation: relative error flag

`cmd_validate` now uses a combined threshold:
```python
flag = "⚠️" if (abs_diff > 5000 or rel_diff > 0.25) else ""
```

A 167% relative error on a small score (e.g. +5k engine vs +3k actual) is a bug
signal — the old `±1000` absolute threshold would have silently passed it.

### Validation: systemic summary block

Always printed when riders are scored:
```
Validation summary (8 riders scored):
  Mean diff:               -1,250
  Median diff:             -1,000
  Overpredicted riders:  2/8
  Underpredicted riders: 6/8
  ⚠️  Consistent bias detected — check for systemic scoring error
```

Bias warning fires when `abs(mean_diff) > 3000`.

### API snapshots (`data/api_snapshots/`)

When `cmd_validate` fetches live rider data, it calls `_save_api_snapshot()`:
```
data/api_snapshots/stage1_riders.json
```

`_print_ownership_stats()` prints summary or warns on null/constant popularity values. `data/api_snapshots/` is gitignored; `.gitkeep` committed.

### Calibration hardening

- **`VALID_SCENARIOS`** frozenset — `save_calibration_history` raises `ValueError` on invalid scenario names (catches `"bunch sprint"` vs `"bunch_sprint"` typos)
- Every entry now has **`recorded_at`** (UTC ISO), **`scope: "team_only"`**, **`stage_result_type`**
- Scope comment added to `compute_stage_brier`

---

## Part 3 — Patch 2: Input alignment, idempotency, ownership scale, structured output

### Fix 1 — `value_snapshot` now stores GC position

`cmd_settle` snapshot changed from `{rid: int}` to `{rid: {value, gc_position, is_out}}`:

```python
value_snapshot = {
    rid: {
        "value":       rider_map[rid].value,
        "gc_position": rider_map[rid].gc_position,
        "is_out":      getattr(rider_map[rid], "is_out", False),
    }
    for rid in my_team if rid in rider_map
}
```

`cmd_validate` reads both the new dict format and the legacy plain-int format gracefully.
Per-rider output now includes a `GC@N` column, directly exposing GC-state mismatches.

**Why this matters for Session 18:** lookahead adds `λ × EV_next`. Wrong GC position in
the base simulator compounds through the lookahead term. Getting baseline state correct
before adding lookahead is the priority.

### Fix 2 — Calibration history: soft deduplicate on re-run

`save_calibration_history` warns but does NOT block on duplicate stage:
```
UserWarning: Calibration entry for stage 1 already exists.
Appending anyway for audit trail — check for accidental re-run.
```

The duplicate entry gets `[RERUN]` appended to `notes`. Session 19's calibration script
filters to `keep="first"` per stage when computing rolling averages.

### Fix 3 — Ownership scale guard (`ingestion/api.py`)

New `normalise_ownership(riders)` function:
- Detects `0–100` scale if `max(popularity) > 1.5`
- Normalises to `0–1` in-place
- Returns `(riders, was_normalised, max_val)`

`_print_ownership_stats` in main.py calls this before printing stats. Prevents silent
bad-data corruption of Session 20 differential picks.

### Fix 4 — Structured validation JSON (`data/validation/`)

After each `validate` run, `data/validation/stage{N}.json` is written:
```json
{
  "stage": 1, "date": "2026-05-09",
  "riders": [
    {"holdet_id": "...", "name": "...", "engine_delta": 92000,
     "actual_delta": 89000, "diff": -3000, "rel_diff": 0.0337,
     "gc_position": null, "flag": false}
  ],
  "summary": {
    "mean_diff": -1250, "median_diff": -1000,
    "overpredicted": 2, "underpredicted": 6,
    "n_scored": 8, "bias": "neutral"
  }
}
```

Session 19's calibration script reads these files directly — no markdown parsing needed.
`data/validation/` gitignored; `.gitkeep` committed.

---

## Live data tasks (blocked until May 9, 2026)

### Part A — `validate --stage 1`

Run after `settle --stage 1`:
```bash
python3 main.py settle  --stage 1
python3 main.py validate --stage 1
```

Fill in `tests/validation_log.md` per-rider table with engine vs Holdet deltas.
Add `### Observations` block and `Inferred scenario:` line.

**Calibration discipline:** do NOT adjust `ROLE_TOP15` or `SCENARIO_MULTIPLIERS` after Stage 1.
- After 3 stages: adjust only if same role is wrong by >15pp in same direction
- After 5 stages: adjust confidently with before/after Brier comparison

### Part B — API probe

After Stage 1, check GC standings and ownership endpoints:
```python
# /api/games/612/standings
resp = session.get("https://nexus-app-fantasy-fargate.holdet.dk/api/games/612/standings")

# popularity field
pops = [(r.name, getattr(r, 'popularity', None)) for r in fetch_riders(...)]
```

Document findings in `API_NOTES.md` using the structured template.

---

## New tests summary (+26 total, 429 → 455)

### Core build (+11)

| File | Tests |
|------|-------|
| `test_validate.py` | log written on mismatch, small diff tolerated, 3+ bias riders shown |
| `test_cli.py` | roles runs, names in output, gc_contender on mountain, multiplier in output |
| `test_tracker.py` | perfect Brier=0, uniform baseline math, calibration file created, required fields |

### Patch 1 (+7)

| File | Tests |
|------|-------|
| `test_validate.py` | rel error flag fires, bias summary, summary always prints |
| `test_tracker.py` | recorded_at present, scope=team_only, invalid scenario rejected, None accepted |

### Patch 2 (+8)

| File | Tests |
|------|-------|
| `test_validate.py` | snapshot stores gc_position, legacy int snapshot reads, JSON written to data/validation/ |
| `test_tracker.py` | warns on duplicate, [RERUN] in notes, write not blocked |
| `test_ingestion.py` | 0-100 scale normalised, 0-1 scale unchanged |

---

## Key design decisions

- **`inferred_scenario` left blank at settle time.** Requires human judgment; auto-filling would be guesswork.
- **Calibration history is append-only.** Reruns are visible in the audit trail rather than silently overwritten.
- **Relative error is OR, not AND.** Both `abs > 5000` and `rel > 25%` independently trigger the flag — catches both large absolute errors and large proportional errors on small scores.
- **Snapshot is now a dict.** Storing `gc_position` alongside `value` prevents invisible GC-state drift between `settle` and `validate` — critical for Session 18 lookahead.
- **Snapshots and validation JSON are gitignored.** Raw API responses and stage records change every race; `.gitkeep` files ensure directories exist on fresh clones.
- **Baseline Brier interpretation.** Naive baseline for `p_win` is `(n-1)/n²` (uniform predictor), not 0.25. For n=8 riders, uniform baseline ≈ 0.109. Model Brier below this = positive skill.
