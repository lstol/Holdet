# Session 22.5 Summary — Backend Transparency & Traceability Layer

**Date:** 2026-04-26
**Tests:** 519 → 526 passing (+7)
**Branch:** merged to main

---

## What was built

Pure observability and reproducibility layer. No scoring logic, simulation outputs,
probability model, or captain selection behaviour was changed.

### Part A — DecisionTrace (`scoring/decision_trace.py`)

- `DecisionTrace` dataclass: 7 fields per rider
  - `base_ev`: EV from raw priors, no shaping applied
  - `probability_adjustment`: `EV_full - EV_no_prob_shaping` (marginal, not additive)
  - `variance_adjustment`: `EV_full - EV_no_variance` (Layer 3.5 isolated)
  - `intent_adjustment`: hardcoded `0.0` — reserved for Session 23
  - `lookahead_adjustment`: `0.0` when `enable_lookahead=False`
  - `final_ev`: fully shaped EV (same as optimizer input)

### Part B — Ablation framework (`scoring/decision_trace.py`)

- `ablation_run(riders, stage, raw_probs, ctx, component_flags, seed=42)`
  - Re-runs `simulate_all_riders()` with one shaping component toggled off
  - Always uses `my_team=[], captain=""` — captain logic excluded entirely
  - All context modifications via `dataclasses.replace()` — original ctx never mutated
  - Seed=42 contract: same as `_eval_team()` → deltas are comparable and reproducible
  - Flags: `"variance"`, `"intelligence"`, `"prob_shaping"`, `"lookahead"`
- `build_decision_traces(riders, stage, raw_probs, ctx, ev_full, seed=42)`
  - Runs base + variance ablations then builds one `DecisionTrace` per rider in ev_full

### Part C — Contributor schema (`scoring/decision_trace.py`)

- `ALLOWED_EFFECT_ENUMS = {"team_bonus", "transfer_gain", "role_penalty"}`
- `validate_contributor_label(label, rider_names, scenario_keys)` — raises on invalid labels
- `build_contributors(my_team, sim_results, rider_names, scenario_stats, scenario_priors)`
  - `rider_contributors`: top-3 by clipped EV, shares sum to 1.0
  - `scenario_contributions`: only when `scenario_priors` is non-null (omitted entirely otherwise)
  - All labels validated before inclusion — no silent omissions

### Part D+E — Captain trace and flip threshold (`scoring/captain_selector.py`)

- `select_captain()` now returns 4-tuple: `(captain_id, candidates, captain_trace, flip_threshold)`
- `captain_trace`: exactly `{mode, lambda, ev_component, p_win_component, final_score}`
  - `final_score == ev_component + p_win_component` (exact analytic equality)
- `flip_threshold`: `{score_gap, interpretation}` for top-2 candidates
  - `D = (EV_A - EV_B) + λ(P_A - P_B)`; A wins when D > 0
  - Omitted (`None`) when team has only one candidate

### Part F — CLI flag (`main.py`)

- `--trace-level off|minimal|full` on `brief` subcommand (default: `off`)
- `off`: unchanged behaviour
- `minimal`: captain trace + flip threshold one-liners
- `full`: top-10 riders with base_ev, prob_adj, var_adj, final_ev columns

### Part G — API extension (`api/server.py`)

- `/brief` response now includes:
  ```json
  "decision_trace": {
    "riders": { ... },        // all riders in sim_results
    "captain_trace": { ... },
    "flip_threshold": { ... },
    "contributors": { ... },
    "trace_version": "22.5"   // sentinel for Session 22.6 frontend rendering
  }
  ```
- `trace_version: "22.5"` present in every `/brief` response

---

## Design invariants confirmed

- **Non-additivity**: Each adjustment is a marginal ablation effect. `base_ev + sum(adjustments) ≠ final_ev`. This is correct — not tested.
- **intent_adjustment = 0.0**: Hardcoded. `apply_intent_to_ev()` is NOT called even though it's in scope. Session 23 baseline numbers depend on this.
- **Captain excluded from ablation**: `my_team=[], captain=""` in all ablation runs. Verified by test.
- **No optimizer re-run**: Ablation operates at simulation level only. No squad search.

---

## Files changed

| File | Change |
|------|--------|
| `scoring/decision_trace.py` | New file |
| `scoring/captain_selector.py` | Extended to 4-tuple return + captain_trace + flip_threshold |
| `api/server.py` | decision_trace block + trace_version in /brief response |
| `main.py` | `--trace-level` argument + trace output in cmd_brief |
| `tests/test_decision_trace.py` | New file, 7 tests |
| `tests/test_captain_selector.py` | Updated to unpack 4-tuple |
| `SESSION_22.5_SUMMARY.md` | This file |
| `SESSION_ROADMAP.md` | Session 22.5 marked complete, test count updated |

---

## Done condition checklist

- [x] `scoring/decision_trace.py` exists with all required components
- [x] `ablation_run()` operates at simulation level only — does NOT re-run optimizer
- [x] `ablation_run()` excludes captain logic entirely (my_team=[], captain="")
- [x] All ablation runs use seed=42
- [x] Ablation deltas are marginal effects — no additive reconstruction asserted
- [x] `intent_adjustment` is hardcoded 0.0
- [x] `lookahead_adjustment` is 0.0 (enable_lookahead=False default)
- [x] `captain_trace` has exactly 3 numeric fields; `final_score` is exact sum
- [x] `flip_threshold` returns `score_gap` and `interpretation` only
- [x] `validate_contributor_label()` raises on invalid labels
- [x] `scenario_contributions` omitted when `scenario_priors` is null
- [x] No free-text strings in `decision_trace` API output
- [x] `trace_version: "22.5"` present in every `/brief` response
- [x] `--trace-level` CLI flag added; `off` is default
- [x] 7 new tests pass (526 total)
- [x] No changes to optimizer.py logic, probability_shaper.py layers, or simulator.py
- [x] `SESSION_22.5_SUMMARY.md` created
- [x] `SESSION_ROADMAP.md` updated
- [x] Committed and pushed to main
