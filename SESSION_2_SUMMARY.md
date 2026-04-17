# Session 2 Summary — Probability Layer

**Date:** 2026-04-17
**Status:** Complete — 84/84 tests passing (59 engine + 25 probability)

---

## What Was Built

### `scoring/probabilities.py`

All probability logic for a single stage. No I/O except `save_probs`/`load_probs`.

**Exports:**
- `RiderProb` — dataclass with all fields from ARCHITECTURE.md
- `generate_priors(riders, stage) → dict[str, RiderProb]`
- `interactive_adjust(probs, stage, riders, _input_fn) → dict[str, RiderProb]`
- `save_probs(probs, stage_number, state_path) → None`
- `load_probs(stage_number, state_path) → dict[str, RiderProb] | None`

### `tests/test_probabilities.py`

25 unit tests across 7 test classes.

| Class | Tests | Covers |
|-------|-------|--------|
| `TestDNSRider` | 2 | p_dnf=1.0, all others 0.0 |
| `TestProbabilityClamping` | 2 | [0,1] range across all stage types and values |
| `TestMonotonicity` | 2 | p_win ≤ p_top3 ≤ p_top10 ≤ p_top15 always |
| `TestCompleteness` | 2 | Entry for every rider, mixed DNS/active |
| `TestJerseyRetention` | 3 | Green > 0.5 on flat, yellow flat > mountain, no jersey = empty dict |
| `TestSprintKOM` | 4 | Zero unless stage has sprint/KOM defined; non-zero when defined |
| `TestInteractiveAdjust` | 5 | source="adjusted", manual_overrides populated, value correct, unadjusted unchanged, DNF clamped |
| `TestPersistence` | 5 | Round-trip, missing stage=None, no file=None, creates file, preserves other keys |

---

## Architecture

### `RiderProb` dataclass

```python
@dataclass
class RiderProb:
    rider_id: str
    stage_number: int
    p_win: float
    p_top3: float
    p_top10: float
    p_top15: float
    p_dnf: float
    p_jersey_retain: dict        # jersey_name → float
    expected_sprint_points: float
    expected_kom_points: float
    source: str                  # "model" | "adjusted"
    model_confidence: float
    manual_overrides: dict       # field → value (audit trail)
```

### Prior generation

Stage-type lookup table → derive probability hierarchy:
```
p_top15 = BASE_TOP15[stage_type][rider_type]
p_top10 = p_top15 × 0.65
p_top3  = p_top10 × 0.30
p_win   = p_top3  × 0.35
```

| Stage type | all-type p_top15 |
|------------|-----------------|
| flat       | 0.12            |
| hilly      | 0.12            |
| mountain   | 0.08            |
| itt        | 0.12            |
| ttt        | 0.50            |

DNS riders: `p_dnf=1.0`, all others `0.0`, `model_confidence=1.0`.

Jersey retention by stage type (flat: yellow=0.85, mountain: yellow=0.40, etc).

Sprint/KOM expectations: zero unless `stage.sprint_points` / `stage.kom_points` are defined.

### CLI adjustment interface

```
──────────────────────────────────────────────────────────
  STAGE 1 — PROBABILITY REVIEW  [Flat, 147km]
──────────────────────────────────────────────────────────
   #  Rider                  Team      Win%  Top3  Top15   DNF  SpKOM  Conf  Src
  ─────────────────────────────────────────────────────────────────────────────
   1  Vingegaard Jonas        TVL         1%     2%     12%     1%    0.4   0.6   mod
   2  Milan Jonathan          LIT         1%     2%     12%     1%    0.4   0.6   mod
  ...

Commands:
  <rider fragment> <field> <value>   e.g. "milan win 35" or "ving dnf 5"
  done                               accept all and save
  show <rider fragment>              show full prob detail for one rider
  reset <rider fragment>             reset to model priors
```

Adjustment fields: `win`, `top3`, `top10`, `top15`, `dnf`, `sprint`, `kom`.
Probability values entered as percentages (50 = 0.50). Sprint/KOM as floats.
Adjusted values stored in `manual_overrides` dict. Source becomes `"adjusted"`.

### Persistence

Saves to `state.json["prob_history"]["stage_N"]`:
```json
{
  "prob_history": {
    "stage_1": {
      "47372": { "rider_id": "47372", "p_win": 0.38, ... }
    }
  }
}
```
Preserves all other existing keys in `state.json`.

---

## Design Decisions

### 1. Rider type always "all" for now

The `_rider_type()` function currently returns `"all"` regardless of rider value or data.
This means all active riders get identical priors for a given stage type.

**Why:** The session spec explicitly defers rider classification to Session 8.
Jersey holders are the one signal already used (for `p_jersey_retain`).

**Session 8 action:** Implement value-bracket classification:
- `value > 8_000_000` → GC or star sprinter (depending on stage type context)
- `value > 5_000_000` → specialist
- `value < 3_000_000` → domestique/wildcard

### 2. `_input_fn` injectable for testing

`interactive_adjust` accepts an optional `_input_fn` parameter that defaults to `input`.
Tests pass a mock that returns lines from a list.

**Why:** Avoids subprocess overhead and makes CLI tests deterministic and fast.

### 3. Empty file guard in `save_probs`

`tempfile.NamedTemporaryFile` creates a 0-byte file. Added `os.path.getsize() > 0`
check before attempting `json.load()` to avoid `JSONDecodeError` on empty files.

---

## Environment Notes

| Item | Value |
|------|-------|
| Python | 3.7 (default `python3`) works for this module |
| Run tests | `python3 -m pytest tests/ -v` |
| All tests | 84 passed |

---

## Next Session

**Session 3 — Monte Carlo Simulator** (`scoring/simulator.py`)
- `simulate_rider(rider, stage, probs, my_team, captain, n=10000) → SimResult`
- `simulate_team(riders, stage, probs, my_team, captain) → dict[id, SimResult]`
- Uses scoring engine internally for each simulation
- Returns EV, std_dev, p10, p50, p80, p90, p95, p_positive
- Start by reading: ARCHITECTURE.md (SimResult schema), SESSION_1_SUMMARY.md
- Confirm `python3 -m pytest tests/ -v` still passes (84 tests) before starting
