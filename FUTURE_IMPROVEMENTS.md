# FUTURE_IMPROVEMENTS.md — Backlog & Deferred Ideas
# Items here are not broken, not urgent, but worth doing when the time is right.
# Add new items with a date and brief context. Remove when implemented.
# Last updated: 2026-04-22

---

## Simulation & Optimizer

### NOISE_FLOOR should scale with n_sim
**Added:** 2026-04-22
**Context:** `NOISE_FLOOR = 20_000` implicitly encodes "typical noise at n=500 sims".
If `n_sim` is later increased to 1000+, the threshold becomes too conservative.
**Fix when needed:**
```python
NOISE_FLOOR = BASE_NOISE / sqrt(n_sim)  # BASE_NOISE ≈ 450_000 to match current behaviour
```
Don't over-engineer until n_sim actually changes.

---

### Double-swap sampling should be probability-weighted
**Added:** 2026-04-22
**Context:** `random.sample(eligible_not_in_squad, 2)` uses uniform sampling.
This misses strong synergy pairs among high-p95 riders.
**Fix when needed:**
```python
buy_pair ~ weighted_sample(eligible, weights=[sim_results[r].percentile_95 ...], k=2)
```
Even slight weighting improves discovery of ceiling combinations. Cheap upgrade.

---

### Etapebonus overfitting watch
**Added:** 2026-04-22
**Context:** Etapebonus is now visible in diagnostics and influences optimizer implicitly via
team-level simulation. Risk: optimizer may over-stack mid-tier riders chasing etapebonus
at the expense of star power.
**Sanity check to run periodically:**
- Team A: 8 riders with p_top15 ≈ 0.6 each
- Team B: 3 superstars (p_win > 0.20) + 5 fillers
- Both strategies should sometimes win depending on profile. If Team A always dominates
  under ALL profiles → etapebonus is overweighted.

---

### Conditional captain selection from team simulations
**Added:** 2026-04-22
**Context:** Captain is currently selected deterministically from individual `sim_results`
(p10/EV/p95 per profile). This is fast and good enough, but slightly misaligned —
a rider with high individual p95 may not align with team outcomes in the same sims.
**Future fix:** Inside `_eval_team`, simulate each squad member as captain, pick best
per distribution. Expensive — only worth doing if captain choice turns out to matter
significantly in live validation.

---

### Share simulations across profiles
**Added:** 2026-04-22
**Context:** All 4 profiles currently run independent `_eval_team` calls. The same squad
evaluated under ANCHOR, BALANCED, AGGRESSIVE, ALL_IN runs 4 × 500 = 2000 sims.
**Fix:** Run one set of 500 sims per squad, extract p10/EV/p80/p95 from the same
distribution. Reduces optimizer cost by ~4×. Worth doing when n_sim increases.

---

## Probability & Role System

### Role debug view
**Added:** 2026-04-22
**Context:** Roles now drive frontend display, probability adjustments, and scenario
multipliers. A misclassified rider shifts the whole model. Need a quick way to audit.
**Fix:** Add CLI command or briefing section:
```
python3 main.py roles --stage N
```
Output: rider → role(s) → p_win / p_top15 / p_dnf — so misclassifications are obvious.

---

### Odds-based role override
**Added:** 2026-04-22
**Context:** Role classification uses value brackets + one p_win probabilistic hook.
Better signal: if bookmaker win odds for a flat stage are below X, rider is clearly a
sprinter regardless of value. Not wired yet.
**Fix:** In `_rider_roles()`, accept odds map and apply:
```python
if odds_map and odds_map.get(rider.holdet_id, {}).get("p_win", 0) > 0.08:
    # strong odds signal overrides value bracket
```

---

## Scenario System

### User-controlled scenario override (highest ROI)
**Added:** 2026-04-22
**Context:** `scenario_priors` is now exposed in the API. The full pipeline
(priors → simulator → optimizer) is in place. A user override of scenario weights
will now propagate correctly end-to-end.
**What to build:**
- API: accept optional `scenario_override` dict in `/brief` request body
- Simulator: use override weights instead of `STAGE_SCENARIOS[stage_type]` when present
- Frontend: sliders on briefing page for each scenario (bunch sprint / breakaway / gc day)
**ROI:** High. Lets user encode race-day intelligence ("breakaway more likely today —
headwinds, aggressive teams") and have it affect the full model output.

---

### Scenario-conditioned multiplier tuning
**Added:** 2026-04-22
**Context:** `SCENARIO_MULTIPLIERS` in `simulator.py` are hand-tuned constants. After
several Giro stages, realized scenario distributions will be available.
**Fix:** Compare predicted vs realized scenario frequencies using `validate` output.
Tune multipliers to match. Even coarse adjustments (±20%) will improve calibration.

---

### Realized scenario stats in TeamSimResult
**Added:** 2026-04-22
**Context:** `scenario_priors` shows the model's prior. After simulation, we know what
scenarios were actually sampled. Exposing the realized distribution helps debugging
and gives the user a sense of model variance.
**Fix:**
```python
# In simulate_team(), track scenario counts
scenario_counts: dict[str, int] = defaultdict(int)
for sim_i in range(n):
    scenario = _sample_scenario(stage_type, rng)
    scenario_counts[scenario] += 1
    ...

# In TeamSimResult
realized_scenarios: dict[str, float]  # normalised frequencies
```
Expose as `scenario_stats` (realized) alongside existing `scenario_priors` in `/brief`.

---

## Frontend

### Role debug overlay
**Added:** 2026-04-22
**Context:** Role badges are displayed in tables, but there's no way to see why a rider
got a given role (what signal fired). Useful during model tuning.
**Fix:** Tooltip on role badge showing classification reason:
`"GC: gc_position=4" / "Sprint: p_win=0.08 > threshold" / "Breakaway: value=6.2M"`

---

## Live Validation (post May 9)

### Tune NOISE_FLOOR against real data
**Added:** 2026-04-22
**Context:** `NOISE_FLOOR = 20_000` is a prior estimate. After 3–5 Giro stages,
compute actual standard deviation of team score distributions across sims.
Set `NOISE_FLOOR ≈ std / sqrt(n_sim)` empirically.

### Calibrate ROLE_TOP15 matrix against Giro results
**Added:** 2026-04-22
**Context:** The role×stage_type prior matrix is hand-designed. After each stage,
`validate` will show how well model probabilities matched actual outcomes.
Track Brier scores per role per stage type and adjust matrix values accordingly.

---
