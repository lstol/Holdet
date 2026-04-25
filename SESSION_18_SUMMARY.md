# Session 18 Summary — ICDL v1: Intelligence-Conditioned Decision Layer

**Date:** 2026-04-24 (fixes: 2026-04-25)
**Tests:** 464/464 passing (429 → 458 in Session 18, +6 in 18-Fixes)
**Branch:** claude/heuristic-cartwright-4cacf0 → merged to main

---

## What was built

### Part 18A — StageIntent (`scoring/stage_intent.py`)

New file. Deterministic, fully testable.

**`INTENT_FIELDS` constant (locked field order — import this, never hardcode):**
```python
INTENT_FIELDS: list[str] = [
    "win_priority",
    "survival_priority",
    "transfer_pressure",
    "team_bonus_value",
    "breakaway_likelihood",
]
```

**`StageIntent` dataclass (frozen):**
- `win_priority` — how much does winning matter today?
- `survival_priority` — how bad is DNF / gruppo risk?
- `transfer_pressure` — how urgently should we rotate?
- `team_bonus_value` — is holding a full team worth it today?
- `breakaway_likelihood` — chance of a small-group finish

**Base values by stage type:**

| Stage type | win_priority | survival_priority | transfer_pressure | team_bonus_value | breakaway_likelihood |
|------------|-------------|------------------|------------------|-----------------|---------------------|
| flat       | 0.90        | 0.25             | 0.40             | 0.80            | 0.20                |
| hilly      | 0.75        | 0.55             | 0.55             | 0.60            | 0.45                |
| mountain   | 0.70        | 0.90             | 0.70             | 0.20            | 0.55                |
| itt        | 0.85        | 0.20             | 0.30             | 0.10            | 0.05                |
| ttt        | 0.60        | 0.50             | 0.20             | 1.00            | 0.00                |

**Modifiers (additive, clamped to [0,1]):**
- `gc_spread_tight`: ≥3 riders gc_position ≤ 5 on mountain → survival_priority +0.10, win_priority +0.05
- `next_stage_is_mountain`: flat/hilly → mountain → transfer_pressure +0.20
- `next_stage_is_flat`: mountain → flat → transfer_pressure +0.15
- `sprinter_dense_pool`: >30% riders have gc_position=None on flat → breakaway_likelihood +0.05

---

### Part 18B — Intelligence Overrides (`scoring/stage_intent.py`)

**`SIGNAL_INTENT_DELTAS` dict (4 signals):**

| Signal key | Fields affected |
|------------|----------------|
| `crosswind_risk:high` | breakaway_likelihood +0.25, survival_priority +0.15 |
| `sprint_train_disruption:likely` | breakaway_likelihood +0.20, win_priority -0.15 |
| `gc_rider_illness:confirmed` | survival_priority +0.20, transfer_pressure +0.25 |
| `stage_shortened:confirmed` | team_bonus_value -0.30 |

**`SIGNAL_ALIASES` dict (user-facing shortcuts):**
```python
SIGNAL_ALIASES: dict[str, str] = {
    "sprint_disruption": "sprint_train_disruption",
    "gc_illness": "gc_rider_illness",
}
```

**`apply_intelligence_signals(intent, signals) → StageIntent`:**
- Signals format: `{"crosswind_risk": "high"}`
- Key resolved through `SIGNAL_ALIASES` before lookup
- Value lowercased before lookup — `"HIGH"` and `"high"` are identical
- Lookup via `key:value` compound key in `SIGNAL_INTENT_DELTAS`
- Unknown keys (after alias resolution) logged at WARNING, not raised
- All fields clamped to [0.0, 1.0]
- Returns NEW StageIntent — original never mutated (frozen dataclass)

**Override file format** (`overrides/stage_N.json`):
```json
{
  "stage_3": {
    "signals": {"crosswind_risk": "high"},
    "reason": "DS confirmed crosswind — echelon risk"
  }
}
```
`reason` field is required (validated in `cmd_brief`).

**`overrides/` directory:** created with `.gitkeep`, git-tracked.

---

### Part 18C — Intent-Weighted EV + Captain (`scoring/optimizer.py`)

**New imports:**
```python
from config import LAMBDA_TRANSFER
from scoring.stage_intent import StageIntent, compute_stage_intent, apply_intelligence_signals, INTENT_FIELDS
```

**SESSION 20 BOUNDARY guard** (above EV utility functions):
```
# SESSION 20 BOUNDARY — DO NOT WIRE BELOW THIS POINT UNTIL SESSION 20
# apply_intent_to_ev() and compute_transfer_penalty() are defined here
# but intentionally not called from _eval_team() or optimize().
```

**`apply_intent_to_ev(base_ev, intent) → float`:**
```python
return base_ev * (1.0 + 0.3 * intent.win_priority)
```

**`compute_transfer_penalty(fee, intent) → float`:**
```python
return fee * (1.0 + intent.transfer_pressure)
```

Both are utility functions — NOT wired into `_eval_team()` yet (Session 20).

**`_pick_captain` updated:**
- New optional `intent: StageIntent | None = None` parameter
- BALANCED profile: `s.expected_value + (intent.win_priority * s.percentile_95 * 0.1 if intent else 0)`
- ANCHOR and ALL_IN profiles: unchanged
- Guard comment above BALANCED branch warns of Session 20 double-counting risk

**`_build_reasoning()` updated:**
- Accepts optional `intent` parameter
- Appends transfer pressure note when `intent.transfer_pressure >= 0.65`:
  `"High transfer pressure (0.70) — stage context favours aggressive rotation today."`

**`optimize()` signature:**
```python
def optimize(..., intent: Optional[StageIntent] = None, next_stage: Optional[Stage] = None) -> ProfileRecommendation
```
`intent` passed to all `_pick_captain` and `_build_reasoning` call sites inside optimize, including `_try_double_swaps`.

