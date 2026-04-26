# SESSION_ROADMAP.md — Claude Code Build Plan
# Each session has a clear goal, defined inputs, and a done condition.
# Do not start a session until the previous session's done condition is met.
# If debugging becomes circular (3+ failed attempts), stop and bring to Claude.ai.
# Last updated: 2026-04-26 (Session 21 complete: unified probability shaping + frontend fixes)

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

## Session 5 — API Ingestion ✓ COMPLETE (2026-04-17)

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

**Result:** 219/219 tests passing. `fetch_riders("612", cookie)` confirmed
returning 91 riders live. Probe findings documented in API_NOTES.md.
See SESSION_5_SUMMARY.md.

---

## Session 6 — CLI Orchestrator + State Management ✓ COMPLETE (2026-04-17)

**Goal:** `main.py` ties all modules into the daily workflow

**Builds:** `main.py`, `config.py`

**Commands:**
```bash
python3 main.py ingest --stage N       # fetch riders from API
python3 main.py brief  --stage N       # run optimizer, generate briefing
python3 main.py settle --stage N       # record results, update state, check engine
python3 main.py status                 # show team, bank, rank
```

**State management:**
- Load state.json at start of every command
- Save state.json at end of every command
- Never leave state in partially-updated condition (write atomically)

**Session 5 findings that affect Session 6:**
- `fetch_my_team()` HTML scraping confirmed working (HTTP 200, 284k chars,
  `initialLineup` / `initialBank` / `initialCaptain` all present).
  The `ingest` command should call this in addition to `fetch_riders()` to
  populate `in_my_team`, `is_captain`, and `bank` in state.json automatically.
- Confirmed rich fields available in `initialLineup[]`: `captainPopularity`,
  `owners`, `captainOwners`, `isInjured`, `isEliminated`, `favorite` (slot 1–8).
  Store these in state.json — useful for briefing output and injury alerts.
- **⚠️ AWSALB is IP-sticky.** Cookie only works from the machine it was captured
  on. Tests that call the live API must be skipped in CI / other environments.
  Use `pytest -m "not live"` pattern or mock all HTTP for the standard test suite.

**Done when:** Full daily workflow runs end-to-end. `brief` produces
readable output. `settle` updates rider values and bank.

**Result:** 265/265 tests passing. Live `ingest --stage 1` confirmed: 91 riders,
8-rider team loaded (Vingegaard 17.5M captain Tarozzi, bank 4.5M). `status` working.
See SESSION_6_SUMMARY.md.

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

### Session 8 addition: Realistic probability fixture verification

Verify optimizer ANCHOR profile behaviour with realistic flat-stage probability
inputs (GC riders earn positive EV from standing value even when irrelevant to
stage outcome). Confirm ANCHOR retains GC top-10 riders for the right reason:
guaranteed per-stage GC income, not artificially inflated p10 in test fixtures.

On a flat stage:
  - GC top-10 rider: finishes in peloton, earns GC standing value (60–100k),
    p10 is positive (~60k) because standing income is guaranteed
  - Top sprinter: higher EV and p95 ceiling but more variable floor because
    crashes and missed sprints can happen
  - ANCHOR correctly keeps GC riders because their floor is higher — driven by
    reliable GC standing income, not inflated test numbers

### Session 8 addition: Post-Stage-1 API endpoint investigation

Session 5 live probe (pre-race) found:
- `/api/games/612/standings` returns `[]` — check again after Stage 1, likely
  to contain GC standings once racing starts. If confirmed, extend `fetch_riders()`
  or add a separate `fetch_standings()` to populate `gc_position` automatically
  (currently requires manual input).
- `/api/games/612/rounds` and `/statistics` return HTML (Next.js pages), not JSON.
  These are not usable as data endpoints.
- `items[].popularity` is `null` pre-race — check after Stage 1. May contain
  ownership percentage useful for contrarian/differential pick strategy.
- `items[].positionId` is 264 for all riders — likely a single "cyclist" type.
  Confirm this doesn't differentiate sprinters/climbers.

Priority: confirm whether `/standings` provides GC data, as this would eliminate
the last remaining manual input step in the ingestion pipeline.

### Session 8 addition: Odds-based probability inputs ✓ DONE

`scoring/odds.py` — bookmaker odds → normalised implied probabilities.

- `decimal_to_implied`, `normalise`, `odds_to_p_win`, `h2h_to_prob`
- `apply_odds_to_probs` — patches probs dict, derives full hierarchy, sets source="odds"
- `cli_odds_input` — interactive CLI: outright + H2H entry before `interactive_adjust()`
- `--odds` flag on `python3 main.py brief` activates the odds input step

16 new tests. 310/310 passing. See SESSION_8_SUMMARY.md.

---

