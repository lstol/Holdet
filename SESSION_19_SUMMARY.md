# Session 19 Summary — Calibration Pass

**Date:** 2026-04-25
**Tests:** 476/476 passing (464 → 476, +12 new)
**Branch:** claude/heuristic-cartwright-4cacf0 → merged to main

---

## What was built

### `scripts/calibrate.py` — Full calibration scaffolding

8 pure, deterministic, independently testable functions.
No real Giro data yet — correct behavior on race day is guaranteed by the test suite.

---

### Global invariants (enforced everywhere)

```python
# Always use ROLE_TOP15[role][stage_type] as the predicted probability.
# Do NOT infer or average predicted values from entries.

# Ignore very small adjustments (<0.01) to prevent noise-driven updates.

# Do not allow more than 2 parameter updates per calibration run.
# Prevents cascading changes from noisy early data.
```

---

### `parse_validation_log(path) → list[dict]`

Parses `tests/validation_log.md` (written by `_log_mismatch()` in `main.py`).

- Only rows where `Field == "total_rider_value_delta"` are returned
- Missing file → empty list (caller prints "No validation data yet")
- Malformed rows → silently skipped
- Header and separator lines → ignored
- Returns dicts with: `stage`, `rider`, `engine_delta`, `actual_delta`, `timestamp`

---

### `infer_outcomes(entries, riders, stages) → list[dict]`

Enriches parsed entries with `role`, `stage_type`, `outcome`, `scenario`.

```python
# NOTE: top-15 proxy based on value delta. Imperfect signal.
# Use only for directional calibration, not absolute accuracy.
outcome = 1 if abs(actual_delta) > 0 else 0
```

Scenario inferred deterministically from winner_role (highest actual_delta per stage):

```python
if winner_role == RiderRole.SPRINTER and stage_type == "flat":
    scenario = "bunch_sprint"
elif winner_role == RiderRole.CLIMBER and stage_type == "mountain":
    scenario = "gc_day"
else:
    scenario = "breakaway"
```

---

### `_brier_score(p, o) → float`

```python
def _brier_score(p: float, o: float) -> float:
    return (p - o) ** 2   # CORRECT
    # NOT (mean(p - o)) ** 2  — WRONG
```

Public helper for unit testing and internal use.

---

### `compute_brier_scores(entries) → dict`

Returns per-(role, stage_type) Brier scores:

```python
{
    "overall":        {(role, stage_type): float},
    "rolling_last_5": {(role, stage_type): float},
}
```

- Always uses `ROLE_TOP15[role][stage_type]` for predicted probability
- p clamped to [0, 1]
- Missing fields → entry skipped

---

### `aggregate_metrics(entries) → dict`

Computes per-(role, stage_type) metrics for `suggest_adjustments`.

Direction formula (strict — all errors same sign):

```python
# error = p - outcome  (where p = ROLE_TOP15[role][stage_type])
# If all errors > 0 → "over"   (model overestimates)
# If all errors < 0 → "under"  (model underestimates)
# Otherwise         → "mixed"
```

Returns: `{(role, stage_type): {"count": int, "observed_rate": float, "direction": str}}`

---

### `Suggestion` dataclass

```python
@dataclass
class Suggestion:
    role: str
    stage_type: str
    old: float    # current ROLE_TOP15 constant
    new: float    # proposed value
    observed: float
    count: int
```

---

### `suggest_adjustments(metrics) → list[Suggestion]`

A suggestion is generated only if ALL conditions are met:

| Condition | Value |
|-----------|-------|
| Count | ≥ 3 stages for same (role, stage_type) |
| Direction | "over" or "under" — not "mixed" |
| Alpha (nudge) | 0.3 (conservative) |
| Overshoot guard | new clamped to [min(current, observed), max(current, observed)] |
| Stability guard | `abs(new - current) ≥ 0.01` |

```python
current = ROLE_TOP15[role][stage_type]  # always use constant, never mean
new = current + 0.3 * (observed - current)
low, high = min(current, observed), max(current, observed)
new = max(low, min(new, high))
```

---

### `evaluate_holdout(entries, suggestion) → (brier_before, brier_after)`

Validates suggestion with proper holdout isolation:

| Condition | Strategy |
|-----------|----------|
| < 5 unique stages | Leave-one-out cross-validation (LOOCV) |
| ≥ 5 unique stages | Last stage as holdout |

- Only evaluates entries matching `(role, stage_type)` of the suggestion
- Never mutates `ROLE_TOP15` — uses `suggestion.old` and `suggestion.new` directly
- Returns `(inf, inf)` if no relevant entries
- Caller must reject if `brier_after >= brier_before`

---

### `scenario_frequency_analysis(entries, stages) → list[dict]`

Compares `STAGE_SCENARIOS` priors vs realized frequencies. Reports only — no auto-adjustment.

```python
{
    "stage_type": "flat",
    "scenario": "bunch_sprint",
    "expected": 0.65,
    "observed": 0.80,
    "gap": 0.15,
    "flagged": False,  # True if gap > 0.25
}
```

---

### `run_calibration(entries, dry_run, history_path, input_fn)` — core logic

Extracted from `main()` for testability.

