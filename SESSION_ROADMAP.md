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

## Session 14 — Simulation Layer Rebuild ✓ COMPLETE (2026-04-22)

**Goal:** Replace rider-level Monte Carlo with stage-level simulation.
Current simulate_rider() produces independent draws which breaks etapebonus,
team bonus, and captain bonus — these only work correctly at team level.

**What to build:**

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

### Optimizer uses team-level simulation
- ANCHOR:     maximize team p10
- BALANCED:   maximize team EV
- AGGRESSIVE: maximize team p80
- ALL_IN:     maximize team p95

Captain selected per profile:
- ANCHOR:     rider with highest floor (p10)
- BALANCED:   rider with highest EV
- AGGRESSIVE/ALL_IN: rider with highest ceiling (p95)

**Sanity checks required:**
- Flat stage: sprinters dominate team p95, GC riders stabilize p10
- Mountain stage: GC riders dominate all percentiles
- ALL_IN produces meaningfully wider distribution than ANCHOR
- Etapebonus visible: team EV > sum of individual rider EVs

**Do not build in Session 14:**
- Multi-stage transfer planning (Session 15)
- Improved probability inputs (Session 15)

---

## Session Continuity Rules

1. Start each session by reading README.md, RULES.md, ARCHITECTURE.md
2. Run `python3 -m pytest` to confirm existing tests still pass
3. Build only what the session scope defines
4. End with all tests passing and state.json valid
5. Commit to GitHub at end of each session
6. If stuck after 3 attempts → stop, bring code + problem to Claude.ai