## Session 9 — Giro 2026 Frontend (React + Supabase) ✓ COMPLETE (2026-04-19)

**Goal:** Shareable web interface for Giro d'Italia 2026

**Stack:** Next.js 16 + Tailwind CSS + Supabase + recharts

**Supabase project:** `xcmyypnywmqdofukkvga` (eu-west-1)
**Live URL:** https://holdet.syndikatet.eu (Netlify, Let's Encrypt SSL)

**Part A — Pre-race engine improvements:**
- `_rider_type()` value-bracket classification (gc/sprinter/specialist/domestique)
- ANCHOR fixture tests (`TestAnchorRealisticFixtures`, 3 tests)
- `scripts/fetch_stage_images.py` — downloads Giro stage profile images

**Part C — Frontend (Part B deferred to after Giro start May 9):**
- 8-table Supabase schema with RLS (user-scoped + shared tables)
- `scripts/sync_to_supabase.py` — upserts all local state after each CLI command
- `scripts/keep_alive.py` + `.github/workflows/keep_alive.yml` — prevents free-tier pause
- 5-page Next.js app: `/briefing`, `/team`, `/history`, `/riders`, `/stages`
- Gather Intelligence: Anthropic claude-sonnet-4-20250514 + web_search tool
- Supabase Auth: email/password, multi-user, RLS enforced

**316/316 tests passing. See SESSION_9_SUMMARY.md.**

---

## Session 10 — FastAPI Bridge + Railway Deployment ✓ COMPLETE (2026-04-19)

**Goal:** Add FastAPI server so frontend buttons trigger Python CLI actions.
Deploy to Railway so holdet.syndikatet.eu works from any device.

**What was built:**
- `api/server.py` — FastAPI with endpoints: /status, /ingest, /brief, /settle, /team, /sync
- `railway.json` — Railway deployment config
- `requirements.txt` — all Python dependencies
- `scripts/start_api.sh` — local dev start script
- `tests/test_api.py` — 25 new tests (341 total)
- Frontend buttons wired: [Refresh Riders], [Run Briefing], [Update My Team], [Settle Stage N]

**Deployment:**
- FastAPI: Railway (auto-deploys from GitHub main branch)
- Frontend: Netlify (holdet.syndikatet.eu)
- Auth: Holdet email/password login — auto-login implemented in Session 11

341/341 tests passing. See SESSION_10_SUMMARY.md.

---

## Session 11 — Auto-Login + Validate Scaffolding ✓ COMPLETE (2026-04-20)

**Goal:** Replace manual cookie with email/password auto-login. Scaffold
live engine validation for after Giro start May 9.

**What was built:**
- `ingestion/api.py` — 3-step NextAuth login (GET /csrf → POST /signin → confirm)
- `get_session()` — module-level cached session with auto-retry on 401
- `_SESSION_CONFIRM_URL` changed to `/api/games/612/players` (confirmed working)
- `config.py` — `get_cookie()` removed; `get_email()` + `get_password()` added
- `api/server.py` + `main.py` — all cookie refs replaced with `get_session()`
- `main.py validate --stage N` — compares engine delta vs actual Holdet delta
- `tests/test_autologin.py` — 12 tests
- `tests/test_validate.py` — 7 tests

**361/361 tests passing. See SESSION_11_SUMMARY.md.**

---

## Session 12 — Railway Hardening + Auth UX ✓ COMPLETE (2026-04-20)

**Goal:** Fix all blocking issues preventing the Railway-deployed FastAPI server
from ingesting and syncing correctly, and show a visible login prompt on gated
frontend pages instead of a blank screen.

**Fixes shipped:**

1. **Railway `$PORT` binding** (`railway.json`)
   - Start command was hardcoded to `--port 8000`; Railway injects a dynamic
     `$PORT`. Changed to `--port ${PORT:-8000}`.

2. **Session confirm URL returns 404** (`ingestion/api.py`)
   - `/api/session` does not exist on the Holdet nexus API.
   - Replaced `_SESSION_CONFIRM_URL` with `/api/games/612/players` — a confirmed
     auth-gated endpoint (200 = session valid, 401/403 = failed).

3. **Data files missing on Railway** (`api/server.py`, `data/.gitkeep`)
   - `_load_state()` defaults changed to `current_stage=1`, `bank=50_000_000`
     matching the actual race start values.
   - `_save_state()` already creates `data/` via `os.makedirs` (confirmed, no
     change needed).
   - Added `data/.gitkeep` so the directory exists on fresh clones/Railway deploys.
   - `.gitignore` was already correct (ignores `state.json`/`riders.json` only).

4. **`sync_riders()` format mismatch** (`scripts/sync_to_supabase.py`)
   - `save_riders()` writes a dict keyed by holdet_id, not a list or `{"riders": [...]}`.
   - Added a third branch: `riders_list = list(raw.values())` as the fallback.

