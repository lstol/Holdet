# SESSION_20_SUMMARY.md — Identity-Aware Lookahead EV Layer

**Date:** 2026-04-26
**Tests:** 502 passing (491 → 502, +11 new)
**Branch:** merged to main

---

## Design decision: handoff vs roadmap

Session 20 had two proposed designs:

| | Roadmap (original) | Handoff (adopted) |
|---|---|---|
| Approach | Inline optimizer: lookahead changes which squad is recommended | Separate analytical layer: lookahead informs the USER, optimizer unchanged |
| Output | 2-stage combined EV per squad (BALANCED profile) | Per-rider EV, volatility, consistency_risk across N stages |
| Captain | Not addressed explicitly | Explicitly excluded — critical invariant |
| Rider profiles | Consumed by optimizer | Explicitly consumed per stage |
| Adjustments | Passed through as probs_n1 | Stage-specific, non-bleeding, per design |

Adopted the handoff design because:
- Keeps optimizer clean and fast (single-stage, well-tested)
- Richer analytical output (volatility + consistency risk, not just squad EV)
- Matches "decision-support, not automation" philosophy — Lasse sees the ranking and decides
- Captain constraint is explicit and enforceable

The inline optimizer infrastructure (`evaluate_action_multistage`, `_eval` closure, `enable_lookahead` params) is retained as backward-compatible scaffolding for Session 21.

---

## What was built

### 1. `scoring/lookahead.py` (new) — primary deliverable

Core principle (enforced in code and comments):
```python
# LOOKAHEAD MUST NOT SELECT CAPTAINS.
# Captain selection is handled in the daily optimizer only.
# This module is strictly EV projection — it returns per-rider EVs
# for analytical purposes only.
```

**`LookaheadResult` dataclass**
```python
@dataclass
class LookaheadResult:
    rider_id: str
    ev_total: float
    ev_by_stage: list[float]    # one float per stage in horizon
    volatility: float           # std(ev_by_stage); 0.0 for horizon=1
    consistency_risk: float     # 1 / profile.consistency (1.0 if no profile)

    # properties: stages_simulated, ev_per_stage
```

**`simulate_lookahead()` pipeline per stage:**
- Step A: fresh `deepcopy(base_probs)` — adjustment never bleeds
- Step B: `apply_rider_adjustments(stage-specific)` ← STAGE-SPECIFIC ONLY
- Step C: `apply_rider_profiles(structural)`
- Step D: `simulate_all_riders(my_team=[], captain="")` ← CAPTAIN EXCLUDED
- Step E: accumulate EV per rider

**Adjustment non-bleeding guarantee:** Each stage starts from `deepcopy(base_probs)`. Stage 1 adjustments are NOT present when stage 2 is simulated. Tested by `test_stage1_adjustment_does_not_affect_stage2_ev`.

**Consistency risk formula:**
```python
consistency_risk = 1.0 / profile.consistency
# consistency=1.10 → risk ≈ 0.91 (reliable)
# consistency=0.85 → risk ≈ 1.18 (unreliable)
# no profile      → risk = 1.00 (neutral)
```

**Ranking helpers:** `rank_by_ev()`, `rank_by_volatility()`, `rank_by_stability()`

**`format_lookahead_table()` CLI output:**
```
LOOKAHEAD EV PROJECTION  (horizon=3 stages, n=200 sims/stage)
Rank  Rider                  Team      EV Total  EV/Stage  Volatility  Cons.Risk
────────────────────────────────────────────────────────────────────────────────
   1  Rider SP1              TEAM_C    +345,000  +115,000     +89,000       1.00
   2  Rider SP2              TEAM_D    +290,000   +96,667     +72,000       1.18
```

### 2. `scoring/optimizer.py` — backward-compatible lookahead infrastructure

