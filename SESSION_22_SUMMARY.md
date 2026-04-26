# Session 22 Summary — Variance-Aware Shaping + Captain System

**Date:** 2026-04-26
**Tests:** 510 → 519 passing (+9)
**Branch:** merged to main

---

## What was built

### Part A — Variance shaping (Layer 3.5)

**`scoring/probability_shaper.py`**

- Added `variance_mode: str = "balanced"` field to `ProbabilityContext` (default: "balanced")
- Inserted Layer 3.5 between intelligence signals (Layer 3) and rider confidence adjustments (Layer 4)
- Stable mode: `p_win *= 0.90`, `p_top15 *= 1.05` — flattens distribution toward consistency
- Aggressive mode: `p_win *= 1.15`, `p_top15 *= 0.95` — amplifies win probability
- Balanced mode: strict no-op (zero riders tagged with variance source)
- `_normalize_rp()` called explicitly after layer (mandatory — same contract as all other layers)
- `prob_shaping_trace` now includes `"variance"` key (count of riders touched)

**`api/server.py`**

- `variance_mode: str = "balanced"` added to `BriefRequest` model
- `ProbabilityContext` constructed with `variance_mode=req.variance_mode or "balanced"`

### Part B — Captain selector module

**`scoring/captain_selector.py`** (new file)

- `LAMBDA = {"stable": 0.0, "balanced": 0.5, "aggressive": 1.5}`
- `PROFILE_VARIANCE_DEFAULT` — suggested frontend pre-fills (never applied automatically)
- `select_captain(team, probs, sim_results, mode)` → `(captain_id, candidates)`
  - Formula: `score = EV + λ * p_win`
  - EV from `sim_results[rid].expected_value` (per-rider Monte Carlo, not re-derived)
  - p_win from final shaped `probs` dict (same probs passed to optimizer)
  - Returns top-5 candidates always, each with `{rider_id, ev, p_win, score}`

**`scoring/optimizer.py`**

- `_pick_captain()` docstring updated: INTERNAL USE ONLY (Session 22+)
- Not deleted — still used during squad search (evaluating team EV requires a captain)

**`api/server.py`**

- `select_captain()` called after `optimize_all_profiles()`, before response construction
- `/brief` response includes:
  - `captain_recommendation: {rider_id, mode}` — the recommended captain by variance mode
  - `captain_candidates: list[{rider_id, ev, p_win, score}]` — always top 5, always returned

---

## Architecture note

The captain module formula `score = EV + λ * p_win` uses probabilities in [0, 1] range.
For modes to differentiate in tests, EV values must be small enough that `λ * p_win` is
non-negligible. In production, both stable and balanced modes will typically converge on the
same rider (highest EV), while aggressive will only diverge when a rider has meaningfully
higher p_win than the EV leader. This is intentional — most variance expression lives in
probability shaping (earlier in the pipeline), not in captain selection.

---

## Files changed

| File | Change |
|------|--------|
| `scoring/probability_shaper.py` | Layer 3.5 + variance_mode field + trace key |
| `scoring/captain_selector.py` | New file |
| `scoring/optimizer.py` | _pick_captain docstring (INTERNAL USE ONLY) |
| `api/server.py` | variance_mode in BriefRequest + ProbabilityContext + captain wiring |
| `tests/test_probability_shaper.py` | +4 variance tests |
| `tests/test_captain_selector.py` | New file, 5 tests |
| `SESSION_22_SUMMARY.md` | This file |
| `SESSION_ROADMAP.md` | Session 22 marked complete, test count updated |

---

## Done condition checklist

- [x] `variance_mode` field on `ProbabilityContext` and `BriefRequest`
- [x] Variance layer inserted at Layer 3.5 (after intelligence, before odds)
- [x] `_normalize_rp()` called explicitly after variance layer
- [x] `prob_shaping_trace` includes `"variance"` count
- [x] `scoring/captain_selector.py` exists with `select_captain()` and `LAMBDA` dict
- [x] `_pick_captain()` in optimizer marked INTERNAL USE ONLY (not deleted)
- [x] `/brief` response contains `captain_recommendation` and `captain_candidates` (always top 5)
- [x] `captain_candidates` includes `ev`, `p_win`, `score` per rider
- [x] `balanced` variance mode is a no-op (probs unchanged)
- [x] All tests pass (519)
- [x] `SESSION_22_SUMMARY.md` created
- [x] `SESSION_ROADMAP.md` updated
- [x] Committed and pushed to main