5. **`user_id` missing in state on Railway** (`scripts/sync_to_supabase.py`)
   - `sync_all()` now falls back to `os.getenv("HOLDET_USER_ID")` before giving up.
   - Set `HOLDET_USER_ID` as a Railway env var to fix the silent sync no-op.

6. **Frontend login prompt** (4 pages)
   - Gated pages (`/briefing`, `/team`, `/riders`, `/history`) previously showed
     a blank screen to logged-out users.
   - Added `const [user, setUser] = useState<any>(null)` + `setUser(user)` in each
     `useEffect` load function.
   - JSX now shows an orange "Sign in" button before rendering any content when
     `user` is null.
   - `/stages` has no user gate — left unchanged.

**No new tests added** (infrastructure/config fixes only).

---

## Session 13 — UI Fixes + Briefing Improvements ✓ COMPLETE (2026-04-22)

**Goal:** Fix 9 UI/UX issues identified after Session 12 deployment, plus several
post-session bug fixes discovered during live testing.

**Fixes shipped:**

1. **HTTPS hardening** (`frontend/next.config.ts`, `frontend/netlify.toml`)
   - Added `Strict-Transport-Security: max-age=63072000` header via Next.js `headers()` config
   - Added HTTP→HTTPS redirect in `netlify.toml` (`301 force`)

2. **Nav auth state** (`frontend/components/Nav.tsx`)
   - On mount, fetches current user via `supabase.auth.getUser()`
   - Logged in: shows truncated email + "Sign out" button → clears session, redirects `/auth`
   - Logged out: shows "Sign in" link

3. **Button label** (`frontend/app/riders/page.tsx`)
   - "Ingest" → "Refresh Riders"

4. **Intelligence API improvements** (`frontend/app/api/intelligence/route.ts`)
   - Model: `claude-sonnet-4-20250514` → `claude-sonnet-4-5`
   - `max_tokens`: 2000 → 4000 (prevents response cutoff)
   - Replaced narrow source requirements with 5 broad generic searches
   - JSON extraction: regex for code fences before string replace fallback
   - Hard prompt instruction: output bare `{...}` JSON only

5. **Team name in briefing tables** (`frontend/app/briefing/page.tsx`)
   - Team simulation table: added "Team" column via `rider.team_abbr`
   - Per-profile transfer list: muted team abbreviation next to rider name

6. **Sync to Supabase button removed** (`frontend/app/team/page.tsx`)
   - Auto-sync already runs after every ingest/brief/settle on Railway

7. **Update My Team error visibility** (`frontend/app/team/page.tsx`)
   - `console.error` in `saveTeam()` catch block
   - Yellow warning shown when `riders.length === 0 && user`

8. **Stage profile images** (`frontend/app/stages/page.tsx`, `frontend/app/briefing/page.tsx`)
   - Full-height image: `w-full h-auto rounded-lg` (removed `max-h-48 object-cover`)
   - Added `vertical_meters`, `start_location`, `finish_location` to stage detail grid

9. **Briefing result persistence** (`frontend/app/briefing/page.tsx`)
   - `sessionStorage` → `localStorage` under key `holdet_briefing_result`
   - Survives tab switches, browser close, and cross-session

**Post-session bug fixes:**

- **`parseJsonField` helper** — Supabase returns `my_team`, `stages_completed`,
  `jerseys` as JSON strings in some paths. Added defensive helper applied across
  `briefing`, `team`, `stages`, and `riders` pages.
- **`sync_to_supabase.py`** — `my_team`, `stages_completed`, `jerseys` now stored
  as native arrays (not `json.dumps` strings).
- **Optimizer budget-aware knapsack fill** — when building from empty team, added
  `_cheapest_n_eligible(slots_left)` budget reservation check before each pick,
  plus emergency fill as last resort.
- **`team_note` in brief response** (`api/server.py`) — `/brief` includes a note
  when `my_team` is empty.
- **`load()` error boundaries** — `team/page.tsx` `load()` wrapped in `try/catch`.

**Tests:** 363/363 passing (+1 new optimizer test class with 2 tests).

---

## Session 14 — Simulation Layer Rebuild ✓ COMPLETE (2026-04-22)

**Goal:** Replace rider-level Monte Carlo with stage-level simulation.
Current simulate_rider() produces independent draws which breaks etapebonus,
team bonus, and captain bonus — these only work correctly at team level.

**What was built:**

### simulate_stage_outcome(stage, riders, probs) → StageResult
1. Sample scenario based on stage type:
   - flat:     bunch_sprint=0.65, reduced_sprint=0.20, breakaway=0.15
   - hilly:    bunch_sprint=0.25, reduced_sprint=0.25, breakaway=0.30, gc_day=0.20
   - mountain: gc_day=0.70, breakaway=0.25, reduced_sprint=0.05
   - itt/ttt:  deterministic
