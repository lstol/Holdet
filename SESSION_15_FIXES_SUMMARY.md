# Session 15-Fixes Summary — Cache Key, Threshold, Role Precedence, Etapebonus Diagnostics

**Date:** 2026-04-22
**Tests:** 415/415 passing (was 407 at start, +8 new tests)
**Branch:** main

---

## What was fixed

### Fix 1 — Cache key: sort enforced inside `_eval_team`

`scoring/optimizer.py` — `_eval_team()` now always computes:
```python
key = (tuple(sorted(squad_ids)), captain_id)
```
Previously relied on callers passing a pre-sorted tuple (implicit contract). Now the invariant is guaranteed internally regardless of call order. New test `TestEvalCacheKeySorted` verifies that `["r3","r1","r2",...]` and `["r1","r2","r3",...]` produce the same cache entry.

---

### Fix 2 — Double-swap threshold: scale-aware with noise floor

`scoring/optimizer.py` — Added module-level constant:
```python
NOISE_FLOOR = 20_000  # ≈ one position improvement at n=500
```
Both `_eval_swap` and `_try_double_swaps` now use:
```python
threshold = max(0.01 * abs(current_metric), NOISE_FLOOR)
```
Previously `_try_double_swaps` used `0.01 * abs(current_metric)`, which collapses to near-zero when `current_metric` is small (early race, low EV). `_eval_swap` used hard `<= 0` comparisons.

`_eval_swap` gains a `current_metric: float = 0.0` parameter; the greedy loop passes it. Two existing tests updated: `test_balanced_accepts_if_ev_gain_exceeds_threshold` (gain 15k → 50k) and `test_all_in_accepts_any_positive_gain` renamed to `test_all_in_accepts_gain_above_noise_floor` (gain 1 → 25k).

---

### Fix 3 — Role classification: explicit precedence rule

`scoring/probabilities.py` — `_rider_roles()` restructured with a `specialist_assigned` flag:

```
Tier 1: GC — if gc_position ≤ 20 or value > 12M
Tier 2: specialist — probability signal first (p_win > 0.05 on flat)
        → if fires: sets specialist_assigned=True, skips value bracket
        → else: value bracket assigns Sprinter/Climber/Breakaway/Domestique
Tier 3: TT — additive for ITT/TTT if value ≥ 8M
```

Prevents probability and value signals from independently appending the same role. Guarantees no duplicates in any combination of inputs.

---

### Fix 4 — Etapebonus diagnostics in `TeamSimResult`

`scoring/simulator.py` — `TeamSimResult` gains two new fields:
- `etapebonus_ev: float = 0.0` — mean etapebonus across simulations
- `etapebonus_p95: float = 0.0` — 95th percentile etapebonus

`simulate_team()` now tracks `etabonuses = np.empty(n)` alongside `totals`, storing `vd.etapebonus_bank_deposit` each sim.

`api/server.py` — `_serialize_profiles()` exposes both fields per profile:
```json
"etapebonus_ev": 45000,
"etapebonus_p95": 120000
```

`frontend/app/briefing/page.tsx` — `ProfileRec` type updated; "Eta EV" column added to the 4-profile comparison table (yellow-600, shows mean etapebonus for each profile's recommended squad). Immediately reveals whether a team's edge comes from star-power or top-15 clustering.

---

### Fix 5 — Scenario stats renamed to `scenario_priors` (cosmetic)

`api/server.py` key renamed: `"scenario_stats"` → `"scenario_priors"`. Prevents future confusion when realised-scenario tracking is added (which will be `scenario_stats`).

`frontend/app/briefing/page.tsx` — `BriefResult` type updated to `scenario_priors`; display logic updated.

---

## New tests added (+8)

**test_optimizer.py:**
- `TestEvalCacheKeySorted.test_unsorted_and_sorted_ids_hit_same_cache` — different id orderings hit same cache entry; only 1 entry created
- `TestThresholdNoiseFloor.test_noise_floor_constant_defined` — `NOISE_FLOOR == 20_000`
- `TestThresholdNoiseFloor.test_threshold_uses_noise_floor_when_metric_is_zero` — gain=0 rejected, gain=NOISE_FLOOR+1 accepted
- `TestThresholdNoiseFloor.test_threshold_scales_with_metric_above_noise_floor` — metric=5M → threshold=50k; 40k rejected, 60k accepted

**test_probabilities.py:**
- `TestRolePrecedenceAndDuplicates.test_role_prob_overrides_value_for_mid_value_flat_rider` — 6M flat rider with p_win=0.08 → sprinter (not breakaway)
- `TestRolePrecedenceAndDuplicates.test_role_no_duplicates` — exhaustive grid check across all stage types × value brackets × gc positions

**test_simulator.py:**
- `TestEtapebonusDiagnostics.test_etapebonus_ev_positive_for_capable_team` — 8 riders with p_top15=0.65 → etapebonus_ev > 0
- `TestEtapebonusDiagnostics.test_etapebonus_p95_gte_etapebonus_ev` — p95 ≥ mean by definition

---

## Existing tests updated

- `test_balanced_accepts_if_ev_gain_exceeds_threshold`: gain 15k → 50k (must exceed NOISE_FLOOR)
- `test_all_in_accepts_any_positive_gain` → `test_all_in_accepts_gain_above_noise_floor`: gain 1 → 25k
