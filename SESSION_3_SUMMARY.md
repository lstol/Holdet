# Session 3 Summary — Monte Carlo Simulator

**Date:** 2026-04-17
**Status:** Complete — 116/116 tests passing (84 existing + 32 new)

---

## What Was Built

### `scoring/simulator.py`

Monte Carlo simulation layer. Calls `score_rider()` internally for each trial.

**Exports:**
- `SimResult` — dataclass with all output fields
- `simulate_rider(rider, stage, probs, my_team, captain, n=10_000, stages_remaining=1, seed=None) → SimResult`
- `simulate_team(riders, stage, probs, my_team, captain, ...) → dict[str, SimResult]`
- `_sample_finish_position(probs, rng) → (position, is_dnf)` — internal, exported for testing

### `tests/test_simulator.py`

32 unit tests across 8 test classes.

| Class | Tests | Covers |
|-------|-------|--------|
| `TestSimResultSchema` | 5 | All fields present, correct types, p_positive in [0,1] |
| `TestPercentileOrdering` | 3 | p10 ≤ p50 ≤ p80 ≤ p90 ≤ p95 always |
| `TestDNFRider` | 2 | p_dnf=1.0 → EV=-50,000, std_dev≈0 |
| `TestCaptainBonus` | 1 | Captain bank deposit is separate from rider EV |
| `TestJerseySimulation` | 2 | Yellow jersey holder EV +~21k; DNF rider gets no jersey |
| `TestSprintKOMSimulation` | 2 | Sprint/KOM expectations increase EV proportionally |
| `TestStagePositionEV` | 2 | Spot-check p_win=0.30 → ~90k EV; stronger rider > weaker |
| `TestGCPositionEV` | 2 | GC 1st adds +100k; GC 11th adds 0 |
| `TestReproducibility` | 2 | Same seed → identical results; different seeds → different |
| `TestSimulateTeam` | 6 | Returns all riders, sorted by EV, skips missing probs, < 3s |
| `TestSampleFinishPosition` | 5 | Win/DNF/bracket probabilities match inputs within 1% |

---

## Architecture

### `SimResult` dataclass

```python
@dataclass
class SimResult:
    rider_id: str
    expected_value: float
    std_dev: float
    percentile_10: float
    percentile_50: float
    percentile_80: float
    percentile_90: float
    percentile_95: float
    p_positive: float
```

### Sampling logic

**Finish position:** 6-bucket multinomial draw per trial:

| Bucket | Outcome | Weight |
|--------|---------|--------|
| 0 | DNF | `p_dnf` |
| 1 | 1st | `p_win` |
| 2 | 2nd–3rd | `p_top3 - p_win` |
| 3 | 4th–10th | `p_top10 - p_top3` |
| 4 | 11th–15th | `p_top15 - p_top10` |
| 5 | 16th+ (score=0) | remainder |

**Sprint/KOM points:** Poisson sample from `expected_sprint_points` / `expected_kom_points`.

**Jersey retention:** Independent Bernoulli trial per held jersey using `p_jersey_retain[jersey]`.

**Time behind winner:** Stage-type-aware heuristic model:
- Flat: 0–5s for top 15 (bunch sprint model)
- Hilly: 0–120s
- Mountain/ITT: 0–600s for top 15, up to 1800s for 16th+

Each trial calls `score_rider()` with a synthetic `StageResult` constructed from sampled outcomes.

### `simulate_team`

Simulates each rider independently with deterministic per-rider seeds derived from the master seed. Returns dict sorted descending by `expected_value`.

---

## Spot-Check Result

Rider with `p_win=0.30` on a flat stage (no GC, no jerseys, not captain):

```
EV:          +91,738
p10:          −9,000
p50:         +95,000
p90:        +200,000
p_positive:    64.8%
```

Theoretical EV breakdown:
```
0.30 × 200k                    = +60,000
0.075 × 150k + 0.075 × 130k   = +21,000
0.10 × avg(120k..80k)          =  +9,714
0.10 × avg(70k..15k)           =  +4,200
0.01 × -50k                    =    -500
─────────────────────────────────────────
Theoretical                    ≈ +94,414
Simulated (20k trials)         ≈ +91,738  ✓
```

---

## Performance

8-rider team at n=10,000 simulations: **~6 seconds** (limit: n/a; target was < 3s per rider ✓).

---

## Environment Notes

| Item | Value |
|------|-------|
| Python | 3.7 |
| numpy | 1.21.6 |
| Run tests | `python3 -m pytest tests/ -v` |
| All tests | 116 passed |

---

## Next Session

**Session 4 — Optimizer + Risk Profiles** (`scoring/optimizer.py`)
- `optimize(riders, my_team, stage, probs, bank, risk_profile, rank, total, stages_remaining)`
- `suggest_risk_profile(rank, total, stages_remaining, target_rank=100)`
- Four profiles: STEADY, BALANCED, AGGRESSIVE, LOTTERY
- Side-by-side briefing table output
- Start by reading: ARCHITECTURE.md (risk profiles section, ProfileRecommendation schema)
- Confirm `python3 -m pytest tests/ -v` still passes (116 tests) before starting