2. Adjust rider weights conditionally on scenario:
   - bunch_sprint → boost sprinters, suppress GC riders
   - gc_day → boost climbers/GC, suppress sprinters
   - breakaway → boost domestiques/attackers
3. Sample finish order via Plackett-Luce (weighted sampling without replacement)
   Guarantees: 1 winner, no duplicates, valid top-15
4. Assign sprint/KOM points consistent with scenario
5. Return full StageResult

### simulate_team(team, captain, stage, riders, probs, n=5000) → TeamSimResult
For each of n simulations:
- result = simulate_stage_outcome(...)
- score all 8 team riders against result using score_rider()
- apply captain bonus dynamically (best performer in that simulation)
- sum total team value incl. etapebonus (once), team bonus
Return: EV, p10, p80, p95 at TEAM level

**Sanity checks required:**
- Flat stage: sprinters dominate team p95, GC riders stabilize p10
- Mountain stage: GC riders dominate all percentiles
- ALL_IN produces meaningfully wider distribution than ANCHOR
- Etapebonus visible: team EV > sum of individual rider EVs

---

## Session 15 — Team-Level Optimizer + Role Display + Probability Distribution ✓ COMPLETE (2026-04-22)

**Goal:** Wire the optimizer to use team-level Monte Carlo simulation for squad evaluation.
Add multi-role rider classification. Surface probability distributions and scenario stats in the frontend.

**What was built:**
- A1: `simulate_team()` captain fix — declared captain, not dynamic best performer
- A2–A6: `_eval_team()` with memoization, `_team_metric()`, greedy + double-swap optimizer, hybrid EV+p95 candidate filtering
- A7–A8: `ProfileRecommendation.team_result`, `scenario_stats` in `/brief`
- B: `_rider_roles()` returning up to 3 roles, exposed in API `team_sims`
- C: Frontend — `RoleBadge` pills (GC/Sprint/Climber/Breakaway/TT/Dom), `DistributionBar` (p10/p50/EV/p80/p95), team EV/p10/p80/p95 columns in 4-profile table, scenario stats line

**407/407 tests passing (+16 new). See SESSION_15_SUMMARY.md.**

---

## Session 15-Fixes — Cache Key, Threshold, Role Precedence, Etapebonus Diagnostics ✓ COMPLETE (2026-04-22)

**Goal:** Five targeted fixes to Session 15 — no new features, no scope creep.

**What was fixed:**
- Fix 1: `_eval_team` enforces `tuple(sorted(squad_ids))` key internally — removes implicit caller contract
- Fix 2: `NOISE_FLOOR = 20_000` constant; `_eval_swap` and `_try_double_swaps` use `max(1% × metric, NOISE_FLOOR)` — prevents noise acceptance when metric is small
- Fix 3: `_rider_roles()` restructured with `specialist_assigned` flag — probability signal overrides value bracket, no duplicates possible
- Fix 4: `TeamSimResult.etapebonus_ev` + `etapebonus_p95`; exposed in API and as "Eta EV" column in 4-profile frontend table
- Fix 5: `scenario_stats` → `scenario_priors` in API + frontend

**415/415 tests passing (+8 new). See SESSION_15_FIXES_SUMMARY.md.**

---

## Session 16 — Scenario-Aware Simulation + Override + Multi-Stage Architecture Scaffold ✓ COMPLETE (2026-04-24)

**Goal:** Make the simulation responsive to scenario assumptions, expose scenario control to the user,
and prepare a clean foundation for future multi-stage optimization. Do NOT implement multi-stage optimization.

**What was built:**
- Pre-flight: diff-based transfer reporting — Stage 1 empty team shows buy 8/sell 0 (no phantom sells)
- A: `_resolve_scenarios()` + `_normalize_scenarios()` + upgraded `_sample_scenario(dict, rng)`
- A: `simulate_team()` accepts `scenario_priors`, samples scenario once per sim, tracks `scenario_counts`
- A: `TeamSimResult.scenario_stats` populated with realized scenario frequencies
- A: `simulate_stage_outcome()` accepts optional `scenario` + `roles_map` (backward-compatible)
- B: `_build_weights()` upgraded to use `_rider_roles()` via pre-computed `roles_map` (max multiplier across all roles); `_rider_type()` no longer called from `_build_weights`
- C+D: API `BriefRequest.scenario_priors`, `_resolve_scenarios` called at request time, threaded through full optimizer stack; response includes both `scenario_priors` and `scenario_stats`
- E: Frontend `team_note` yellow banner; scenario sliders with proportional normalization + 500ms debounce; prior vs realized stats display
- G: `docs/MULTI_STAGE_ARCHITECTURE.md` — full scaffold (state, action, transition, evaluation function signatures + design principles + blockers)

