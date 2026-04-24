# ROADMAP TO FINAL VERSION — Sessions 17–25
# Revised after Session 16. Incorporates corrections from external review.
# Last updated: 2026-04-24

---

## Two tracks running in parallel

**Track A — Reality alignment**
Validate engine vs Holdet → calibrate probabilities → tune multipliers

**Track B — Decision quality**
Lookahead → variance-aware profiles → differential picks → strategy layer

These run in parallel, not sequentially. Track B does not wait for Track A
to be perfect. Even imperfect probabilities improve decisions when lookahead
is shallow and based on relative ordering.

---

## The one correction to the previous plan

~~"Do NOT build lookahead before calibration"~~

**Better rule:** Build lookahead early, keep it shallow and testable, trust it
fully only after calibration confirms the base simulator is directionally correct.

Why this is safe: lookahead at horizon=1 mostly depends on relative rider ordering
(sprinter vs climber on a flat vs mountain stage), not on precise probability values.
Even rough calibration is sufficient for this. The biggest current flaw in the tool
is myopic single-stage decisions — fixing that early has immediate competitive value.

---

## Phase 1 — Foundation (Sessions 17–19)

### Session 17 — Live Validation + Calibration
**After:** Stage 1 results available (May 9+)
**Goal:** Confirm engine is correct. Get first Brier score. Do not over-tune.

**Must deliver:**
- `validate --stage 1` runs cleanly, output in `validation_log.md`
- Engine matches Holdet output for all team riders (|diff| < 5k per rider)
- First Brier score logged (p_win and p_top15, separately)
- GC standings auto-populated if `/api/games/612/standings` is now live
- `python3 main.py roles --stage N` debug command
- Ownership field populated if `popularity` endpoint is live

**⚠️ Calibration discipline — important:**
Do NOT adjust `ROLE_TOP15` or `SCENARIO_MULTIPLIERS` after one stage.
One stage is noise. Log what you see, note the direction, wait.

Adjustment rule:
- After 3 stages: adjust if same role is wrong by >15pp in the same direction
- After 5 stages: adjust confidently with Brier score before/after comparison

**Target: 440 tests passing**

---

### Session 18 — Minimal Lookahead
**After:** Session 17 complete (engine confirmed correct)
**Goal:** Fix the biggest current flaw — myopic single-stage decisions.

**Scope: keep it small. No new architecture. No state transitions. Just this:**

```python
EV_total = EV_stage_N + λ * EV_stage_N1
```

Where:
- `EV_stage_N` = current `simulate_team()` result (already working)
- `EV_stage_N1` = fast lookahead simulation (n=200) for next stage
- `λ = 0.85` (tune after 3+ stages of data)

**What to build:**
- `optimize()` gains optional `next_stage: Stage | None` parameter
- If provided, `_eval_team()` adds `λ * lookahead_ev` to the metric
- **Backend auto-detects next stage** from `stages[current_index + 1]` in
  `server.py` — no frontend input required. Frontend does nothing. An optional
  manual override can exist, but the default must always be automatic. This
  avoids user mismatch, incorrect assumptions, and UI friction.
- **Lookahead recomputes probabilities for next stage type.** Do not reuse
  current stage probs. A sprint→mountain transition means GC riders should
  get climber probs in the lookahead, not flat-stage probs. Call the existing
  `generate_priors(riders, next_stage)` — it already supports stage-type-dependent
  priors. This is one line and avoids the hidden assumption that stage N ≈ stage N+1.
- No new frontend parameters needed. `/brief` request body unchanged.

**Why λ=0.85 and not 1.0:**
Next-stage EV is less certain (probs may shift, weather, tactics).
Discount encodes that uncertainty without requiring a formal model.

**Key constraint:** lookahead runs at n=200, not n=500. Speed matters here —
4 profiles × candidate evaluation × 2 stages must stay under 10 seconds.

**Done when:** On a sprint→mountain transition, ANCHOR recommends keeping a
GC climber that pure single-stage ANCHOR would sell. That's the proof it works.

**Target: 448 tests passing (+8)**

---

### Session 19 — Calibration Pass
**After:** 5+ Giro stages
**Goal:** Replace hand-tuned constants with data-driven values.

