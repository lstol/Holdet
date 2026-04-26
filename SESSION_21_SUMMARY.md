# Session 21 Summary — Unified Probability Shaping Layer + Frontend Fixes

**Date:** 2026-04-26
**Tests:** 510 passing (502 → 510, +8)

## What was built

### Part A — Unified Probability Shaping (`scoring/probability_shaper.py`)

New file implementing the two-layer architecture:

- `ProbabilityContext` dataclass — single source of truth for stage, profiles, roles, adjustments, odds, intelligence, and user weights (stub)
- `STAGE_ROLE_MULTIPLIER` — sprint/climber/gc/breakaway/tt/domestique × stage type matrix
- `_normalize_rp()` — clamp + ordering invariant, called after every layer
- `apply_probability_shaping()` — 6-layer deterministic pipeline returning `(probs, trace)`

**Layers (strict order):**
1. Stage-role compatibility multipliers ← Carapaz fix
2. Rider profiles (consistency + role bias)
3. Intelligence signals (rider-level overrides)
4. Rider confidence adjustments (expert multipliers, was `apply_rider_adjustments`)
5. Odds/market blending (optional)
6. Final normalization + clamp

**Design invariant:**
```
RAW PRIORS → PROBABILITY SHAPER → FINAL PROBS → PURE OPTIMIZER (EV only)
```

### Part B — Optimizer cleanup (`scoring/optimizer.py`)

- Added `DESIGN INVARIANT` module-level comment
- Wired `eval_fn` (partial of `evaluate_action_multistage`) into `_try_double_swaps` — activates when `enable_lookahead=True`
- Changed `n_sim` default from `500` to `None` — resolves via `config.get_n_sim(stages_remaining)` automatically

### Config (`config.py`)

- Added `get_n_sim(stages_remaining) → int`:
  - ≤5 stages → 2000 sims
  - ≤10 stages → 1000 sims
  - else → 500 sims

### API (`api/server.py`)

- Added `_load_profiles()` + `_resolve_profiles()` helpers
- `/brief` endpoint: replaces fragmented `apply_rider_adjustments` / `apply_rider_profiles` with unified `apply_probability_shaping(ctx)`
- `prob_shaping_trace` now in every `/brief` response (model, role, profile, intelligence, odds, user counts)

### CLI (`main.py`)

- `cmd_brief()`: unified shaping via `ProbabilityContext` + `apply_probability_shaping` — identical pipeline to API

### Part C — Frontend fixes

**C1 — Sliders visible before first run:**
- `STAGE_TYPE_DEFAULTS` map initializes `scenarioPriors` when stage is loaded
- `runBriefing()` always updates sliders from API response

**C2 — Slider re-sim without blanking:**
- `reSimulating` state: slider runs keep old table visible with `opacity-50` overlay
- Full runs (`priorsOverride == null`) clear table as before

**C3 — Riders persist across page refresh:**
- `RIDERS_CACHE_KEY` localStorage in both `riders/page.tsx` and `briefing/page.tsx`
- Falls back to cache when Supabase returns empty; shows "Showing cached riders" banner
- Ingest button updates cache on success

**C4 — Tab-switch preserves slider state:**
- Mount `useEffect` restores `scenarioPriors` from cached `briefResult.scenario_priors`

## Files changed

| File | Change |
|------|--------|
| `scoring/probability_shaper.py` | NEW — unified shaping layer |
| `config.py` | Added `get_n_sim()` |
| `scoring/optimizer.py` | DESIGN INVARIANT, eval_fn wiring, n_sim auto-scale |
| `api/server.py` | `_load_profiles`, `ProbabilityContext`, `prob_shaping_trace` |
| `main.py` | `cmd_brief` uses `apply_probability_shaping` |
| `frontend/app/briefing/page.tsx` | C1, C2, C3, C4 |
| `frontend/app/riders/page.tsx` | C3 |
| `tests/test_probability_shaper.py` | NEW — 5 tests |
| `tests/test_optimizer.py` | +3 tests (Session 21 class) |

## Done conditions

- ✅ `prob_shaping_trace` in `/brief` response with `model` and `role` keys
- ✅ Sprint stage: sprinters rank above climbers (STAGE_ROLE_MULTIPLIER applied)
- ✅ `optimizer.py` contains DESIGN INVARIANT comment and zero role/profile/stage logic
- ✅ CLI brief and API `/brief` use identical `ProbabilityContext` pipeline
- ✅ Sliders visible immediately when stage is selected
- ✅ Slider drag re-sims without blanking the recommendations table
- ✅ Riders page shows cached riders on page refresh
- ✅ Tab switch preserves briefing result and slider positions
- ✅ 510 tests passing