**429/429 tests passing (+14 new). See SESSION_16_SUMMARY.md.**

---

## Two tracks running in parallel (Sessions 17–25)

**Track A — Reality alignment**
Validate engine vs Holdet → calibrate probabilities → tune multipliers

**Track B — Decision quality**
Stage intent → transfer-awareness → lookahead → differential picks

These run in parallel, not sequentially. Track B does not wait for Track A
to be perfect.

**Core principle (locked after Session 18 reframe):**

The system has three layers — build in order:
1. **State** — what is true right now ✅ Sessions 1–17
2. **Intent** — what kind of decision does this stage require ← Session 18
3. **Optimization** — best action given state + intent ← Session 20+

Lookahead on top of a system with no intent understanding gives confident wrong
answers. Intent first, optimization second.

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

### Session 18 — Intelligence-Conditioned Decision Layer (ICDL v1) ✓ COMPLETE (2026-04-24)
**After:** Session 17 engine validated (|diff| < 5k per rider)
**Goal:** Give the system understanding of what kind of decision each stage
is asking for. This is the missing layer between state and optimization.

**Why this before lookahead:**
Lookahead on a system with no intent understanding gives confident wrong answers.
Intent first → lookahead (Session 20) lands on correct decision structure.

**One pipeline. Four internal parts. No new sessions.**

```
raw_stage
    ↓
compute_stage_intent(stage, gc_state, next_stage)      [18A]
    ↓
apply_intelligence_overrides(intent, signals)           [18B]
    ↓
generate_priors()                                       [unchanged]
    ↓
simulate()                                              [unchanged]
    ↓
evaluate_team(net_ev with λ, apply_intent_to_ev)        [18C]
    ↓
select_captain(intent-weighted)                         [18C]
    ↓
CLI: --override, --lambda, --lookahead flags            [18D]
```

#### 18A — StageIntent

**New file: `scoring/stage_intent.py`**

```python
@dataclass
class StageIntent:
    win_priority: float        # how much does winning matter today?
    survival_priority: float   # how bad is DNF / gruppo risk?
    transfer_pressure: float   # how urgently should we rotate?
    team_bonus_value: float    # is holding a full team worth it today?
    breakaway_likelihood: float

def compute_stage_intent(
    stage: Stage,
    gc_state: GCState,
    next_stage: Stage | None
) -> StageIntent:
    ...
```

Purely deterministic. Driven by stage type, GC standings tightness,
next-stage profile, rider pool structure. No randomness. Fully testable.

Example outputs:
```
Flat stage (sprint likely):
  win_priority=0.9, survival_priority=0.3,
  transfer_pressure=0.4, team_bonus_value=0.8, breakaway_likelihood=0.2

Mountain stage after flat:
  win_priority=0.7, survival_priority=0.95,
  transfer_pressure=0.9, team_bonus_value=0.1, breakaway_likelihood=0.6
```

#### 18B — Intelligence Overrides (signals, not numeric edits)

**Critical design rule:** Overrides are EVENT SIGNALS, not direct mutations
of intent fields. This keeps calibration meaningful and EV changes attributable.

```python
def apply_intelligence_overrides(
    intent: StageIntent,
    signals: dict
) -> StageIntent:
    # translate signals → intent deltas
    # never allow direct numeric field assignment from outside
    ...
```

**Override format (locked):**
```json
{
  "stage_3": {
    "signals": {
      "crosswind_risk": "high",
      "sprint_train_disruption": "likely"
    },
    "reason": "DS confirmed crosswind — echelon risk"
  }
}
```

**Signal → intent delta map** (lives in `scoring/stage_intent.py` as dict,
one line to add new signals in Session 23):

| Signal | Intent fields affected |
|--------|----------------------|
| `crosswind_risk: high` | `breakaway_likelihood ↑`, `survival_priority ↑` |
| `sprint_train_disruption: likely` | `win_priority ↓` for sprinters, `breakaway_likelihood ↑` |
| `gc_rider_illness: confirmed` | `survival_priority ↑`, `transfer_pressure ↑` |
| `stage_shortened: confirmed` | `team_bonus_value ↓` |

Override files live in `overrides/stage_N.json`. Git-tracked. Auditable.
Reason field is required (reject overrides without it).

#### 18C — Intent-Weighted EV + Captain

**EV function (locked):**
```python
adjusted_ev = base_ev * (1 + 0.3 * intent.win_priority)
transfer_penalty *= (1 + intent.transfer_pressure)
net_ev = adjusted_ev - transfer_penalty + LAMBDA_TRANSFER * next_stage_ev
```

