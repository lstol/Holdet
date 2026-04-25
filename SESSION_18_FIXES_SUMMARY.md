# Session 18-Fixes Summary — Stabilization Before Session 19

**Date:** 2026-04-25
**Tests:** 464/464 passing (was 458 at start, +6 new tests)
**Branch:** main

---

## What was fixed

### Fix 18E — Naming Consistency

**`scoring/stage_intent.py`:** Added `INTENT_FIELDS` constant — the single source of truth for field order:
```python
INTENT_FIELDS: list[str] = [
    "win_priority",
    "survival_priority",
    "transfer_pressure",
    "team_bonus_value",
    "breakaway_likelihood",
]
```

`apply_intelligence_signals()` now uses `{f: getattr(intent, f) for f in INTENT_FIELDS}` for field iteration — no hardcoded order.

**`main.py`:** Intent summary block replaced with `INTENT_FIELDS`-ordered loop.

**`api/server.py`:** `stage_intent` response dict replaced with `{f: getattr(intent, f) for f in INTENT_FIELDS}`.

No shorthand aliases (`survival`, `team_bonus`) remain outside the dataclass definition.

---

### Fix 18F — Signal Alias Mapping

**`scoring/stage_intent.py`:** Added `SIGNAL_ALIASES` dict and value casing normalization:
```python
SIGNAL_ALIASES: dict[str, str] = {
    "sprint_disruption": "sprint_train_disruption",
    "gc_illness": "gc_rider_illness",
}
```

In `apply_intelligence_signals()`:
```python
k_norm = SIGNAL_ALIASES.get(key, key)    # resolve alias
v_norm = str(value).lower()              # normalize casing
canonical_key = f"{k_norm}:{v_norm}"
```

Unknown signals (after alias resolution) still log WARNING — unchanged behaviour.

**4 new tests** (`TestSignalAliasesAndNormalization`):
- `sprint_disruption` alias produces same result as `sprint_train_disruption`
- `gc_illness` alias produces same result as `gc_rider_illness`
- Unknown alias logs WARNING, no exception, intent unchanged
- Value casing `"HIGH"` / `"High"` / `"high"` all produce identical results

---

### Fix 18G — Intent Delta Visibility in CLI

**`main.py`:** Intent summary now uses `INTENT_FIELDS` order. When `--override` is used and fields changed, a delta block is printed:

```
Stage Intent (flat):
  win_priority=0.90  survival_priority=0.25  transfer_pressure=0.40  team_bonus_value=0.80  breakaway_likelihood=0.20

Intent delta (from overrides):
  breakaway_likelihood     0.20 → 0.45  (+0.25)
  survival_priority        0.25 → 0.40  (+0.15)
```

Silent when `--override` not used. `base_intent` saved before override application.

---

### Fix 18H — Non-Wiring Guard + Regression Test

**`scoring/optimizer.py`:** SESSION 20 BOUNDARY guard comment above `apply_intent_to_ev()` and `compute_transfer_penalty()`:

```python
# SESSION 20 BOUNDARY — DO NOT WIRE BELOW THIS POINT UNTIL SESSION 20
# apply_intent_to_ev() and compute_transfer_penalty() are defined here
# but intentionally not called from _eval_team() or optimize().
# Wiring happens in Session 20 (lookahead optimizer).
# See: docs/MULTI_STAGE_ARCHITECTURE.md
```

**1 new test** (`TestIntentDoesNotAffectOptimizerPreSession20`):
- `optimize()` with ANCHOR profile + intent produces identical squad, transfers, and expected_value vs intent=None (captain allowed to differ for BALANCED).

---

### Fix 18I — Captain Logic Alignment in Roadmap

**`SESSION_ROADMAP.md`:** Two locations corrected.

**Location 1** — Session 18C captain section. Old (probability formula):
```python
captain_value = (2.0 * p_win + 1.2 * p_top15 + intent.win_priority)
```
New (simulation-based):
```python
# BALANCED captain uses simulation EV with intent-weighted p95 nudge:
# score = expected_value + intent.win_priority * percentile_95 * 0.1
# ANCHOR: argmax(p10)   ALL_IN: argmax(p95)   — both unchanged by intent
```

**Location 2** — Locked decisions table:
- Old: `Intent-weighted: 2×p_win + 1.2×p_top15 + win_priority`
- New: `Simulation EV + intent.win_priority × p95 × 0.1 (BALANCED only)`

Verified: `grep "2.*p_win\|p_top15" SESSION_ROADMAP.md` returns only Brier tracking mentions.

---

### Fix 18J — Transfer Pressure in Reasoning

**`scoring/optimizer.py`:** `_build_reasoning()` accepts optional `intent` parameter. When `intent.transfer_pressure >= 0.65`, appends:

```
High transfer pressure (0.70) — stage context favours aggressive rotation today.
```

Safe string concatenation: ensures period before suffix, no double spaces. All `_build_reasoning()` call sites in `optimize()` updated to pass `intent`.

---

### Fix 18K — Anti-Double-Counting Guard in `_pick_captain`

**`scoring/optimizer.py`:** Guard comment immediately above the BALANCED intent branch:

```python
# NOTE — SESSION 20 DOUBLE-COUNTING RISK:
# win_priority biases captain toward high-p95 riders here.
# If apply_intent_to_ev() is wired in Session 20 (scales all EVs by
# win_priority), this term may need to be reduced or removed to avoid
# counting win_priority twice. Revisit at Session 20 start.
```

No code changes. Comment only.

---

### Fix 18L — Override Immutability Test

**1 new test** (`TestIntentImmutability.test_apply_intelligence_signals_does_not_mutate_original`):

Four assertions:
1. `base is not modified` — different object returned
2. `base.breakaway_likelihood != modified.breakaway_likelihood` — signal actually changed something
3. `base == recomputed` — no side effects on base (deterministic recompute matches)
4. `base is not recomputed` — no shared instance or caching

---

## Test breakdown (+6 new)

| File | New tests | Classes |
|------|-----------|---------|
| `tests/test_stage_intent.py` | 5 | `TestSignalAliasesAndNormalization` (4), `TestIntentImmutability` (1) |
| `tests/test_optimizer.py` | 1 | `TestIntentDoesNotAffectOptimizerPreSession20` (1) |

**Total: 464 passing** (target was 463+)

---

## Files changed

| File | Change |
|------|--------|
| `scoring/stage_intent.py` | `INTENT_FIELDS`, `SIGNAL_ALIASES`, value casing normalization |
| `scoring/optimizer.py` | SESSION 20 guard, double-counting guard, `_build_reasoning(intent)` |
| `main.py` | `INTENT_FIELDS` import, delta block, summary rewrite |
| `api/server.py` | `INTENT_FIELDS` import, `stage_intent` dict via comprehension |
| `SESSION_ROADMAP.md` | Captain formula corrected (2 locations) |
| `tests/test_stage_intent.py` | 5 new tests |
| `tests/test_optimizer.py` | 1 new test |
