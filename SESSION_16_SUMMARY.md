# Session 16 Summary — Scenario-Aware Simulation + Override + Multi-Stage Architecture Scaffold

**Date:** 2026-04-24
**Tests:** 429/429 passing (was 415 at start, +14 new tests)
**Branch:** main

---

## What was built

### Pre-flight bug fix — Diff-based transfer reporting (`scoring/optimizer.py`)

**The bug:** Optimizer accumulated transfers incrementally (fill buys, then swap sells/buys).
With `my_team=[]` (Stage 1), this produced nonsensical output like "buy 10, sell 2" even though
the final squad is 8 riders — the 2 fill riders that were later swapped out appeared as phantom sells.

**The fix:** After forced sells (Step 1), snapshot `input_squad`. Remove all `transfers.append()`
calls from Steps 2–5 (fill, greedy swap, double-swap, fallback fill). After Step 5, compute a
clean diff between `input_squad` and `active_squad` — only record what actually changed vs what
the user owned.

**Correct behaviour:**
- Stage 1 (empty team): all profiles show buy 8, sell 0
- Stage 2+ (8 owned riders): sells reference only riders actually in `my_team`
- Net squad always = 8 after applying transfers

---

### Part A — Scenario override plumbing (`scoring/simulator.py`)

**`_normalize_scenarios(d)` + `_resolve_scenarios(stage, override)`**
- `_normalize_scenarios`: normalises a weights dict to sum 1
- `_resolve_scenarios`: returns stage-type defaults merged with optional partial override, then normalised
- Invalid override keys raise `ValueError` immediately

**`_sample_scenario(scenarios: dict, rng)`**
- Replaced the old `_sample_scenario(stage_type, rng)` which took a string
- Now accepts a normalised weights dict — works with resolved overrides directly

**`simulate_stage_outcome()` signature extended**
```python
def simulate_stage_outcome(stage, riders, probs, rng,
                           scenario: str | None = None,
                           roles_map: dict | None = None) -> StageResult
```
- If `scenario` is provided (from `simulate_team`), skip internal sampling
- Backward-compatible: standalone calls still sample internally

**`simulate_team()` signature extended**
```python
def simulate_team(..., scenario_priors: dict | None = None) -> TeamSimResult
```
- Resolves scenario weights once before the loop: `_resolve_scenarios(stage, scenario_priors)`
- Samples one scenario per simulation: `scenario = _sample_scenario(scenarios, rng)`
- Passes scenario string to `simulate_stage_outcome` — single latent state per sim
- Tracks `scenario_counts: dict[str, int]` throughout the loop

**`TeamSimResult.scenario_stats` field added**
- Populated after loop: `{scenario: count/n for scenario, count in scenario_counts.items()}`
- Realized frequencies ready for comparison against priors

---

### Part B — Multi-role upgrade in `_build_weights()` (`scoring/simulator.py`)

**Before:** used `_rider_type(rider, stage)` → single role → single multiplier

**After:** uses `roles_map[rider.holdet_id]` → list of roles → `max(mult_table[role] for role in roles)`

```python
def _build_weights(riders, probs, stage, scenario, dnf_mask, roles_map=None) -> np.ndarray
```

**Pre-computed `roles_map` in `simulate_team()`:**
```python
roles_map = {r.holdet_id: _rider_roles(r, stage, probs) for r in riders}
```
Computed once before the loop — `_rider_roles()` is deterministic for a given
(rider, stage, probs) triple and must not be called N×n times inside the inner loop.

`_rider_type()` is no longer called from `_build_weights` after this change.

---

### Parts C+D — API changes (`api/server.py`)

**`BriefRequest` model:**
```python
scenario_priors: Optional[dict] = None  # partial override of stage-type scenario weights
```

**`_resolve_scenarios` imported** and called at request time to get normalized weights.

**`optimize_all_profiles()` + `optimize()` + `_eval_team()`** all accept `scenario_priors` and
thread it all the way down to `simulate_team()`. The scenario distribution used for evaluation
matches what the user requested.

**Response now includes:**
```json
{
  "scenario_priors": {"bunch_sprint": 0.6571, "reduced_sprint": 0.2, "breakaway": 0.1429},
  "scenario_stats":  {"bunch_sprint": 0.64, "reduced_sprint": 0.19, "breakaway": 0.17}
}
```
`scenario_priors` = resolved/normalized weights (before simulation).
`scenario_stats` = realized frequencies from BALANCED profile's team simulation.