**What to build:**
- `scripts/calibrate.py` — reads `validation_log.md`, computes per-role Brier
  scores across all stages so far, suggests updated `ROLE_TOP15` values
- Interactive confirmation: show current vs suggested, require explicit approval
- Multi-stage Brier tracking: not just per-stage but rolling 5-stage window
- Scenario frequency tracking: predicted prior vs realized frequency per stage type
- Update `SCENARIO_MULTIPLIERS` if realized frequencies diverge from priors
- `calibration_history.json` — audit trail of all constant changes with Brier delta

**Adjustment discipline:**
Only adjust a constant if:
1. It is wrong in the same direction for 3+ stages of the same type
2. The Brier score improves after adjustment (verify on held-out stage)

**Done when:** Brier score improves vs Session 17 baseline. At least one
constant adjusted with documented before/after comparison.

**Target: 458 tests passing (+10)**

---

## Phase 2 — Model Quality (Sessions 20–22)

### Session 20 — Competitor Analysis + Differential Picks
**After:** 5+ stages (ownership data meaningful by then)
**Goal:** Know what the field is doing. Find structural edges.

**The core insight:** In a 50–100k player field, a rider owned by 40% of teams
gives zero relative upside even if they win. You need picks others don't have.

**Differential score:**
```python
differential = ev_rank - ownership_rank
# Positive = underowned relative to EV → buy signal
# Negative = overowned relative to EV → avoid or sell signal
```

**What to build:**
- `ingestion/competitors.py` — scrape top-100 teams from Holdet leaderboard
- `ownership_pct` on `Rider` (from `popularity` endpoint — confirmed or add
  manual input fallback)
- Differential score computed for all riders after each ingest
- `/brief` response includes `differential_picks`: top 5 riders by differential
- Frontend: "Own%" and "Diff" columns in rider table
- Briefing page: differential picks section highlighted separately from EV picks