**`optimize_all_profiles()` signature:**
```python
def optimize_all_profiles(..., intent: Optional[StageIntent] = None, next_stage: Optional[Stage] = None) -> dict
```

**`config.py`:**
```python
LAMBDA_TRANSFER: float = 0.85
```

---

### Part 18D — CLI + API

**`main.py` — `brief` subparser new flags:**
- `--override PATH` — load override JSON file, apply signals, print summary + delta
- `--lambda FLOAT` — transfer discount factor (stored as `args.lambda_val`)
- `--lookahead` — logs "not yet implemented, Session 20" and continues

**Intent summary printed at start of brief (INTENT_FIELDS order):**
```
Stage Intent (flat):
  win_priority=0.90  survival_priority=0.25  transfer_pressure=0.40  team_bonus_value=0.80  breakaway_likelihood=0.20
```

**Delta block printed when `--override` used and fields changed:**
```
Intent delta (from overrides):
  breakaway_likelihood     0.20 → 0.45  (+0.25)
  survival_priority        0.25 → 0.40  (+0.15)
```
Silent when `--override` not used.

**`api/server.py` — `BriefRequest` new fields:**
```python
intelligence_signals: Optional[dict] = None
intelligence_reason: Optional[str] = None
next_stage_type: Optional[str] = None   # reserved for Session 20
```

**`/brief` handler:**
- Computes `intent = compute_stage_intent(stage, gc_positions, next_stage=None, riders=riders)`
- If `intelligence_signals` provided: validates `intelligence_reason` (HTTP 400 if missing), applies signals
- Passes `intent` to `optimize_all_profiles()`

**`/brief` response includes (INTENT_FIELDS order):**
```json
"stage_intent": {
    "win_priority": 0.90,
    "survival_priority": 0.25,
    "transfer_pressure": 0.40,
    "team_bonus_value": 0.80,
    "breakaway_likelihood": 0.20
}
```

---

## Test breakdown (464 total, +35 from Session 18 baseline of 429)

### Session 18 — 29 new tests

**`tests/test_stage_intent.py` (24 new tests):**

`TestComputeStageIntent` (11 tests):
- flat stage high win_priority
- mountain stage high survival_priority
- ITT low breakaway_likelihood
- TTT max team_bonus_value
- next_mountain increases transfer_pressure
- next_flat increases transfer_pressure on mountain
- tight GC boosts survival_priority on mountain
- tight GC does NOT affect flat survival_priority
- all fields clamped to [0,1] under stacked modifiers
- sprinter-dense pool raises breakaway_likelihood on flat
- hilly base values exact

`TestApplyIntelligenceOverrides` (8 tests):
- crosswind raises breakaway_likelihood and survival_priority
- sprint disruption lowers win_priority
- gc_illness raises survival_priority and transfer_pressure
- stage_shortened lowers team_bonus_value
- unknown signal ignored, not raised
- returns new intent, original unchanged
- stacked signals clamped to [0,1]
- all 4 signals covered in SIGNAL_INTENT_DELTAS

`TestIntentEVFunctions` (5 tests):
- apply_intent_to_ev: win_priority=1.0 → EV × 1.3
- apply_intent_to_ev: win_priority=0.0 → EV unchanged
- compute_transfer_penalty: pressure=1.0 → 2× fee
- compute_transfer_penalty: pressure=0.0 → fee unchanged
- λ=0 regression guard: next_ev has zero contribution

**`tests/test_optimizer.py` (+5 new tests):**

`TestOptimizeAcceptsIntent` (3 tests):
- optimize() with intent=None: no error
- optimize() with valid StageIntent: no error
- optimize_all_profiles() with intent: returns 4 ProfileRecommendations

`TestICDLRegressionGuard` (2 tests):
- λ=0, win_priority=0 → net_ev == base_ev - fee (pre-Session-18 behaviour)
- ANCHOR captain identical with and without intent

### Session 18-Fixes — 6 new tests

**`tests/test_stage_intent.py` (+5):**

`TestSignalAliasesAndNormalization` (4 tests):
- `sprint_disruption` alias → same result as `sprint_train_disruption`
- `gc_illness` alias → same result as `gc_rider_illness`
- unknown alias: no exception, intent unchanged
- value casing: `"HIGH"` / `"High"` / `"high"` all produce identical results

`TestIntentImmutability` (1 test):
- `base is not modified`, value changed, no side effects on base, no shared instance

**`tests/test_optimizer.py` (+1):**

`TestIntentDoesNotAffectOptimizerPreSession20` (1 test):
- optimize() with ANCHOR profile + intent produces identical squad, transfers, and EV vs intent=None

---

## Key design decisions confirmed

| Decision | Outcome |
|----------|---------|
| `INTENT_FIELDS` | Defined once in stage_intent.py; imported everywhere field iteration needed |
| `apply_intent_to_ev` wiring | NOT in _eval_team() — SESSION 20 BOUNDARY guard prevents accidental wiring |
| Override validation | reason field checked in cmd_brief, not in apply_intelligence_signals |
| Signal aliases | SIGNAL_ALIASES resolves short keys; value casing always normalized to lowercase |
| gc_positions input | list → dict conversion handled in main.py and api/server.py |
| _try_double_swaps | Also receives intent and threads it to _pick_captain |
| StageIntent frozen | Immutable by design — apply_intelligence_signals always returns new instance |
| Captain formula | Simulation EV + intent.win_priority × p95 × 0.1 (BALANCED only); NOT probability-based |
| Double-counting risk | Guard comment in _pick_captain BALANCED branch — revisit at Session 20 start |
| Transfer pressure note | Visible in briefing reasoning when transfer_pressure ≥ 0.65 |