| Added | Default | Purpose |
|---|---|---|
| `LOOKAHEAD_ALPHA = 0.85` | constant | Discount for N+1 EV |
| `LOOKAHEAD_N = 200` | constant | Fast-sim count for N+1 |
| `_lookahead_cache: dict` | `{}` | Separate cache for 2-stage results |
| `evaluate_action_multistage()` | — | Core 2-stage team evaluator |
| `eval_fn` param in `_try_double_swaps` | `None` | Route to lookahead evaluator |
| `enable_lookahead`, `probs_n1`, `intent_n1` in `optimize()` | `False/None/None` | Thread-through for Session 21 |
| `lookahead_ev` on `ProfileRecommendation` | `None` | Set when lookahead active |
| Same 3 params in `optimize_all_profiles()` | thread-through | — |

The `_eval` closure inside `optimize()` routes to the right evaluator:
```python
# When enable_lookahead=False (default): identical to previous _eval_team calls
# When enable_lookahead=True + next_stage + probs_n1 all set: uses evaluate_action_multistage
```

### 3. `main.py` — lookahead command and --lookahead flag

New subcommand:
```bash
python3 main.py lookahead --stage 5 --horizon 3
python3 main.py lookahead --stage 5 --horizon 5 --top 30
```

Pipeline called in correct order:
1. `generate_priors(riders, stage)`
2. Load profiles from `data/rider_profiles.json`
3. Load stage-specific adjustments from `state.json["rider_adjustments"]`
4. `simulate_lookahead(..., horizon=horizon)`
5. `format_lookahead_table(...)` + team rider highlight block

`--lookahead` flag on brief: appends a 3-stage lookahead table after normal briefing output.

New helper: `_load_stages_from(stages_path, from_stage) → list[Stage]`
Returns all stages with `number >= from_stage`, sorted by number.

---

## Test breakdown (+11 new, total 502)

`tests/test_lookahead.py` — 11 new tests

| Class | Tests | What it verifies |
|---|---|---|
| `TestLookaheadDoesNotMutateInputs` | 2 | `base_probs` unchanged; `adjustments` dict unchanged |
| `TestProfilesAffectEVVariance` | 1 | `consistency=1.10` → higher EV than `consistency=0.85` |
| `TestAdjustmentsAreStageSpecific` | 1 | stage 1 adj boosts stage 1 EV; stage 2 EV identical to baseline |
| `TestCaptainSelectionNotInLookahead` | 2 | no `captain_id` field; correct fields present |
| `TestVolatilityIncreasesWithLowConsistencyProfile` | 2 | risk formula verified; no profile → risk=1.0 |
| `TestEVAccumulatesAcrossHorizon` | 3 | `ev_total=sum(ev_by_stage)`; horizon=1→volatility=0; horizon=2 EV ≥ horizon=1 EV |

---

## Key design decisions

| Decision | Outcome |
|---|---|
| Separate module vs inline optimizer | Separate (`scoring/lookahead.py`) — cleaner, testable, no optimizer entanglement |
| Captain exclusion | Hard invariant: `my_team=[], captain=""` in all `simulate_all_riders` calls |
| Adjustment non-bleeding | `deepcopy(base_probs)` per stage — structural guarantee, not a convention |
| Consistency risk formula | `1 / profile.consistency` — monotonic, correct direction, range [0.83, 1.25] |
| Volatility formula | `statistics.stdev(ev_by_stage)` — standard, 0.0 for horizon=1 |
| n_sim default | 200 — fast enough for all riders across 5 stages in < 5s |
| Optimizer infrastructure | Kept, backward-compatible, no-ops by default (Session 21 can activate) |

---

## Files changed

| File | Change |
|---|---|
| `scoring/lookahead.py` | New — primary Session 20 deliverable |
| `scoring/optimizer.py` | Lookahead infrastructure: `evaluate_action_multistage`, `_eval` closure, constants, `ProfileRecommendation.lookahead_ev` |
| `main.py` | `cmd_lookahead()`, `_load_stages_from()`, `--lookahead` flag wired, argparse entry |
| `tests/test_lookahead.py` | 11 new tests |
| `SESSION_20_SUMMARY.md` | This file |
| `SESSION_ROADMAP.md` | Session 20 marked complete |