Intent shapes EV for all three core heuristics:
1. `win_priority` → stage winner picks
2. `transfer_pressure` → transfer cost penalty
3. `survival_priority` → sprinter survival risk (structural, not hard rule)

**Sprinter survival** (structural signal, not a hard exclusion rule):
```python
if sprinter_survival_risk(rider, gc_state, stage_profile) == HIGH:
    expected_stage_penalty = -0.8 * p_dnf_equivalent
```

**λ (transfer discount):**
```python
LAMBDA_TRANSFER = 0.85   # in config.py
```
- CLI: `--lambda 0.7` overrides default
- λ is a strategy knob, not a model parameter
- Do NOT tie λ to calibration metrics
- Session 21+: candidate for volatility-based auto-adaptation

**Captain scoring:**
```python
# BALANCED captain uses simulation EV with intent-weighted p95 nudge:
# score = expected_value + intent.win_priority * percentile_95 * 0.1
# ANCHOR: argmax(p10)   ALL_IN: argmax(p95)   — both unchanged by intent
```
Simulation-based (not probability formula). Intent nudges BALANCED only.

#### 18D — CLI

```bash
python3 main.py brief --stage 5
python3 main.py brief --stage 5 --lookahead
python3 main.py brief --stage 5 --override overrides/stage_5.json
python3 main.py brief --stage 5 --lookahead --override overrides/stage_5.json --lambda 0.75
```

**Tests (+8 → ~448 passing):**
```python
def test_compute_stage_intent_flat_stage_high_win_priority()
def test_compute_stage_intent_mountain_stage_high_survival()
def test_apply_overrides_signal_crosswind_raises_breakaway()
def test_apply_overrides_requires_reason_field()
def test_net_ev_lambda_zero_equals_unadjusted_ev()        # key regression guard
def test_intent_win_priority_scales_ev_correctly()
def test_captain_selection_uses_intent_weight()
def test_sprinter_penalty_applied_on_high_survival_risk()
```

`test_net_ev_lambda_zero_equals_unadjusted_ev` is the regression guard:
λ=0 and win_priority=0 must reproduce previous single-stage EV exactly.

**Done when:**
- [ ] `compute_stage_intent()` correct for flat, mountain, ITT
- [ ] Overrides rejected without `reason` field
- [ ] Signal → intent delta map handles all 4 signals in table above
- [ ] `net_ev` with λ=0, win_priority=0 matches pre-Session-18 EV (regression)
- [ ] `--override` works end-to-end on a real stage
- [ ] Captain output changes vs static `argmax(p_win)` on ≥1 real stage
- [ ] 448+ tests passing
- [ ] `SESSION_18_SUMMARY.md` created
- [ ] This roadmap updated

**Commit:** `Session 18: ICDL v1 — stage intent, signal overrides, transfer-aware EV, intent-weighted captain`

---

### Session 19 — Calibration Pass
**After:** 5+ Giro stages
**Goal:** Replace hand-tuned constants with data-driven values.

**What to build:**
- `scripts/calibrate.py` — reads `validation_log.md`, computes per-role Brier
  scores across all stages so far, suggests updated `ROLE_TOP15` values
- Interactive confirmation: show current vs suggested, require explicit approval
- Multi-stage Brier tracking: rolling 5-stage window
- Scenario frequency tracking: predicted prior vs realized frequency per stage type
- `calibration_history.json` — audit trail of all constant changes with Brier delta

**Adjustment discipline:**
Only adjust a constant if:
1. Wrong in same direction for 3+ stages of the same type
2. Brier score improves after adjustment (verify on held-out stage)

**Note:** Session 19 does NOT block Session 20. Run in parallel once
5 stages of data are available.

**Target: 458 tests passing (+10)**

---

## Phase 3 — Decision Optimization (Sessions 20–22)

### Session 20 — Lookahead Optimizer v1
**After:** Session 18 ICDL live + Session 17 engine validated
**Goal:** Non-myopic decisions using intent-aware multi-stage EV

**Now safe to build because:**
- `net_ev` is already transfer-aware (Session 18)
- `StageIntent` already encodes race context (Session 18)
- Lookahead calls `evaluate_action_multistage()` using both
- Math is simple because decision structure is already correct

**What to build:**
- `evaluate_action_multistage(state, action, stages, riders, probs, horizon=2)`
- Stage N: full sim at current `n_sim`
- Stage N+1: fast lookahead at n=200 (speed: 4 profiles × candidates × 2 stages < 10s)
- Uses `StageIntent` for both stages — no blind EV rollup
- Discount α = 0.85 (tune after 3+ stages; independent of λ)
- Briefing output: "over 2 stages" framing shows why recommendation differs

**Done when:** BALANCED+lookahead produces different recommendation from
BALANCED on ≥1 real stage, with documented reason.

**Target: 468 tests passing (+10)**

---

