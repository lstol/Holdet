# SESSION_ROADMAP.md — Claude Code Build Plan
# Each session has a clear goal, defined inputs, and a done condition.
# Do not start a session until the previous session's done condition is met.
# If debugging becomes circular (3+ failed attempts), stop and bring to Claude.ai.

---

## Session 1 — Scoring Engine

**Goal:** `scoring/engine.py` with full test coverage

**Start by reading:** README.md, RULES.md, ARCHITECTURE.md

**Builds:**
- `scoring/engine.py` — `score_rider()` pure function
- `tests/test_engine.py` — comprehensive unit tests

**Engine must handle all 11 cases:**
1. Stage finish position (1st–15th, and 16th+)
2. GC standing (1st–10th, and 11th+)
3. Jersey bonus — winner/defender at FINISH gets it, not wearer entering stage
4. Sprint + KOM points (+3,000 each, always ≥ 0)
5. Late arrival penalty (truncated minutes × −3,000, cap −90,000)
6. DNF penalty (−50,000 one-time, still gets sprint/KOM, no team bonus)
7. DNS penalty (−100,000 × stages_remaining)
8. Team bonus (60k/30k/20k to active same-team riders)
9. Captain bonus (positive value mirrored to bank, losses NOT amplified)
10. Stage depth bonus / Etapebonus (nonlinear bank deposit by top-15 count)
11. TTT mode (replaces finish, team bonus, late arrival, etapebonus entirely)

**Tests must cover:**
- Sprint stage win with jersey + sprint points + captain
- DNF mid-stage (correct penalty, no team bonus, still sprint/KOM)
- DNS cascade (stages_remaining × −100,000)
- TTT stage (correct scoring, etapebonus not applied)
- Captain positive day → bank deposit
- Captain negative day → no amplification
- Late arrival truncation: 4 min 54 sec = −12,000 (not −15,000)
- Jersey rule: rider wears yellow, loses it at finish → 0 jersey bonus
- Team bonus: active rider gets it, DNF rider on same team does not
- Etapebonus: 4 riders top-15 = 35,000 to bank

**Done when:** All tests pass. Manually verify one known result
from Holdet site if Giro has started.

**Do not build:** Simulator, optimizer, ingestion, CLI

---

## Session 2 — Probability Layer ✓ COMPLETE (2026-04-17)

**Goal:** Probability model + manual adjustment CLI

**Start by reading:** README.md, ARCHITECTURE.md (RiderProb schema)

**Builds:** `scoring/probabilities.py`, `tests/test_probabilities.py`

**Key features:**
- `RiderProb` dataclass with all fields
- `generate_priors(riders, stage)` — model estimates from stage type + heuristics
- `interactive_adjust(probs, stage)` — CLI review + adjustment loop
- Adjustment syntax: `rider_name_fragment field value` (e.g. `vingegaard win 0.50`)
- Display table showing all riders, probs, confidence, source
- `*` flag on manually adjusted values
- Save model and manual probs to `state.json` for audit trail

**Prior generation heuristics (start simple):**
- Stage type → base P(top15) per rider profile
- `isOut=True` → P(dnf) = 1.0, all others = 0
- Price as proxy for quality (higher price → higher expected performance)
- Jersey holders → P(jersey_retain) based on stage type

**Done when:** Can generate prob table for a stage, adjust two riders,
and confirm changes persist to state.json.

**Result:** 84/84 tests passing (59 engine + 25 probability). See SESSION_2_SUMMARY.md.

---

## Session 3 — Monte Carlo Simulator

**Goal:** Per-rider value projections with percentile distributions

**Start by reading:** ARCHITECTURE.md (SimResult schema)

**Builds:** `scoring/simulator.py`

**Key features:**
- `simulate_rider(rider, stage, probs, my_team, captain, n=10000) → SimResult`
- Uses scoring engine internally for each simulation
- Returns: EV, std_dev, p10, p50, p80, p90, p95, p_positive
- `simulate_team(riders, stage, probs, my_team, captain) → dict[id, SimResult]`
- Fast enough: all 8 team riders simulated in < 3 seconds

**Done when:** Can simulate all 8 riders on team and return sorted
table of expected values with percentile spread. Spot-check: a rider
with P(win)=0.3 on a sprint stage should show ~+90k EV from stage
finish alone.

---

## Session 4 — Optimizer + Risk Profiles

**Goal:** Transfer and captain recommendations across all 4 risk profiles

**Start by reading:** ARCHITECTURE.md (risk profiles section)

**Builds:** `scoring/optimizer.py`

**Key features:**
- Four profiles: STEADY, BALANCED, AGGRESSIVE, LOTTERY
- `optimize(riders, my_team, stage, probs, bank, risk_profile, rank, total, stages_remaining)`
- `suggest_risk_profile(rank, total, stages_remaining, target_rank=100)`
- Transfer cost/benefit: fee recovery across remaining stages
- Respects constraints: 8-rider max, 2-per-team rule, budget
- Returns `ProfileRecommendation` with transfers, captain, EV, upside, downside, reasoning

