# Multi-Stage Optimization Architecture

**Status: SCAFFOLD ONLY — not implemented. Session 16 documents the design.
Implementation is gated on Giro validation data (see §8).**

---

## 1. State Definition

```python
@dataclass
class RaceState:
    stage_index: int           # 0-based
    squad_ids: list[str]       # 8 holdet_ids
    bank: float                # current bank balance in kr
    transfers_left: int        # remaining transfers (if capped)
```

The state fully describes what the player owns and can spend going into the next
decision point. `stage_index` maps to `stages[stage_index]` in the full stage list.

---

## 2. Action Definition

```python
@dataclass
class Action:
    sells: list[str]    # holdet_ids to remove from squad
    buys: list[str]     # holdet_ids to add (len must equal sells)
    captain: str        # holdet_id of designated captain
```

Constraints (validated before applying):
- `len(sells) == len(buys)`
- All sell ids are currently in `squad_ids`
- All buy ids are not currently in `squad_ids`
- Resulting squad satisfies team-count rule (≤2 per team)
- Net cost ≤ `bank`

---

## 3. Transition Function (signature only — not implemented)

```python
def apply_action(state: RaceState, action: Action, stage: Stage) -> RaceState:
    """
    Apply transfers and captain selection.
    Returns new RaceState with updated squad and bank after buying/selling.
    Does NOT run simulation — just validates and updates squad.
    """
    ...
```

This is a pure state update: validate constraints, deduct buy fees, credit sell
proceeds, update `squad_ids`. Simulation runs separately in `simulate_team()`.

---

## 4. Stage Value

Already implemented:

```python
simulate_team(team, captain, stage, riders, probs, n=500, seed=42) -> TeamSimResult
```

`TeamSimResult.expected_value` is the primary value signal for one stage.
The optimizer already uses this via `_eval_team()` with memoization.

---

## 5. Future Value (planned, not built)

### Phase 1 (Session 17 candidate)

```
value(action) = v_stage + α * v_next_stage
```

Where:
- `v_stage` = `simulate_team(resulting_squad, stage_N)` EV
- `v_next_stage` = `optimize(resulting_squad, stage_N+1)` best-profile EV
- `α` = discount factor (0.8–0.95) to weight certainty of current stage

This is a 1-stage look-ahead. Viable with n=100–200 per evaluation since the
next-stage value is approximate anyway.

### Phase 2 (future)

Monte Carlo rollout over 2–3 stage horizon. Expensive — only viable with:
- Fast simulator (n=100–200 per node)
- Pruned action space (see §8)
- Parallel evaluation of candidate actions

---

## 6. Evaluation Function (signature only — not implemented)

```python
def evaluate_action_multistage(
    state: RaceState,
    action: Action,
    stages: list[Stage],
    riders: list[Rider],
    probs: dict[str, RiderProb],
    horizon: int = 2,
    profile: str = "BALANCED",
) -> float:
    """Returns estimated multi-stage value of taking this action."""
    ...
```

The implementation would:
1. Apply `action` to `state` → `new_state`
2. Evaluate `v_stage` = `simulate_team(new_state.squad_ids, stages[state.stage_index])`
3. For `horizon > 1`: recursively evaluate best action at `stages[state.stage_index + 1]`
   (using the current `optimize()` as rollout policy)
4. Return discounted sum

---

## 7. Design Principles

- **Simulation separate from optimizer**: `simulate_team()` is a pure value
  function. The optimizer is a search procedure over actions. Keep them decoupled.

- **Reuse current `optimize()` as rollout policy**: For multi-stage lookahead,
  the single-stage optimizer approximates the greedy policy for future stages.
  This is good enough for Phase 1.

- **Limit horizon to 2–3 stages maximum**: Beyond 3 stages, prediction error
  compounds faster than the signal from lookahead.

- **No GC time-gap modeling in Phase 1**: GC standings evolve stage-by-stage
  and would require a full GC simulation layer. Out of scope until Phase 2.

- **Transfer cost compounding is already handled**: `_eval_team()` accounts for
  fees via `remaining_budget`. Reuse this — don't duplicate the accounting.

- **Current multiplier model is the last stable design point**: Session 16
  delivers `scenario → role → scalar multiplier`. This is intentionally coarse.
  The next natural evolution is `scenario × stage_type × rider_profile →
  probability shift`, but that requires calibrated Giro data first. Do not
  generalize the multiplier table prematurely — extend it only once live
  validation reveals systematic bias.

---

## 8. Why Not Now

### Blockers

| Blocker | Detail |
|---|---|
| Probability calibration | `p_win`, `p_top15` are model estimates. Giro 2026 data will reveal systematic bias. A 2-stage horizon amplifies modeling errors quadratically. |
| Scenario correctness | Session 16 delivers scenario-aware simulation. This must be validated against Giro outcomes before extending the horizon. |
| Action space size | O(C(91,2)²) sell-buy pairs × captain choices before pruning. Need a good candidate filter before multi-stage search is tractable. |
| No stage-to-stage state update | Rider values, statuses, and GC standings change after each stage. The full state transition requires a live data pipeline. |

### What needs to be true before Phase 1

1. **Giro validation**: at least 5 stages of observed outcomes to calibrate
   `p_win` and `p_top15` accuracy per stage type.
2. **Scenario audit**: verify that scenario-conditioned outcomes match priors
   (Session 16's `scenario_stats` field provides this data).
3. **Candidate pruning**: reduce action space to ~50 riders × top-20 sell
   candidates before attempting lookahead.

---

*Last updated: Session 16 (2026-04-24)*
