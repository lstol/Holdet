# Session 1 Summary — Scoring Engine

**Date:** 2026-04-16
**Status:** Complete — 59/59 tests passing

---

## What Was Built

### `scoring/engine.py`
Pure scoring function with zero side effects. No file I/O, no API calls, no global state.

**Exports:**
- `score_rider(rider, stage, result, my_team, captain, stages_remaining, all_riders=None) → ValueDelta`
- `late_arrival_penalty(seconds_late) → int` — standalone helper, also used internally
- All dataclass schemas: `Rider`, `Stage`, `StageResult`, `ValueDelta`, `SprintPoint`, `KOMPoint`
- All scoring tables as named constants: `STAGE_POSITION_TABLE`, `GC_STANDING_TABLE`, `JERSEY_VALUES`, `TEAM_BONUS_TABLE`, `ETAPEBONUS_TABLE`, `TTT_PLACEMENT_TABLE`

**All 11 scoring cases implemented (RULES.md references in comments):**

| # | Case | Rule |
|---|------|------|
| 1 | Stage finish position (1st–15th, 16th+=0) | RULES.md 2.1 |
| 2 | GC standing (1st–10th, 11th+=0, every stage) | RULES.md 2.2 |
| 3 | Jersey bonuses (holder at END of stage, not entrant) | RULES.md 2.3 |
| 4 | Sprint + KOM points (+3,000/point, ≥0 always) | RULES.md 2.4 |
| 5 | Late arrival penalty (truncated minutes × −3,000, cap −90,000) | RULES.md 2.5 |
| 6 | DNF penalty (−50,000 once; keeps sprint/KOM; no team bonus) | RULES.md 2.6 |
| 7 | DNS penalty (−100,000 × stages_remaining) | RULES.md 2.6 |
| 8 | Team bonus (60k/30k/20k to active same-team riders from other finishers) | RULES.md 3.1 |
| 9 | Captain bonus (positive growth mirrored to bank; losses NOT amplified) | RULES.md 3.2 |
| 10 | Etapebonus (nonlinear bank deposit by top-15 count) | RULES.md 3.3 |
| 11 | TTT mode (replaces finish, team bonus, late arrival, etapebonus) | RULES.md 4 |

### `tests/test_engine.py`
59 unit tests across 9 test classes.

| Class | Tests | Covers |
|-------|-------|--------|
| `TestLateArrivalPenalty` | 10 | Truncation, cap, TTT exemption, via score_rider |
| `TestJerseyRule` | 6 | Lost-at-finish=0, multiple jerseys, most aggressive, TTT |
| `TestCaptainBonus` | 5 | Positive day, negative day, zero day, non-captain, example |
| `TestDNF` | 6 | Penalty, sprint/KOM kept, no team bonus, no position, no late arrival, DQ |
| `TestDNS` | 4 | Cascade, 1 stage, no position/team, rules example |
| `TestEtapebonus` | 4 | Full table, 4-rider case, bank-not-rider, zero on TTT |
| `TestTTT` | 6 | 1st place, all replacements=0, GC still applies, placements, 6th+=0, captain |
| `TestTeamBonus` | 6 | Active gets it, DNF doesn't, different team=0, values, TTT=0, None=0 |
| `TestGCStanding` | 3 | Full values, 11th+=0, not in list=0 |
| `TestStagePosition` | 3 | Key values, 16th+=0, TTT=0 |
| `TestSprintKOM` | 4 | Sprint, KOM, combined, zero |
| `TestValueDeltaTotals` | 2 | total_rider_value_delta sum, total_bank_delta sum |

### Project scaffolding
- `scoring/__init__.py`
- `tests/__init__.py`
- `data/results/` (directory)
- `.gitignore`
- `.env.example`

---

## Design Decisions

### 1. `all_riders` parameter extension
`score_rider()` takes an optional `all_riders: dict[str, Rider] | None = None` parameter not present in the original ARCHITECTURE.md signature.

**Why it's needed:** Team bonus (RULES.md 3.1) requires knowing the real-world team of each top-3 finisher. The StageResult schema only stores `finish_order` as holdet_ids — without a lookup dict, team membership of finishers is unknowable inside the pure function.

**Behaviour when None:** `team_bonus` silently returns 0. Does not raise. Safe for unit tests that don't need team bonus.

**Session 6 action:** When building the CLI settle command, pass `all_riders` populated from the rider market fetch.

### 2. Team bonus excludes the rider's own finish position
When computing `team_bonus` for rider X, positions where `finisher_id == rider.holdet_id` are skipped. The rider's own finish is already captured in `stage_position_value`.

**Why:** Without this skip, a rider finishing 2nd would receive both `stage_position_value = +150,000` (correct) and `team_bonus = +30,000` (wrong — they'd be getting a bonus from themselves). Tests confirmed this is the correct interpretation.

### 3. `etapebonus_bank_deposit` is team-level but per-rider in output
The same value is computed and returned in every `score_rider()` call for the same stage. It represents the team's total etapebonus for that stage.

**Caller responsibility:** Credit the bank ONCE per stage, not once per rider. When Session 6 builds `settle`, use `delta.etapebonus_bank_deposit` from any one rider's result (they're all identical).

---

## Environment Notes

| Item | Value |
|------|-------|
| Python | 3.14.4 at `/usr/local/bin/python3.14` |
| Default `python3` | 3.7 — **do not use** |
| Run tests | `python3.14 -m pytest tests/ -v` |
| pytest | 9.0.3 |

---

## Validation (To Do)

Once Giro Stage 1 results are known, validate the engine against the Holdet site:

```python
# Enter actual Stage 1 results, then:
from scoring.engine import score_rider, Rider, Stage, StageResult

rider = Rider(holdet_id="47372", ...)   # Vingegaard
stage = Stage(number=1, ...)
result = StageResult(...)

delta = score_rider(rider, stage, result, my_team, captain, stages_remaining, all_riders)
print(delta)
# Compare delta.total_rider_value_delta against Holdet site value change
```

Log results in `tests/validation_log.md` using the format from ARCHITECTURE.md section 8.

---

## Next Session

**Session 2 — Probability Layer** (`scoring/probabilities.py`)
- `RiderProb` dataclass
- `generate_priors(riders, stage)` — model estimates from stage type + rider data
- `interactive_adjust(probs, stage)` — CLI review and adjustment loop
- Start by reading: README.md, ARCHITECTURE.md (RiderProb schema)
- Confirm `python3.14 -m pytest tests/ -v` still passes before starting
