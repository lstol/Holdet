# Session 17 Fixes Summary — Validation Robustness + Calibration Hardening

**Date:** 2026-04-24
**Tests:** 447/447 passing (was 440 at start, +7 new tests)
**Branch:** main

---

## What was fixed / hardened

### Part A — Validation robustness (`main.py` → `cmd_validate`)

#### A1 — Relative error flag replaces absolute-only threshold

**Before:** a diff was flagged only if `abs(diff) > 1000`.

**After:**
```python
abs_diff = abs(diff)
rel_diff = abs_diff / max(1, abs(actual_delta))
flag = "⚠️" if (abs_diff > 5000 or rel_diff > 0.25) else ""
match = not flag
```

A diff of +5,000 on a +3,000 actual is a 167% relative error — that's a model bug,
not noise. The old threshold would have silently passed it.

**Updated column header and print line** now include a `RelDiff` column:
```
  Rider                      Engine     Actual       Δ  RelDiff  Flag
  Vingegaard                 +92,000   +89,000  -3,000    3.4%  OK
  Merlier                    +45,000   +12,000 -33,000  275.0%  ⚠️
```

#### A2 — Systemic summary block (always printed when riders are scored)

After the per-rider table, a summary block now appears:
```
Validation summary (8 riders scored):
  Mean diff:               -1,250
  Median diff:             -1,000
  Overpredicted riders:  2/8
  Underpredicted riders: 6/8
  ⚠️  Consistent bias detected — check for systemic scoring error
```

The `⚠️  Consistent bias` line fires when `abs(mean_diff) > 3000` — the signal that
the same logic bug is hitting multiple riders in the same direction.

---

### Part B — API snapshot persistence (`main.py`)

#### B1 — Snapshot saved when live riders are fetched

When `cmd_validate` successfully fetches live rider data, it now calls `_save_api_snapshot()`:

```python
_save_api_snapshot(args.stage, live_riders)
# → data/api_snapshots/stage1_riders.json
```

Saved as a pretty-printed JSON list of rider dicts. Lets you re-examine the raw API
response after the fact without another live call.

`data/api_snapshots/` added to `.gitignore` (raw API data, not for version control).
`data/api_snapshots/.gitkeep` committed so the directory exists on fresh clones.

#### B2 — Ownership sanity check

After fetching live riders, `_print_ownership_stats()` prints:

```
# If populated and varies:
  Ownership populated (180 riders): min=0.001  max=0.731  mean=0.124

# If all null:
  Ownership: all null — field not yet live

# If constant (placeholder data):
  ⚠️  Ownership: all riders share value 0.0 — likely placeholder
```

The constant check prevents a silent bad-data path where `popularity` is non-null
but all zeros — which would silently corrupt Session 20 differential picks.

---

### Part D — Calibration history hardening (`output/tracker.py`)

#### D1 — New fields in every calibration entry

`save_calibration_history` now writes three additional fields:

| Field | Value | Why |
|-------|-------|-----|
| `recorded_at` | ISO 8601 UTC timestamp | Audit trail for reruns |
| `scope` | `"team_only"` | Distinguishes from Session 19 full-field Brier |
| `stage_result_type` | `None` or `"ttt"` / `"itt"` / `"road"` | Filled manually for stage-type breakdowns |

**New signature:**
```python
def save_calibration_history(
    stage, date, stage_type, inferred_scenario,
    brier_p_win, brier_p_top15, n_riders_scored,
    notes="",
    stage_result_type=None,    # NEW
    path="data/calibration_history.json",
) -> None:
```

#### D2 — `VALID_SCENARIOS` constraint

Defined at module level:
```python
VALID_SCENARIOS = frozenset({
    "bunch_sprint", "reduced_sprint", "breakaway",
    "gc_day", "itt", "", None,
})
```

`save_calibration_history` now raises `ValueError` on unrecognised scenarios:
```
ValueError: Invalid inferred_scenario 'bunch sprint'.
Valid values: ['breakaway', 'bunch_sprint', 'gc_day', 'itt', 'reduced_sprint']
```

Catches the `"bunch sprint"` vs `"bunch_sprint"` typo class at write time, not weeks later.

#### D3 — Scope comment in `compute_stage_brier`

```python
# NOTE: Brier score computed on team riders only (n ≈ 8).
# Small sample — treat as directional signal, not calibration ground truth.
# Full-field scoring is deferred to Session 19.
```

#### D4 — `calibration` CLI command

```bash
python3 main.py calibration
```

Output:
```
Calibration history (team-only, n≈8 per stage):

  Stage  Type      p_win  p_top15  Scenario        Date
  -----  --------- ------- --------  --------------  ----------
      1  flat      0.0430   0.1180  bunch_sprint    2026-05-09
      2  mountain  0.0310   0.0940  gc_day          2026-05-11

Note: small sample — do not tune until 3+ stages of same type.
```

Read-only — no state writes. Reads `data/calibration_history.json`.

---

### New tests (+7, 440 → 447)

**`tests/test_validate.py` — 3 new tests:**

| Test | What it checks |
|------|---------------|
| `test_relative_error_flag_fires_on_large_pct_diff` | engine win bonus vs small actual → ⚠️ in output |
| `test_systemic_summary_detects_consistent_bias` | 3 overpredicted riders → "Validation summary" printed |
| `test_systemic_summary_prints_even_when_all_match` | summary block present even with no discrepancies |

**`tests/test_tracker.py` — 4 new tests:**

| Test | What it checks |
|------|---------------|
| `test_save_calibration_history_includes_recorded_at` | entry has ISO timestamp `recorded_at` key |
| `test_save_calibration_history_includes_scope` | entry has `scope: "team_only"` |
| `test_save_calibration_history_rejects_invalid_scenario` | `"bunch sprint"` raises `ValueError` |
| `test_save_calibration_history_accepts_none_scenario` | `None` written as JSON `null`, no error |

---

## Key design decisions

- **Relative error threshold is OR, not AND.** Both `abs > 5000` and `rel > 25%` independently
  trigger the flag. This catches both large absolute errors (a rider scored 50k more than reality)
  and large proportional errors (a 3k error on a 3k result).
- **Systemic summary always prints.** Even when all riders match, mean_diff = 0 and the bias
  warning is suppressed. Removing the conditional removes the temptation to skip the summary
  pass by engineering all-match conditions in tests.
- **`scope="team_only"` is hardcoded, not a parameter.** The current function only ever computes
  team-only Brier. When Session 19 adds full-field scoring, it will use a different function or
  an explicit `scope` parameter — not silently reuse this one.
- **Snapshots ignored, not committed.** Raw API responses change every stage and are large.
  `.gitkeep` ensures the directory exists on clone; actual stage files never pollute git history.
