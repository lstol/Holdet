# Session 4 Summary — Optimizer + Risk Profiles

**Date:** 2026-04-17
**Status:** Complete — 179/179 tests passing (116 existing + 63 new)

---

## What Was Built

### `scoring/optimizer.py`

Transfer and captain recommendation engine across all 4 risk profiles.

**Exports:**
- `RiskProfile` — enum with ANCHOR / BALANCED / AGGRESSIVE / ALL_IN
- `TransferAction` — dataclass: action, rider_id, rider_name, value, fee, reasoning
- `ProfileRecommendation` — dataclass: profile, transfers, captain, expected_value, upside_90pct, downside_10pct, transfer_cost, reasoning
- `optimize(riders, my_team, stage, probs, sim_results, bank, risk_profile, rank, total_participants, stages_remaining) → ProfileRecommendation`
- `optimize_all_profiles(...) → dict[RiskProfile, ProfileRecommendation]`
- `suggest_profile(rank, total, stages_remaining, target_rank=100) → tuple[RiskProfile, str]`
- `format_briefing_table(recommendations, rider_map, stage) → str`

### `tests/test_optimizer.py`

63 unit tests across 9 test classes.

| Class | Tests | Covers |
|-------|-------|--------|
| `TestRiskProfileEnum` | 6 | 4 profiles, correct values, no STEADY/LOTTERY |
| `TestProfileMetric` | 4 | Correct metric per profile (p10/EV/p80/p95) |
| `TestBuyFee` | 3 | 1% fee calculation, rounding |
| `TestEvalSwap` | 10 | Per-profile swap acceptance logic |
| `TestProfileRecommendationSchema` | 10 | All output fields present and correct type |
| `TestConstraints` | 5 | 8-rider squad, 2-per-team, budget, no DNS/DNF, captain in squad |
| `TestProfileBehaviour` | 7 | Done conditions: sprinters vs GC riders, transfer counts |
| `TestCaptainSelection` | 2 | ANCHOR→highest EV, ALL_IN→highest p95 |
| `TestSuggestProfile` | 7 | All 4 auto-suggestion branches |
| `TestOptimizeAllProfiles` | 3 | Returns all 4 profiles, correct types |
| `TestForcedSells` | 3 | DNS/DNF riders sold, zero fee, replaced |
| `TestFormatBriefingTable` | 3 | Returns string, contains profile headers and stage info |

---

## Architecture

### Risk Profile Design Principle

Profiles are defined by **squad composition objective**, not transfer count.
Transfer count is an output of the optimizer, never an input constraint.

| Profile | Metric | Captain | Transfer logic |
|---------|--------|---------|----------------|
| ANCHOR | `percentile_10` | Highest EV | Only if p10 gain > fee/stages_remaining; never sell GC top-10 |
| BALANCED | `expected_value` | Best EV/std_dev | If EV gain > fee/stages_remaining |
| AGGRESSIVE | `percentile_80` | Highest p90 | If p80 improves; allow -30k EV if p80 gain ≥ +80k |
| ALL_IN | `percentile_95` | Highest p95 | Any positive p95 gain; fee payback secondary |

### Optimizer Algorithm

```
1. Forced sells: DNS/DNF riders on current team → sold, credits added to budget
2. Fill: pad squad to 8 with best-metric eligible riders if needed
3. Greedy swaps (up to 20 iterations):
   - Try all (sell, buy) pairs
   - Score swap via profile's _eval_swap() logic
   - Accept best swap with strictly positive score
   - Repeat until no improvement found
4. Captain: selected per profile rules
5. Aggregate metrics: total EV, p90, p10 across squad
```

Constraints enforced on every swap:
- Max 2 riders per `team_abbr`
- Budget: net spend ≤ bank
- No DNS/DNF riders bought
- Captain must be in the 8-rider squad

### `suggest_profile()` auto-suggestion

```python
if top_pct < 0.001:                    → ANCHOR     "Protect elite position"
if top_pct < 0.01:                     → BALANCED   "Controlled hunting"
if stages_remaining < 5:               → ALL_IN     "Running out of time"
if gap > stages_remaining * 80_000:    → AGGRESSIVE "Gap too large for safe play"
else:                                  → BALANCED   "Standard situation"
```

---

## Done Condition Verification

Test fixture: 8 mountain/GC riders (current team, all GC top-10) + 8 sprinters
(pool), flat stage, bank=50M, stages_remaining=10.

Mountain sim: p10=+20k, p95=+80k, ev=40k
Sprinter sim: p10=−20k, p95=+300k, ev=55k

| Profile | Transfers | GC riders in squad | Sprinters in squad |
|---------|-----------|--------------------|---------------------|
| ANCHOR | 0 | 8 | 0 |
| BALANCED | 8 | 0 | 8 |
| AGGRESSIVE | 8 | 0 | 8 |
| ALL_IN | 8 | 0 | 8 |

- ALL_IN has more sprinters than ANCHOR ✓
- ANCHOR has more GC riders than ALL_IN ✓
- AGGRESSIVE and ALL_IN transfer counts > ANCHOR ✓
- ANCHOR floor (p10) > ALL_IN floor ✓
- ALL_IN upside (p90) > ANCHOR upside ✓

ANCHOR makes 0 transfers because all 8 current riders are GC top-10 (protected).
On a real race, GC protection applies only to GC positions 1–10.

---

## Briefing Table Output Format

```
Stage 5 — FLAT (185 km)
═══════════════════════════════════════════════════════════════════════
                          ANCHOR   BALANCED  AGGRESSIVE     ALL-IN
───────────────────────────────────────────────────────────────────────
Captain:             Rider R1  Rider R1  Rider R1   Rider S1
Expected value:          +320k     +440k      +440k      +440k
Upside (90pct):          +560k     +1600k    +1600k     +1600k
Downside (10pct):        +160k     -160k     -160k      -160k
Transfers needed:            0         8          8          8
Transfer cost:              +0k     -400k      -400k      -400k
Net EV after fees:         +320k      +40k       +40k       +40k
═══════════════════════════════════════════════════════════════════════
```

---

## Files Changed

| File | Change |
|------|--------|
| `scoring/optimizer.py` | Created (new module) |
| `tests/test_optimizer.py` | Created (63 tests) |
| `ARCHITECTURE.md` | Section 4 rewritten: ANCHOR/BALANCED/AGGRESSIVE/ALL_IN |
| `ARCHITECTURE.md` | Section 3 `optimizer.py` interface updated |
| `SESSION_ROADMAP.md` | Session 8 odds-based probability section appended |

---

## Environment Notes

| Item | Value |
|------|-------|
| Python | 3.7 |
| numpy | 1.21.6 |
| Run tests | `python3 -m pytest tests/ -v` |
| All tests | 179 passed |

---

## Next Session

**Session 5 — API Ingestion** (`ingestion/api.py`)
- `get_riders(game_id, cookie) → list[Rider]`
- Parse Holdet API response (`items[]` + `_embedded.persons` + `_embedded.teams`)
- Probe candidate endpoints for GC standings / jersey data
- Start by reading: API_NOTES.md
- Confirm `python3 -m pytest tests/ -v` still passes (179 tests) before starting