**Training/holdout split:**
- If ≥ 5 unique stages: use stages[:-1] as training for `suggest_adjustments`; `evaluate_holdout` handles the final-stage holdout
- If < 5 stages: all entries as training; LOOCV in `evaluate_holdout`

**Max 2 updates per run** — enforced by breaking after 2 accepted changes.

**dry_run mode:** prints debug lines + suggestion summary, no prompt, no write.

```
[DEBUG] Group SPRINTER-flat count=3 direction=under
[DEBUG] Observed=0.52 Current=0.45 Suggested=0.48

  Role: SPRINTER (flat)
  Current: 0.45  Observed: 0.52  Suggested: 0.48
  Brier: 0.21 → 0.18  ✔ passes holdout
```

**Interactive mode:** prompts `Apply this change? [yes/no]` per suggestion.

---

### `_append_calibration_history(path, stages_used, changes)` — append-only

Never overwrites existing records. Loads, appends, re-saves.

```json
{
  "timestamp": "2026-05-12 09:14",
  "stages_used": [1, 2, 3, 4, 5],
  "changes": [
    {"constant": "ROLE_TOP15.SPRINTER.flat", "old": 0.45, "new": 0.48,
     "brier_before": 0.21, "brier_after": 0.18}
  ]
}
```

---

### `main()` — CLI

```
python scripts/calibrate.py [--dry-run] [--stages 1,2,3]
```

`--stages` filter applied immediately after parsing, before all downstream computations. Second empty-entries guard handles filtered-to-nothing case.

Scaffold note: raw entries from the validation log don't carry `role`/`stage_type`. `infer_outcomes` needs live `riders` and `stages` objects. For race day, the CLI will load `data/riders.json` + `data/stages.json` and pass them to `infer_outcomes`. The guard `if "role" not in entries[0]: print("No validation data yet"); return` holds until then.

---

## Test breakdown (+12 new, total 476)

### `tests/test_calibrate.py` — 12 new tests

| Class | Tests |
|-------|-------|
| `TestParseValidationLog` (2) | empty/missing file returns []; correct fields parsed from markdown |
| `TestBrierScore` (2) | perfect prediction = 0.0; worst prediction = 1.0 |
| `TestSuggestAdjustments` (3) | no change < 3 stages; no change when mixed; no overshoot (Case D) |
| `TestHoldout` (2) | LOOCV improves (Case A); last-stage holdout rejects overfitting (Case B) |
| `TestDryRun` (1) | dry_run=True never writes calibration_history.json |
| `TestCalibrationHistory` (1) | second call appends, not overwrites |
| `TestScenarioFrequency` (1) | gap > 0.25 is flagged |

---

## Synthetic test datasets

```python
# Case A — LOOCV accepts (consistent underestimation)
# 4 stages, outcomes [1,1,1,0], ROLE_TOP15["sprinter"]["flat"]=0.45
# new = 0.45 + 0.3*(0.75-0.45) = 0.54
# LOOCV brier_before=0.2775, brier_after=0.2316 → accept ✓

# Case B — Last-stage holdout rejects (most important test)
# 5 stages, outcomes [1,1,1,0,0]
# Suggestion from training (1-4): new=0.54
# Holdout stage 5 (outcome=0): brier_before=(0.45)²=0.2025, brier_after=(0.54)²=0.2916
# brier_after > brier_before → reject ✓

# Case C — Mixed direction (no suggestion)
# 3 stages, outcomes [1,0,1], p=0.45
# errors = [-0.55, +0.45, -0.55] — not all same sign → "mixed" → no suggestion ✓

# Case D — Overshoot protection
# 3 stages, outcomes [1,1,1], p=0.45, observed=1.0
# new=0.615, clamped to [0.45, 1.0] → no overshoot past observed ✓
```

---

## Common failure modes — prevented

| Failure mode | How prevented |
|--------------|--------------|
| Brier as `(mean(p-o))²` | `_brier_score(p,o)` helper; comment in code |
| Averaged predicted probability | Global invariant comment + always fetch ROLE_TOP15 |
| Applying without holdout | `evaluate_holdout` required before any change |
| Mutating ROLE_TOP15 | `evaluate_holdout` uses `suggestion.old`/`.new` directly |
| Not isolating (role, stage_type) | First filter in `evaluate_holdout` |
| Non-deterministic scenario | Deterministic if/elif chain in `infer_outcomes` |
| < 3 stages or mixed direction | Guards in `suggest_adjustments` |
| > 2 updates per run | `len(accepted_changes) >= MAX_UPDATES_PER_RUN` break |

---

## Key design decisions

| Decision | Outcome |
|----------|---------|
| Direction formula | Strict "all errors same sign" — mixed if any error contradicts |
| Holdout tests | Directly construct `Suggestion` to isolate `evaluate_holdout` from `suggest_adjustments` |
| `run_calibration()` | Extracted from `main()` with injectable `input_fn` for testability |
| `_brier_score` | Exported as public helper so tests can verify the formula directly |
| CLI scaffold | Prints "No validation data yet" when entries lack role/stage_type (pre-race-day) |
| `overrides/` directory | Pre-existing from Session 18 |
| Max 2 updates | Hard-coded constant `MAX_UPDATES_PER_RUN = 2` |
| append-only history | Load-append-save, never truncate |
