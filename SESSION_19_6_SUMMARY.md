# SESSION_19_6_SUMMARY.md — Rider Identity Stabilization Layer

**Date:** 2026-04-26
**Tests:** 491 passing (+6 new, up from 485)

---

## What was built

### 1. `scoring/rider_profiles.py` (new)
- `RiderProfile` dataclass with `sprint_bias`, `gc_bias`, `climb_bias`, `consistency`
- Constants: `MAX_BIAS=1.15`, `MIN_BIAS=0.85`, `MAX_CONSISTENCY=1.20`, `MIN_CONSISTENCY=0.80`
- `clamp()` method enforces all bounds in-place

### 2. `config.py`
- Added `get_rider_profiles_path()` → defaults to `data/rider_profiles.json`

### 3. `scoring/probabilities.py`
- Added `apply_rider_profiles(probs, profiles, role_map)`:
  - Role bias applied to `p_win` only (sprint/gc/climb depending on role)
  - Consistency applied uniformly to all four probability fields
  - Clamp + ordering enforcement (`p_win ≤ p_top3 ≤ p_top10 ≤ p_top15`) after each rider
  - Source deduplication via set-union (same pattern as `apply_rider_adjustments`)
  - Does not mutate input dict

### 4. `main.py`
- Added `_resolve_profiles(profiles_raw, riders)` — fuzzy name fragment → holdet_id matcher
- Wired pipeline step 2c in `cmd_brief()`: after `apply_rider_adjustments`, before simulation
- Profiles only applied when `data/rider_profiles.json` exists and is non-empty

### 5. `scripts/init_rider_profiles.py` (new)
- Seeds `data/rider_profiles.json` from rule-based type defaults (no data fitting)
- Uses stage 1 (flat) as canonical seed stage for `_rider_type()` classification
- Type → profile defaults table:

| Type          | sprint_bias | gc_bias | climb_bias | consistency |
|---------------|-------------|---------|------------|-------------|
| SPRINTER      | 1.10        | 0.95    | 0.90       | 0.95        |
| GC_CONTENDER  | 0.95        | 1.10    | 1.05       | 1.05        |
| CLIMBER       | 0.90        | 1.05    | 1.10       | 1.00        |
| BREAKAWAY     | 1.00        | 0.95    | 1.00       | 0.90        |
| DOMESTIQUE    | 0.95        | 0.95    | 0.95       | 0.85        |
| TT            | 0.95        | 1.05    | 0.95       | 1.00        |

### 6. `data/rider_profiles.json`
- Initial seed with merlier, vingegaard, garbich as documented examples

### 7. `scripts/calibrate.py`
- Added CALIBRATION FIREWALL EXTENSION comment to `infer_outcomes()` docstring

---

## Core principle (enforced in code and comments)

> Rider profiles are structural bias signals, not learned parameters.
> They do not update from outcomes, calibration, or odds.
> They are static multipliers applied AFTER user adjustments, BEFORE simulation.
> They MUST NOT affect ROLE_TOP15 or calibration outputs.

---

## Design decisions

- **Consistency applied uniformly to all four fields** — applying only to p_win can cause `p_win > p_top3` at max bias values; uniform application + ordering enforcement is cleaner.
- **Ordering re-enforced after clamping** — guarantees `p_win ≤ p_top3 ≤ p_top10 ≤ p_top15` regardless of bias magnitudes.
- **No profile = neutral** — missing profile silently treated as all-1.0 multipliers. No error.
- **Profiles stored in data/ not state/** — matches all other data files; no auto-update path.

---

## Tests added

| Test | What it verifies |
|------|-----------------|
| `test_rider_profile_applies_sprint_bias` | sprint_bias=1.10 increases p_win proportionally |
| `test_rider_profile_consistency_reduces_all_fields` | consistency=0.90 reduces all fields; ordering holds |
| `test_profile_does_not_modify_role_top15` | ROLE_TOP15 dict unchanged after call |
| `test_profile_pipeline_order_correct` | adjustments run first (rca_p_win present); source has both "user" and "profile" |
| `test_missing_profile_is_no_op` | rider not in profiles → probs identical, no crash |
| `test_profile_source_tagging_no_duplicates` | calling twice yields "model+profile" not "model+profile+profile" |