---

### Part E — Frontend (`frontend/app/briefing/page.tsx`)

**`team_note` yellow banner:**
- Displayed above the profile table when `briefResult.team_note` is set
- API sends this when `my_team` is empty: "No team picked yet — showing best team to select from scratch."
- Uses `AlertTriangle` icon with yellow styling

**Scenario sliders:**
- Appear after the first `Run Briefing` (initialized from API's `scenario_priors` response)
- One slider per scenario key (e.g. bunch_sprint, reduced_sprint, breakaway)
- Proportional normalization: adjusting one slider rescales all others so sum stays 100%
- 500ms debounce: changing a slider triggers `/brief` recompute after 500ms of inactivity
- Frontend sends priors as fractions (÷100) to the API

**Prior vs realized display:**
```
Priors:   bunch sprint 66% · reduced sprint 20% · breakaway 14%
Realized: bunch sprint 64% · reduced sprint 19% · breakaway 17%
```

**TypeScript types updated:**
- `BriefResult`: added `scenario_stats: Record<string, number> | null`, `team_note: string | null`

---

### Part F — New tests (+14)

**`test_simulator.py` — `TestScenarioResolution` (6 tests):**
- `test_normalize_sums_to_one` — weights sum to 1 after normalize
- `test_normalize_preserves_ratios` — relative weights preserved
- `test_resolve_returns_normalized_defaults` — no override case normalises defaults
- `test_override_replaces_default` — breakaway override > bunch_sprint
- `test_override_still_sums_to_one` — override + normalize sum check
- `test_invalid_key_raises` — `ValueError` on unknown scenario key

**`test_simulator.py` — `TestScenarioStats` (4 tests):**
- `test_scenario_stats_sum_to_one` — realized frequencies sum to ~1
- `test_scenario_stats_keys_match_stage_type` — keys match `STAGE_SCENARIOS[stage_type]`
- `test_scenario_stats_reflect_override` — breakaway override visible in realized stats
- `test_simulate_team_accepts_scenario_priors` — signature smoke test

**`test_simulator.py` — `TestScenarioPipelineEffect` (1 test — F6, most important):**
- `test_breakaway_prior_increases_breakaway_rider_ev` — end-to-end pipeline validation:
  breakaway rider on hilly stage gets higher team EV when breakaway prior is high vs low

**`test_optimizer.py` — `TestDiffBasedTransferReporting` (3 tests):**
- `test_stage1_empty_team_shows_8_buys_0_sells` — empty team → buy 8, sell 0
- `test_stage2_sells_reference_only_owned_riders` — sells only reference riders in `my_team`
- `test_net_squad_always_8` — all 4 profiles produce net squad of exactly 8 riders

---

### Part G — Documentation

**`docs/MULTI_STAGE_ARCHITECTURE.md`** — scaffold only, not implemented:
- State definition (`RaceState`)
- Action definition (`Action`)
- Transition function signature (`apply_action`)
- Stage value (already implemented via `simulate_team`)
- Future value: Phase 1 (1-stage lookahead) and Phase 2 (Monte Carlo rollout)
- Evaluation function signature (`evaluate_action_multistage`)
- Design principles (simulation separate from optimizer, reuse current optimizer as rollout policy)
- Explicit blockers: probability calibration, scenario correctness, action space size, no stage-to-stage state update

---

## Key design decisions

- **Scenario as single latent state:** scenario string passed from `simulate_team` → `simulate_stage_outcome` → `_build_weights`. NOT re-encoded as a weights dict (would double-apply the scenario effect).
- **roles_map pre-computed outside inner loop:** `_rider_roles()` is called once per `simulate_team()` call, not once per simulation. Deterministic given (rider, stage, probs).
- **Partial override normalisation:** override keys are merged into stage defaults and renormalised — user says "set breakaway to 0.90", not "breakaway = 90% of all simulations" (since defaults stay in the mix).
- **scenario_stats from BALANCED profile:** API uses BALANCED's `team_result.scenario_stats` as the realized frequency source, since BALANCED is the most neutral reference.
- **`_rider_type` no longer called from `_build_weights`** after the multi-role upgrade — verified by code inspection.