### Session 21 — Unified Probability Shaping + Frontend Fixes ✅ COMPLETE
**Goal:** Formalize two-layer architecture; fix 4 frontend bugs

**What was built:**
- `scoring/probability_shaper.py` — `ProbabilityContext`, `apply_probability_shaping()`, 6-layer pipeline
- `STAGE_ROLE_MULTIPLIER` — Carapaz fix: climbers penalized on sprint stages
- `config.get_n_sim()` — auto-scale sim count by race position
- Optimizer DESIGN INVARIANT comment + `eval_fn` wired into double-swap loop
- API `prob_shaping_trace` in every `/brief` response
- CLI and API use identical `ProbabilityContext` pipeline
- Frontend: sliders before first run (C1), re-sim overlay (C2), riders cache (C3), tab-switch state (C4)
- `tests/test_probability_shaper.py` — 5 new tests
- `TestSession21Optimizer` — 3 new tests

**Tests: 510 passing (+8)**

---

### Session 22 — Variance-Aware Profiles + Captain System
**Goal:** Real risk behavior encoded in profiles

**What to build:**
- `_team_metric()` gains variance term:
  ```python
  # ANCHOR: penalize variance
  metric = p10 - 0.2 * std_dev
  # AGGRESSIVE: reward variance
  metric = p80 + 0.1 * std_dev
  ```
- Captain evaluated inside team sim (shared pool from Session 21)
- `captain_reasoning` field in `ProfileRecommendation`
- Note: simulation already encodes correlation implicitly — no explicit
  correlation computation needed (80% of benefit, free)

**Target: 486 tests passing (+10)**

---

## Phase 4 — Competitive Edge Layer (Sessions 23–25)

### Session 23 — Intelligence Automation + Differential Picks
**Goal:** Structured external signals + ownership-aware recommendations

**Differential picks (biggest structural edge in 50–100k field):**
```python
differential_score = (
    0.5 * ev_rank_pct           # model says good
    + 0.3 * (1 - ownership_pct) # field undervalues them
    + 0.2 * p95_rank_pct        # has upside
)
```

**Intelligence automation:**
- Upgrade "Gather Intelligence" from free-text to structured JSON suggestions
- Auto-research pipeline outputs signal candidates for `overrides/stage_N.json`
- User reviews + approves before brief runs
- Audit trail: accepted vs rejected suggestions logged to `state.json`

**ev_by_scenario exposure (free — partition existing scenario_counts):**
```json
{
  "rider": "Merlier",
  "ev_total": 82000,
  "ev_by_scenario": {
    "bunch_sprint": 195000,
    "breakaway": -12000,
    "gc_day": 8000
  }
}
```

**Target: 494 tests passing (+8)**

---

### Session 24 — Hardening + Performance
**Goal:** Production-ready final week

**What to build:**
- State recovery from Supabase if Railway drops state.json
- Injury/elimination alerts after ingest
- Mobile briefing view
- NOISE_FLOOR empirical tuning from 10+ stages of real data

**⚠️ Pull forward if:** Railway drops state.json even once.

**Target: 502 tests passing (+8)**

---

### Session 25 — Retrospective + TdF Switch
**Goal:** Learn from the Giro, prepare for Tour de France

**What to build:**
- `scripts/retrospective.py` — Brier scores by stage/role/scenario,
  best/worst model calls, calibration drift over the race
- Swap game ID and event ID to TdF values
- Confirm all ingestion endpoints still work
- Archive Giro state, fresh start

**Done when:** `python3 main.py ingest --stage 1` against TdF game ID returns
clean briefing.

**Target: 510 tests passing (+8)**

---

## Session summary

