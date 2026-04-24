# Session 17 Summary — Live Validation + Calibration Baseline

**Date:** 2026-04-24
**Tests:** 440/440 passing (was 429 at start, +11 new tests)
**Branch:** main

---

## What was built

### Part C — `roles` debug command (`main.py`)

New CLI command that shows how the simulator classifies every rider for a given stage.
Read-only — no state writes.

```bash
python3 main.py roles --stage N
```

**Output format:**
```
Stage 3 (mountain) — Rider Role Classification
────────────────────────────────────────────────────────────────────────────────
Rider                  Roles                  p_top15        Value  Scenario mult
────────────────────────────────────────────────────────────────────────────────
Vingegaard GC#1 [C]    gc_contender, climber     0.35   17,500,000  3.50 (gc_day)
Pogacar GC#2           gc_contender, climber     0.35   18,200,000  3.50 (gc_day)
van Aert               gc_contender, sprinter    0.20   15,800,000  3.50 (gc_day)
Merlier                sprinter                  0.45   13,200,000  0.20 (gc_day)
```

**Implementation details:**
- Loads `riders.json`, `stages.json`, and `state.json`
- Calls `generate_priors()` to get model probs, then `_rider_roles(rider, stage, probs)` per rider
- Dominant scenario = highest-prior scenario for the stage type (e.g. `gc_day` at 70% for mountain)
- Scenario multiplier shown = `max(mult_table[role] for role in roles)` under the dominant scenario
- Sort: value descending, then p_top15 descending
- gc_position appended to name if set; `[C]` marker for captain

This command directly exposes why a rider is favoured or suppressed — no guesswork about
how `SCENARIO_MULTIPLIERS` interacts with `_rider_roles`.

---

### Part D — Calibration history (`output/tracker.py` + `main.py`)

#### `compute_stage_brier(accuracy_records)` — new function

Computes per-event average Brier scores from a settled stage's `ProbAccuracy` records.

```python
result = compute_stage_brier(accuracy_records)
# → {"brier_p_win": 0.043, "brier_p_top15": 0.118, "n_riders_scored": 8}
```

Separates `win` and `top15` events and averages their `model_brier` values independently.

#### `save_calibration_history(...)` — new function

Appends one structured entry to `data/calibration_history.json`. Creates the file if absent,
appends otherwise. Atomic write via `.tmp` + `os.replace`.

```json
[
  {
    "stage": 1,
    "date": "2026-05-09",
    "stage_type": "flat",
    "inferred_scenario": "bunch_sprint",
    "brier_p_win": 0.043,
    "brier_p_top15": 0.118,
    "n_riders_scored": 8,
    "notes": "first baseline — small sample"
  }
]
```

#### Wired into `cmd_settle`

After the Brier tracking block in `cmd_settle`, calibration history is automatically appended:

```
Calibration history saved (brier_p_win=0.0430, brier_p_top15=0.1180, n=8)
```

The `inferred_scenario` field is left blank at settle time — it is filled in manually
in `tests/validation_log.md` after reviewing the stage result.

---

### Calibration discipline (no tuning yet)

The session plan is explicit: **do not adjust** `ROLE_TOP15` or `SCENARIO_MULTIPLIERS` after
Stage 1. The calibration history and validation log are for observation only. Tuning rules:

- After 3 stages: adjust only if the same role is wrong by >15pp in the same direction
- After 5 stages: adjust confidently with before/after Brier comparison

---

### Parts A + B — Blocked on live data (May 9, 2026+)

**Part A — `validate --stage 1`:** requires Stage 1 to finish and `settle --stage 1` to have run.
The validation flow is already implemented; the log format and systemic bias check are documented
in `tests/validation_log.md`.

**Part B — API probe (GC standings + ownership):** endpoints were empty pre-race. Run the probe
after Stage 1 to check if `standings` and `popularity` fields are populated. Document findings
in `API_NOTES.md` using the structured template in the session plan.

---

### New tests (+11, 429 → 440)

**`tests/test_validate.py` — 3 new tests:**

| Test | What it checks |
|------|---------------|
| `test_validation_log_written_after_validate` | validate writes to log when mismatch found |
| `test_validation_tolerates_small_diff` | small actual delta does not crash; summary printed |
| `test_validation_flags_systemic_bias` | 3 mismatching riders all appear in output |

**`tests/test_cli.py` — 4 new tests (`TestRolesCommand`):**

| Test | What it checks |
|------|---------------|
| `test_roles_command_runs_without_error` | happy path: no exception |
| `test_roles_command_output_includes_rider_names` | each rider in riders.json in output |
| `test_roles_command_mountain_stage_shows_gc_climber` | high-value rider gets gc_contender or climber on mountain |
| `test_roles_command_shows_scenario_multiplier` | float multiplier + scenario name in output |

**`tests/test_tracker.py` — 4 new tests (`TestComputeStageBrier`):**

| Test | What it checks |
|------|---------------|
| `test_brier_score_perfect_predictor_is_zero` | p_win=1.0 for actual winner → Brier = 0 |
| `test_brier_score_uniform_predictor_matches_naive_baseline` | uniform probs → correct mean |
| `test_brier_stage1_writes_calibration_history` | file created with 1 entry after first call |
| `test_calibration_history_has_required_fields` | all 7 required JSON fields present |

---

## Key design decisions

- **`inferred_scenario` left blank at settle time.** It requires human judgment (did the stage play
  out as a bunch sprint or a breakaway?). Filling it automatically would be guesswork.
- **Calibration history is append-only.** No deduplication — if `settle` is rerun for the same
  stage, a second entry is appended. This is intentional: it makes reruns visible in the history
  rather than silently overwriting.
- **`compute_stage_brier` is separate from `save_calibration_history`.** The two concerns are
  independent: computing scores from records vs. persisting them. Tests can exercise each in isolation.
- **`roles` command reads only — no state writes.** It can be run at any point before or after
  a stage without affecting the settle flow.
- **Baseline interpretation note.** The naive Brier baseline for `p_win` is `(n-1)/n²` (uniform
  predictor), not 0.25 (which applies only to 50/50 binary events). Stage 1 with n=8 riders
  gives a uniform baseline of ~0.109. A model Brier below this means positive skill.
