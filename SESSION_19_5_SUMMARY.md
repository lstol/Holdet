# Session 19.5 Summary — Rider-Level Expert Adjustments

**Date:** 2026-04-26
**Tests:** 485/485 passing (477 → 485, +8 new)
**Branch:** claude/heuristic-cartwright-4cacf0 → merged to main

---

## Core principle (enforced in code and comments)

```python
# Rider adjustments are temporary decision inputs, not model updates.
# They must not modify ROLE_TOP15, scenario priors, or calibration outputs.
# scripts/calibrate.py MUST ignore all rider-adjusted probabilities.
```

---

## What was built

### 1. `apply_rider_adjustments()` — `scoring/probabilities.py`

New function. Slots between `interactive_adjust()` and `simulate_all_riders()`.

```python
MAX_RIDER_ADJUSTMENT = 0.30   # ±30% cap — cannot flip probabilities
MAX_ADJUSTED_RIDERS  = 3      # above this, print a warning (do not block)

def apply_rider_adjustments(
    probs: dict[str, RiderProb],
    adjustments: dict[str, float],  # rider_id → multiplier e.g. {"abc": 0.20}
) -> dict[str, RiderProb]:
```

Behaviour:
- Deep-copies input — never mutates the original probs dict
- Clamps multiplier to `[-0.30, +0.30]` before applying
- Applies same multiplier to `p_win`, `p_top3`, `p_top10`, `p_top15`
- Stores base value in `rp.manual_overrides["rca_p_win"]` (rca_ prefix = rider confidence adjustment)
- Updates `rp.source` via set union: `"+".join(sorted(sources | {"user"}))` — prevents `model+user+user`
- Prints `[WARNING]` if more than 3 riders adjusted (does not block)
- Rider IDs not in `probs` are silently skipped

**Simplification note (documented in code):**
All four probability fields receive the same multiplier. A field-weighted `adjustment_profile` (e.g. sprint signal only affects p_win/p_top3) is a future upgrade — not needed now.

**No re-normalization note (documented in code):**
Probability mass increases when multiple riders are boosted. Acceptable because simulation samples outcomes rather than treating these as a strict distribution.

---

### 2. Pipeline insertion — `main.py`

```python
# 2. Optional odds input, then interactive adjustment
probs = interactive_adjust(probs, stage, riders)

# 2b. Apply stored rider confidence adjustments (ephemeral — not persisted to calibration)
adjustments = state.get("rider_adjustments", {}).get(str(args.stage), {})
if adjustments:
    probs = apply_rider_adjustments(probs, adjustments)

# 3. Simulate team only (fast preview)
```

---

### 3. State storage — `state.json`

Per-stage, under `"rider_adjustments"`:

```json
"rider_adjustments": {
  "3": {"rider_id_123": 0.20, "rider_id_456": -0.10}
}
```

Stage key is a string (consistent with `probs_by_stage`). Written atomically via `_save_state`.

---

### 4. `adjust` subcommand — `main.py`

```
python3 main.py adjust --stage 3 --rider "Merlier" --pct 20
python3 main.py adjust --stage 3 --rider "Vingegaard" --pct -10
python3 main.py adjust --stage 3 --clear
python3 main.py adjust --stage 3 --list
```

Behaviour:

| Flag | Action |
|------|--------|
| `--rider X --pct N` | Fuzzy-match rider name, store `N/100` multiplier. Prints overwrite notice if replacing existing. |
| `--list` | Print current adjustments or "No adjustments set for stage N" |
| `--clear` | Remove all adjustments for this stage from state |

Fuzzy match reuses `_find_rider()` from `scoring/probabilities.py` (same as `interactive_adjust`).

Output for `--list`:
```
  Stage 3 adjustments:
    Merlier      +20%
    Vingegaard   -10%
```

Output for overwrite:
```
Overwriting Merlier: +15% → +20%
```

---

### 5. Briefing transparency — `output/report.py`

New section "RIDER CONFIDENCE ADJUSTMENTS" rendered after the probability table when any rider has `rca_p_win` in `manual_overrides`:

```
RIDER CONFIDENCE ADJUSTMENTS:
  Rider: Merlier
    Base P(win): 0.28  →  Adjusted: 0.34  (+20% manual)
    Source: model+user

  Rider: Vingegaard
    Base P(win): 0.18  →  Adjusted: 0.16  (−10% manual)
    Source: model+user
```

Silent when no riders adjusted. Never hides adjustments.

---

### 6. Calibration firewall — `scripts/calibrate.py`

Added to `infer_outcomes()` docstring:

```python
# CALIBRATION FIREWALL:
# This function uses actual_delta from validation_log.md only.
# It must never receive rider-adjusted probabilities as input.
# apply_rider_adjustments() results are ephemeral — not stored in validation_log.
# Calibration always computes Brier against ROLE_TOP15[role][stage_type], not adjusted p.
```

The firewall is structural — `infer_outcomes` only receives validation log entries, which never contain adjusted probabilities. The comment makes the guarantee explicit.

---

## Test breakdown (+8 new, total 485)

### `tests/test_rider_adjustments.py` — 8 new tests

| Test | What it verifies |
|------|-----------------|
| `test_rider_adjustment_applies_multiplier_correctly` | +20% on p_win=0.28 → 0.336 exactly |
| `test_rider_adjustment_clamped_to_bounds` | mult=0.50 → clamped to 0.30 before applying |
| `test_zero_adjustment_no_change` | mult=0.0 → probs identical to input |
| `test_adjustments_do_not_mutate_base_probs` | input dict and `manual_overrides` unchanged |
| `test_source_string_no_duplicates` | double-apply yields `"model+user"` not `"model+user+user"` |
| `test_calibration_ignores_adjusted_probs` | `compute_brier_scores` uses ROLE_TOP15, not adjusted p |
| `test_extreme_adjustment_does_not_break_simulation` | +30% all riders → all probs ∈ [0,1], sim completes |
| `test_warning_printed_when_too_many_riders_adjusted` | 4 riders → `[WARNING]` in stdout |

---

## Key design decisions

| Decision | Outcome |
|----------|---------|
| Immutability | `apply_rider_adjustments` deep-copies — never mutates input |
| Source deduplication | Set union before join — `model+user` not `model+user+user` |
| rca_ prefix | Distinguishes rider confidence adjustments from odds/interactive overrides |
| Same multiplier for all fields | Simplification; field-weighted profile is future work (comment in code) |
| No normalization | Acceptable for sampling-based simulation; comment in code |
| MAX_ADJUSTED_RIDERS=3 | Warning threshold, not a hard limit — don't block during race |
| Ephemeral adjustments | Not persisted to validation_log; calibration firewall is structural |
| `--list` always added | Needed immediately during race — trivial now, annoying mid-race |

---

## Files changed

| File | Change |
|------|--------|
| `scoring/probabilities.py` | `apply_rider_adjustments()`, `MAX_RIDER_ADJUSTMENT`, `MAX_ADJUSTED_RIDERS` |
| `main.py` | Import, `cmd_brief` wiring (step 2b), `cmd_adjust()`, argparse |
| `output/report.py` | "RIDER CONFIDENCE ADJUSTMENTS" section in `format_briefing()` |
| `scripts/calibrate.py` | Calibration firewall comment in `infer_outcomes()` |
| `tests/test_rider_adjustments.py` | 8 new tests |
| `SESSION_19_5_SUMMARY.md` | This file |
| `SESSION_ROADMAP.md` | Session 19.5 added and marked complete |
