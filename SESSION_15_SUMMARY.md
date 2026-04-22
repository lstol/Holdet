# Session 15 Summary — Team-Level Optimizer + Role Display + Probability Distribution

**Date:** 2026-04-22
**Tests:** 407/407 passing (was 391 at start, +16 new tests)
**Branch:** main

---

## What was built

### Part A — Wire optimizer to team-level simulation

**A1 — `simulate_team()` captain fix** (`scoring/simulator.py`)
- `sim_values` changed from `list` to `dict[str, float]` keyed by `rider.holdet_id`
- Captain bonus now credits the *declared* captain: `max(0.0, sim_values.get(captain, 0.0))`
- Previously applied to dynamic best performer — now correctly matches game rules
- Added assertion: captain must be in squad

**A2–A3 — `_eval_team()` + `_team_metric()`** (`scoring/optimizer.py`)
- `_eval_team(squad_ids, captain_id, sim_results, stage, all_riders, probs, n_sim)` — runs full team simulation, memoized on `(squad_ids_tuple, captain_id)`
- `_team_metric(result, profile)` — returns p10 (ANCHOR), EV (BALANCED), p80 (AGGRESSIVE), p95 (ALL_IN)
- Module-level `_eval_cache: dict = {}` cleared at the start of each `optimize()` call

**A4 — Greedy swap wired to team simulation**
- Greedy loop now evaluates each candidate swap via `_eval_team()` instead of per-rider SimResult sum
- Candidate set built via `_build_candidates()`: union of top-25 EV + top-25 p95 riders (capped at 50)
- ANCHOR enforces hard constraint: GC top-10 riders and jersey holders cannot be sold

**A5 — Double-swap exploration**
- `_try_double_swaps(squad, captain, sim_results, stage, all_riders, probs, profile, bank, n_sim)` — samples random sell-pairs from the current squad after greedy convergence
- 1% improvement threshold to filter simulation noise
- ANCHOR GC-protection check applied inside the loop

**A6 — Hybrid EV+p95 candidate pre-filtering**
- `_build_candidates()` takes union of top-25 by EV and top-25 by p95, reducing candidate set to ~50
- Makes O(8×50×n_sim) search tractable

**A7 — `ProfileRecommendation` carries `TeamSimResult`**
- `ProfileRecommendation` dataclass: added `team_result: Optional[TeamSimResult] = field(default=None)`
- `optimize()` and `optimize_all_profiles()` accept `n_sim: int = 500`

**A8 — Scenario stats in `/brief`**
- `scenario_stats = {s: p for s, p in STAGE_SCENARIOS.get(stage.stage_type, [])}` added to API response
- Surfaces scenario weights (e.g. bunch_sprint 65%, breakaway 15%, gc_day 20%) for the stage type

### Part B — Multi-role rider classification

**`_rider_roles(rider, stage, probs=None) → list[str]`** (`scoring/probabilities.py`)
- Returns up to 3 roles (vs single role from `_rider_type()`)
- GC if `gc_position ≤ 20` or `value > 12M`
- Sprinter stacks with GC for `value > 14M` flat; Climber stacks with GC for mountain/hilly `> 14M`
- Mid-value (8–14M) gets Sprinter/Climber standalone based on stage type
- 5–8M → Breakaway (or Sprinter if `p_win > 0.05` flat)
- TT additive for ITT/TTT if `value ≥ 8M`
- Domestique fallback — list is never empty, capped at 3

**Exposed in API** — `team_sims` items now include `roles: list[str]`

### Part C — Frontend

**C2 — Role badges** (`frontend/app/briefing/page.tsx`)
- `RoleBadge` component: colour-coded pills per role
  - GC=indigo, Sprinter=green, Climber=red, Breakaway=yellow, TT=purple, Domestique=zinc
- Shown per rider in the Team Simulation table

**C3 — Distribution bar**
- `DistributionBar` component: 128px inline bar showing p10/p50/EV/p80/p95 markers
- Coloured band (p10→p95): blue-tinted for symmetric distributions, green-tinted for right-skewed (EV > p50)
- Tooltip shows exact formatted values on hover
- Shown per rider in the Team Simulation table alongside `p_positive` column

**C4 — Totals row in profile comparison table**
- 4-profile table now shows: EV · Team EV · Team p10 · Team p80 · Team p95 · Fee · Captain
- `team_ev`, `team_p10`, `team_p80`, `team_p95` sourced from `ProfileRec` (new fields from API)

**C5 — Scenario stats header**
- Single line below suggested-profile reasoning: e.g. "Bunch sprint 65% · Breakaway 15% · Gc day 20%"
- Sourced from `scenario_stats` in `/brief` response

---

## TypeScript types updated

- `ProfileRec`: added `team_ev`, `team_p10`, `team_p80`, `team_p95`
- `TeamSim`: added `team_abbr`, `roles`, `percentile_10`, `percentile_50`, `percentile_80`, `percentile_90`, `percentile_95`, `p_positive`
- `BriefResult`: added `scenario_stats: Record<string, number> | null`

---

## New tests added (+16)

**test_simulator.py:**
- `TestCaptainBonusAppliedToDeclaredCaptain` (3 tests): bonus credited when captain wins, bonus never negative, captain must be in squad
- `TestEvalTeamUsesFullPeloton` (1 test): `_eval_team` passes full rider field
- `TestEvalCacheHit` (1 test): same squad evaluated twice → same object (cache hit)
- `TestOptimizerTeamResultInRecommendation` (1 test): `ProfileRecommendation.team_result` is `TeamSimResult`

**test_probabilities.py:**
- `TestMultiRoleClassification` (10 tests): gc_climber, sprinter_only, gc_no_stage_type_match, tt_specialist_on_itt, tt_stacks_with_gc, roles_never_empty, breakaway_for_mid_value_non_flat, probabilistic_reclassify_to_sprinter, max_three_roles, domestique_for_very_low_value

---

## Key design decisions

- **Declared captain bonus**: credits `max(0, sim_values[captain_id])` — matches Holdet rules (losses not amplified, declared not dynamic)
- **Memoization key**: `(frozenset(squad_ids), captain_id)` — unique per squad+captain combination; cache cleared each `optimize()` call for fresh per-stage evaluation
- **1% improvement threshold in double-swap**: filters simulation noise from small n_sim in tests, controlled by `improvement_threshold=0.01`
- **Test performance**: module-scoped fixtures + `n_sim=10`/`n_sim=50` for tests → 82s for 407 tests (acceptable)