**Side-by-side briefing table output:**
```
                    STEADY    BALANCED  AGGRESSIVE  LOTTERY
Transfers:          0         1         2           3
Captain:            X         X         Y           Z
Expected value:     +42k      +51k      +63k        +38k
Upside (90pct):     +78k      +110k     +190k       +340k
Downside (10pct):   −8k       −22k      −55k        −120k
Transfer cost:      0         −45k      −90k        −135k
```

**Done when:** Given a known team and stage, produces 4-column briefing
that makes intuitive sense. Manually verify at least one recommendation.

---

## Session 5 — API Ingestion

**Goal:** Live rider data from Holdet API with one command

**Start by reading:** README.md, API_NOTES.md

**Builds:** `ingestion/api.py`, `ingestion/base.py`, `.env.example`

**Key features:**
- `fetch_riders(game_id) → list[Rider]`
- Parses items[] + _embedded.persons + _embedded.teams
- Handles cookie expiry gracefully (clear error message)
- Probes candidate endpoints for GC standings / jersey data
- `save_riders(riders, path)` → `data/riders.json`
- `.env.example` with placeholder values

**Also investigate:**
- `/api/games/612/rounds` — may have GC standings
- `/api/games/612/standings` — may have leaderboard
- Document findings in API_NOTES.md

**Done when:** `python3 main.py ingest --stage 1` fetches all riders,
saves to riders.json, prints count and a sample (name, team, value).

---

## Session 6 — CLI Orchestrator + State Management

**Goal:** `main.py` ties all modules into the daily workflow

**Builds:** `main.py`, `config.py`

**Commands:**
```bash
python3 main.py ingest --stage N       # fetch riders from API
python3 main.py brief  --stage N       # run optimizer, generate briefing
python3 main.py settle --stage N       # record results, update state, check engine
python3 main.py status                 # show team, bank, rank
python3 main.py validate --stage N     # compare engine output vs manual input
```

**State management:**
- Load state.json at start of every command
- Save state.json at end of every command
- Never leave state in partially-updated condition (write atomically)

**Done when:** Full daily workflow runs end-to-end. `brief` produces
readable output. `settle` updates rider values and bank.

---

## Session 7 — Reporting + Brier Score Tracking

**Goal:** Pre-stage briefing and post-stage accuracy tracking

**Builds:** `output/report.py`, `output/tracker.py`

**Briefing output includes:**
- Stage profile summary (type, distance, sprint/KOM points)
- Probability table (flagging manual overrides with *)
- 4-profile recommendation table (transfers, captain, EV, upside, downside)
- Plain-English reasoning per profile
- Auto-suggested profile with explanation
- DNS warnings for any team riders with isOut=True

**Tracker output includes:**
- Actual vs predicted value per rider
- Brier score this stage: model vs manual
- Season Brier score running total
- "You beat the model on N/M stages"

**Done when:** Briefing is clear enough to make a real pick decision from.
Tracker shows meaningful calibration data after 3+ stages.

---

## Session 8 — Giro Validation + Tuning

**Goal:** Confirm engine matches Holdet site. Tune probability model.

**Activity:**
- Run settle for completed Giro stages
- Compare engine ValueDelta vs actual Holdet value changes
- Log all discrepancies in `tests/validation_log.md`
- Fix any engine bugs found
- Review which probability signals are most predictive
- Tune prior generation heuristics based on real data

**Done when:** Engine matches Holdet output for 5 consecutive stages.
At least one probability model improvement documented.

### Session 8 addition: Odds-based probability inputs

Improve prior generation by replacing flat heuristics with betting-odds-derived
probabilities. Two input types are available:

1. Outright win odds — seed p_win and p_top3 for top 10-15 riders per stage
2. Head-to-head odds — refine relative ordering between rider pairs, especially
   useful for sprinters and climbers where outright market is shallow

Workflow to implement:
- Small CLI that accepts (rider_fragment, odds) pairs and converts to implied
  probabilities (1/odds, normalised to remove bookmaker overround)
- H2H pairs can be chained to build consistent probability ladder across a group
- Output feeds directly into interactive_adjust() as pre-filled starting point
  rather than flat model priors
- User reviews and confirms before saving to state.json

This is the single highest-impact probability model improvement available.

---

## Session 9 — TdF Frontend (React + Supabase)

**Goal:** Shareable web interface for Tour de France

**Stack:** React frontend + Supabase backend + existing Python engine

**Supabase schema mirrors ARCHITECTURE.md section 7.**

**Pages:**
1. Briefing — 4-profile table, probability adjustment
2. My Team — squad, values, captain, bank
3. History — value chart, Brier score
4. Riders — full market with filters

**Auth:** Supabase auth for multi-user support (shareable with others)

**Done when:** Can run full pre-stage workflow from browser.
Other users can log in and use the tool for their own team.

---

## Session Continuity Rules

1. Start each session by reading README.md, RULES.md, ARCHITECTURE.md
2. Run `python3 -m pytest` to confirm existing tests still pass
3. Build only what the session scope defines
4. End with all tests passing and state.json valid
5. Commit to GitHub at end of each session
6. If stuck after 3 attempts → stop, bring code + problem to Claude.ai
