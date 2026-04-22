# Session 14 Summary — Simulation Layer Rebuild + Probability Improvements

**Date:** 2026-04-22
**Tests:** 391/391 passing (was 363 at start, +28 new tests)
**Branch:** main

---

## What was built

### Part A — Stage-level simulation

**`simulate_stage_outcome(stage, riders, probs, rng) → StageResult`** (`scoring/simulator.py`)
- Samples a stage scenario (bunch_sprint / reduced_sprint / breakaway / gc_day) weighted by stage type
- Weights each rider by their role × scenario multiplier (e.g. SPRINTER ×4.0 in bunch_sprint)
- Produces a coherent finish order via Plackett-Luce sampling (Gumbel-max trick, O(n log n))
- Returns a full `StageResult` with finish positions, times, sprint/KOM points, jersey winners, GC standings

**`simulate_team(team, captain, stage, riders, probs, n=5000, ...) → TeamSimResult`** (`scoring/simulator.py`)
- Runs n coherent stage simulations for all 8 team riders
- Dynamic captain: credits bonus to the best performer in each simulation
- Etapebonus credited once per simulation (nonlinear bank deposit, not per-rider sum)
- Returns `TeamSimResult` with EV / p10 / p50 / p80 / p95

**Old `simulate_team()` renamed to `simulate_all_riders()`** — all callers updated in `api/server.py` and `main.py`.

### Part B — Probability layer improvements

**RiderRole classification** (`scoring/probabilities.py`)
- `_rider_type(rider, stage)` assigns: GC_CONTENDER / SPRINTER / CLIMBER / BREAKAWAY / TT / DOMESTIQUE
- Priority: gc_position ≤ 20 → GC_CONTENDER first; value brackets decide the rest
- Stage-type aware: 8–12M rider is SPRINTER on flat, CLIMBER on mountain/hilly

**Role×stage_type prior matrix** (`ROLE_TOP15`)
- Replaces flat `BASE_TOP15` constant with 6-role × 5-stage-type matrix
- Example: SPRINTER flat=0.45, mountain=0.05; GC_CONTENDER flat=0.15, mountain=0.35

**Tiered attention** in `generate_priors()`
- Top-20 riders by value: full prior (×1.0)
- Ranks 21–50: prior ×0.6
- Ranks 51+: domestique baseline (p_top15=0.02)

**Auto-apply odds**: `generate_priors()` accepts optional `odds_map` and applies odds via lazy import (avoids circular dependency with `odds.py`).

### Optimizer captain selection update (`scoring/optimizer.py`)
- ANCHOR → rider with highest p10 (floor guarantee)
- BALANCED → rider with highest EV
- AGGRESSIVE / ALL_IN → rider with highest p95

---

## Sanity checks

| Check | Result |
|---|---|
| Flat stage: sprinters dominate p95 | ✅ sprinter p95=2,463k > GC p95=1,972k |
| Mountain stage: GC riders dominate EV | ✅ GC EV=2,510k vs SP EV=1,967k |
| Etapebonus visible at team level | ✅ team EV=2,380k >> sum individual EVs=326k |

---

## Key design decisions

- **Gumbel-max trick**: Plackett-Luce sampling via `log(w) + Gumbel(0,1)` noise — vectorized, no duplicates, O(n log n)
- **finish_order capped at top-30**: sufficient for etapebonus (top-15) and team bonus (top-3), reduces scan cost
- **Lazy import for odds**: `from scoring.odds import apply_odds_to_probs` only inside `generate_priors()` when `odds_map` is provided — breaks circular import
- **Optimizer unchanged structurally**: team-level simulation called by API/main, not embedded in greedy swap loop

---

## New tests added (+28)

**test_simulator.py:**
- `TestSimulateStageOutcome` (7 tests): flat/mountain scenario dominance, Plackett-Luce no-duplicates, DNF mask, StageResult fields
- `TestSimulateTeamResult` (7 tests): TeamSimResult fields, etapebonus visibility, captain dynamic selection, percentile ordering

**test_probabilities.py:**
- `TestRiderRoleClassification` (8 tests): gc_position priority, value brackets, stage-type awareness, edge cases
- `TestRoleStageMatrix` (6 tests): generate_priors with role matrix, tiered attention multipliers

**test_optimizer.py:**
- `test_anchor_captain_has_highest_p10`: updated for new ANCHOR → p10 logic