| Session       | Status                    | Theme                                | Key unlock                           | Tests |
|---------------|---------------------------|--------------------------------------|--------------------------------------|-------|
| 1             | ✓ complete                | Scoring engine                       | Core value calculation               | 59    |
| 2             | ✓ complete (2026-04-17)   | Probability layer                    | Model priors + manual adjust         | 84    |
| 3             | ✓ complete                | Monte Carlo simulator                | Per-rider EV projections             | —     |
| 4             | ✓ complete                | Optimizer + risk profiles            | 4-profile transfer recs              | —     |
| 5             | ✓ complete (2026-04-17)   | API ingestion                        | Live rider data                      | 219   |
| 6             | ✓ complete (2026-04-17)   | CLI orchestrator                     | Full daily workflow                  | 265   |
| 7             | ✓ complete                | Reporting + Brier tracking           | Calibration data                     | —     |
| 8             | ✓ complete                | Validation + odds inputs             | Engine confirmed + odds              | 310   |
| 9             | ✓ complete (2026-04-19)   | Frontend (React + Supabase)          | Web interface live                   | 316   |
| 10            | ✓ complete (2026-04-19)   | FastAPI + Railway                    | Any-device access                    | 341   |
| 11            | ✓ complete (2026-04-20)   | Auto-login + validate                | No more manual cookie                | 361   |
| 12            | ✓ complete (2026-04-20)   | Railway hardening                    | Deployment stable                    | 361   |
| 13            | ✓ complete (2026-04-22)   | UI fixes + briefing                  | Usable on race day                   | 363   |
| 14            | ✓ complete (2026-04-22)   | Simulation layer rebuild             | Coherent stage outcomes              | —     |
| 15            | ✓ complete (2026-04-22)   | Team-level optimizer                 | Squad-level EV + role display        | 407   |
| 15-Fixes      | ✓ complete (2026-04-22)   | Cache, threshold, role fixes         | Correctness + etapebonus diag        | 415   |
| 18-Fixes      | ✓ complete (2026-04-25)   | Naming, aliases, guards, reasoning   | ICDL stabilized for Session 19       | 464   |
| 16            | ✓ complete (2026-04-24)   | Scenario-aware simulation            | User scenario control                | 429   |
| 17            | planned                   | Live validation + calibration        | Engine confirmed correct             | ~440  |
| 18            | ✓ complete (2026-04-24)   | ICDL v1 — stage intent               | System understands race meaning      | 458   |
| 19            | ✓ complete (2026-04-25)   | Calibration pass                     | Data-driven model constants          | 477   |
| 19.5          | ✓ complete (2026-04-26)   | Rider-level expert adjustments       | Ephemeral signal injection           | 485   |
| 19.6          | ✓ complete (2026-04-26)   | Rider identity stabilization layer   | Static profile multipliers           | 491   |
| 20            | ✓ complete (2026-04-26)   | Identity-aware lookahead EV layer    | Per-rider multi-stage projection     | 502   |
| 21            | planned                   | Optimizer quality                    | Shared sims, faster, smarter         | ~476  |
| 22            | planned                   | Variance-aware profiles + captain    | Real risk behavior                   | ~486  |
| 23            | planned                   | Intelligence + differential picks    | Biggest competitive edge             | ~494  |
| 24            | planned                   | Hardening + performance              | Production-ready final week          | ~502  |
| 25            | planned                   | Retrospective + TdF prep             | Season learning, next race           | ~510  |

---

## Critical path

**17 → 18 → 20** is the competitive critical path.

Session 19 (calibration) does NOT block Session 20 (lookahead).
Run 19 in parallel once 5 stages of data are available.

---

## Locked design decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| StageIntent source | Deterministic algorithm only | Reproducible, testable, no drift |
| Override format | Event signals, not numeric field edits | Calibration stays meaningful |
| Signal translation | `apply_intelligence_overrides()` owns all deltas | One place to audit |
| λ (transfer weight) | Fixed 0.85, exposed as `--lambda` CLI flag | Strategy knob, not model parameter |
| Session 18.5 | Absorbed into Session 18 as `--override` flag | No fragmentation |
| Lookahead timing | Session 20 (after ICDL, not before) | Intent first, optimization second |
| Captain logic | Simulation EV + intent.win_priority × p95 × 0.1 (BALANCED only) | Matches implementation |
| Sprinter penalty | Structural p_dnf_equivalent signal | Not a hard rule — brittle-proof |
| Intent → EV | `ev * (1 + 0.3 * win_priority)` | All 3 heuristics intent-weighted |

---

## What actually wins the Giro

**Non-negotiable:**
- ✅ Engine correctness (Session 17)
- ✅ Stage intent + transfer awareness (Session 18) — fixes decision blindness
- ✅ Lookahead (Session 20) — fixes myopia, now lands on correct foundation
- ✅ Differential picks (Session 23) — biggest structural edge in large field

**High edge:**
- 🔥 Ownership + EV mismatch (Session 23)
- 🔥 Scenario-conditioned EV / ev_by_scenario (Session 23)
- 🔥 Variance-aware profiles (Session 22)
- 🔥 Intelligence signal overrides (Session 18, `--override` flag)

**Nice-to-have:**
- Perfect calibration
- Fully automated intelligence
- Fancy UI

**Biggest risks:**
1. Railway drops state.json → pull Supabase backup to Session 21 if it happens once
2. Over-tuning calibration too early → do not touch multipliers before 5 stages
3. Holdet API changes between Giro and TdF → Session 25 endpoint revalidation

---

## Session Continuity Rules

1. Start each session by reading README.md, RULES.md, ARCHITECTURE.md
2. Run `python3.14 -m pytest` to confirm existing tests still pass
3. Build only what the session scope defines
4. End with all tests passing and state.json valid
5. Commit to GitHub at end of each session
6. If stuck after 3 attempts → stop, bring code + problem to Claude.ai