**Nuance (don't just use raw ownership):**
Combine four signals:
1. EV rank (model quality)
2. Ownership rank (field behavior)
3. Variance rank (upside potential — p95 rank)
4. Downside risk penalty (avoid traps with high EV but blowup risk)

```python
differential_score = (
    0.5 * (ev_rank_pct)           # model says good
    + 0.3 * (1 - ownership_pct)   # field undervalues them
    + 0.2 * (p95_rank_pct)        # has upside
    - 0.2 * (dnf_risk_pct)        # penalize blowup risk (p_dnf rank)
)
```

A rider can be high-EV, low-ownership, high-p95 and still be a trap if their
DNF risk is high or their downside (p10) is deeply negative. The penalty term
catches this. Use `p_dnf` as the primary downside signal — it's already in
`RiderProb`.

**Done when:** Briefing shows differential picks that differ from raw EV picks
on at least one real stage.

**Target: 468 tests passing (+10)**

---

### Session 21 — Optimizer Quality + State Backup
**After:** Session 19 (calibrated probs make this meaningful)
**Goal:** Better squad discovery, faster evaluation. Protect race state.

**⚠️ State backup is not optional.** Once the race starts, losing `state.json`
on Railway means losing bank history, team tracking, and validation data.
This session includes the backup even though it's infrastructure — the failure
cost during a live race is too high to defer to Session 24.

**Four improvements (in priority order):**

**1. Supabase as authoritative state store (pull-forward from Session 24)**
Currently Supabase is a display cache — `state.json` on Railway is the source
of truth. Invert this:
- After every `ingest`/`brief`/`settle`, write state to Supabase atomically
- On Railway startup, if `state.json` is missing, restore from Supabase
- Add `scripts/restore_state.py` — one command recovery

**2. Shared simulation pool (highest priority for optimizer — unlocks everything after)**
All 4 profiles currently run independent `_eval_team` calls.
Fix: run one set of 500 sims per squad, extract p10/EV/p80/p95 from the same
distribution. Reduces optimizer cost by ~4×.

```python
def _eval_team_shared(squad, captain, stage, riders, probs, n, seed) -> TeamSimResult:
    # run once, return full distribution
    # profiles extract their metric from the same result
```

**3. Weighted double-swap sampling**
Current `random.sample(eligible, 2)` misses strong synergy pairs.
Fix: weight by `sim_results[r].percentile_95`:
```python
buy_pair = weighted_sample(eligible, weights=[sim[r].p95 for r in eligible], k=2)
```

**4. NOISE_FLOOR scaling**
Current `NOISE_FLOOR = 20_000` is hardcoded for n=500.
Fix: `NOISE_FLOOR = 450_000 / sqrt(n_sim)` — scales correctly if n_sim changes.

**Done when:** State restores cleanly from Supabase after deleting `state.json`.
Optimizer produces same or better squads in half the wall-clock time.

**Target: 476 tests passing (+8)**

---

### Session 22 — Variance-Aware Profiles + Captain Selection
**After:** Session 21 (shared sim pool required)
**Goal:** Make risk profiles behave like actual strategies, not just metric selectors.

**Current problem:** profiles only differ in which percentile they optimize.
That's not how risk actually works in a large field.

**What profiles should really optimize:**

| Profile  | Behavior                          | Metric                          |
|----------|-----------------------------------|---------------------------------|
| ANCHOR   | Maximize floor, minimize blowups  | p10, minimize downside variance |
| BALANCED | Maximize mean EV                  | EV                              |
| AGGRESSIVE | Maximize upside probability     | p80, maximize std_dev           |
| ALL_IN   | Maximum ceiling, ignore floor     | p95, maximize p_positive        |

**What to build:**
- `_team_metric()` gains variance term:
  ```python
  # ANCHOR: penalize variance
  metric = p10 - 0.2 * std_dev
  # AGGRESSIVE: reward variance
  metric = p80 + 0.1 * std_dev
  ```
- Conditional captain selection (uses shared sim pool from Session 21):
  For each candidate captain, evaluate which maximizes the profile metric
  across the same simulation set — not just individual p10/p95, but team-level
  impact. **Do not explicitly compute correlations.** The simulation already
  encodes correlation implicitly — a rider whose good days align with the team's
  good days will naturally score higher when evaluated inside the team simulation.
  You get 80% of the benefit without the complexity.
- `captain_reasoning` field in `ProfileRecommendation` — show *why* this captain
  was chosen (e.g. "highest team p10 when captain: +12k vs next best")

**Done when:** ANCHOR and ALL_IN recommend different captains on at least 3
historical stages, with documented reasoning.

**Target: 486 tests passing (+10)**

---

## Phase 3 — Strategy Layer (Sessions 23–24)

### Session 23 — Scenario-Conditioned Insights + Intelligence Automation
**After:** Stable full system (probabilities decent, UI clear)
**Goal:** Explainability + automated probability suggestions. Two features,
one session — they share the same API and frontend surface area.

**Part A — Scenario-conditioned EV (pull-forward from Session 25)**
This is not just polish — it's core decision support. Users need to understand
*why* the model recommends what it does, and to reason about "I think it's a
breakaway day" without adjusting sliders blind.

Expose `ev_by_scenario` per rider in the API and frontend:

```json
{
  "rider": "Merlier",
  "ev_total": 82000,
  "ev_by_scenario": {
    "bunch_sprint": 195000,
    "breakaway":    -12000,
    "gc_day":       8000
  }
}
```

Implementation: inside `simulate_team()`, partition simulation runs by scenario
(already tracked in `scenario_counts`). For each scenario bucket, compute the
sub-distribution EV. Cheap — no extra simulations needed.

Frontend: expandable row in the rider table showing EV breakdown by scenario.
This unlocks: "Merlier is only good if it's a bunch sprint — and today might be
a breakaway" as an explicit, model-backed reasoning step.

**Part B — Intelligence automation**
The "Gather Intelligence" button exists (Session 9). Upgrade it.

- `api/intelligence.py` — structured prompt: fetch news + odds → output JSON
  with suggested p_win / p_top15 adjustments per rider
- Response format:
  ```json
  {
    "suggestions": [
      {
        "rider_id": "...",
        "rider_name": "Pogacar",
        "field": "p_win",
        "current": 0.18,
        "suggested": 0.28,
        "confidence": "HIGH",
        "reason": "Bookmaker odds shortened from 4.5 to 3.2 overnight"
      }
    ]
  }
  ```
- Frontend: "Apply suggestions" UI — shows each suggestion, user confirms or
  overrides individually, accepted suggestions flow into `interactive_adjust`
- Audit trail: log accepted vs rejected to `state.json`
- Confidence: HIGH (odds + news), MEDIUM (one signal), LOW (speculation)

**Done when:** `ev_by_scenario` visible in frontend for all riders. On a real
stage, at least 2 intelligence suggestions generated, reviewed, accepted, and
flow through to a changed briefing output.

**Target: 494 tests passing (+8)**

---

### Session 24 — Hardening + Performance
**After:** Mid-race (or earlier if performance issues emerge)
**Goal:** Production-ready for the final week of the Giro.

Note: state backup (Supabase restore) moved to Session 21 — too critical to defer.
This session focuses on performance and operational polish.

**What to build:**
- **n_sim auto-scaling:** bump to 2000 for final 5 stages (higher GC variance,
  shared sim pool from Session 21 makes this feasible without 4× cost increase)
- **Injury/elimination alerts:** email or push notification if any team rider
  shows `isInjured=True` or `isEliminated=True` after ingest
- **Mobile briefing view:** stage-day decisions happen on phone. Optimize
  briefing page for narrow viewport. 4-profile table must be readable on mobile.
- **NOISE_FLOOR empirical tuning:** use actual std_dev from 10+ stages of real
  simulation data to set this precisely (replaces the `450_000 / sqrt(n)` formula
  with an empirically-derived constant).

**Target: 502 tests passing (+8)**

---

### Session 25 — Retrospective + TdF Prep
**After:** Final week of Giro
**Goal:** Learn from the season, prepare for Tour de France.

**What to build:**
- `scripts/retrospective.py` — season summary: Brier scores by stage/role/scenario,
  best/worst model calls, calibration drift over the race, which profile
  performed best at which stage of the race
- Document what worked, what didn't, what to tune for TdF
- Swap game ID and event ID to TdF values
- Confirm all ingestion endpoints still work (Holdet may change API between races)
- Archive Giro state, fresh start for TdF

**Target: 510 tests passing (+8)**

---

## Summary

| Session | Phase | Theme                               | Key unlock                          | Tests |
|---------|-------|-------------------------------------|-------------------------------------|-------|
| 17      | 1     | Live validation + calibration        | Engine confirmed correct            | ~440  |
| 18      | 1     | Minimal lookahead                    | Non-myopic decisions                | ~448  |
| 19      | 1     | Calibration pass                     | Data-driven model constants         | ~458  |
| 20      | 2     | Competitor analysis + differential   | Differential picks + downside risk  | ~468  |
| 21      | 2     | Optimizer quality + state backup     | Shared sims + race state protected  | ~476  |
| 22      | 2     | Variance-aware profiles + captain    | Real risk behavior                  | ~486  |
| 23      | 3     | Scenario insights + intelligence     | Explainability + auto suggestions   | ~494  |
| 24      | 3     | Hardening + performance              | Production-ready final week         | ~502  |
| 25      | 4     | Retrospective + TdF prep             | Season learning + next race         | ~510  |

---

## What actually wins the Giro

**Non-negotiable:**
- ✅ Engine correctness (Session 17)
- ✅ Lookahead (Session 18) — fixes myopia immediately
- ✅ Reasonable calibration (Session 19) — good enough after 5 stages
- ✅ Differential picks (Session 20) — biggest structural edge in large field

**High edge:**
- 🔥 Ownership + EV mismatch (Session 20)
- 🔥 Scenario awareness (already live from Session 16)
- 🔥 Variance-aware profiles (Session 22)

**Nice-to-have:**
- Intelligence automation (Session 23)
- Perfect calibration
- Fancy UI

**Biggest risks:**
1. Over-tuning after Stage 1 (chase noise → worse model)
2. Delaying lookahead (play like everyone else for the first week)
3. Ignoring variance (mean EV is not how you win 50k-player fields)
