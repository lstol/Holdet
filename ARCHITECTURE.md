# ARCHITECTURE.md — Holdet Decision Support Tool
# Design decisions, data schemas, module interfaces, risk profiles

---

## 1. Core Design Principles

1. **Claude recommends, human decides.** No autonomous actions. Every
   output is a briefing for a human decision.

2. **Scoring engine is the foundation.** Validate it against real Holdet
   results during the Giro before trusting any optimizer output. If the
   engine is wrong, everything downstream is wrong.

3. **State is explicit and persistent.** All game state in `state.json`.
   Load at session start, save at session end. No implicit state.

4. **Ingestion is pluggable.** `get_riders()` hides the data source.
   API is confirmed and primary. Fallbacks exist for edge cases.

5. **Probabilities are first-class data.** Stored, displayed, manually
   editable, and compared against actuals for calibration.

6. **Risk profiles are parallel.** Optimizer runs all four in one pass.
   User sees full trade-off space before deciding.

7. **Frontend is planned.** Architecture must support a React + Supabase
   frontend for TdF. Keep state management clean and API-friendly from
   the start.

8. **Bug-fix-first.** If the engine produces a wrong result against a real
   Holdet stage, stop all feature work and fix the engine before proceeding.
   An incorrect engine invalidates every downstream recommendation. Giro 2026
   is the validation run — treat each discrepancy in tests/validation_log.md
   as a blocker.

9. **Engine caller responsibilities.** `score_rider()` is a pure function but
   callers have three critical obligations:
   - Pass `all_riders={holdet_id: Rider}` for all active riders — team bonus
     is 0 without it (engine cannot compute teammate finish positions otherwise).
   - Credit `etapebonus_bank_deposit` exactly ONCE per stage (read it from any
     one rider's ValueDelta — the engine returns the same value for all). Summing
     it across all 8 riders inflates bank by 8×.
   - Build `StageResult` once before the per-rider loop, not once per rider.

---

## 2. Data Schemas

### Rider

```python
@dataclass
class Rider:
    holdet_id: str             # items[].id from API (primary key)
    person_id: str             # items[].personId (for API lookups)
    team_id: str               # items[].teamId (for API lookups)
    name: str                  # "{firstName} {lastName}"
    team: str                  # full team name, e.g. "Team Visma | Lease a Bike"
    team_abbr: str             # e.g. "TVL"
    value: int                 # items[].price — current value in kr
    start_value: int           # items[].startPrice — value at race start
    points: int                # items[].points — cumulative race points
    status: str                # "active" | "dns" (isOut=true) | "dnf" | "disqualified"
    gc_position: int | None    # current GC position (manual input — not in API)
    jerseys: list[str]         # jerseys held: "yellow","green","polkadot","white"
    in_my_team: bool
    is_captain: bool
```

### Stage

```python
@dataclass
class Stage:
    number: int                # 1–21
    race: str                  # "giro_2026" | "tdf_2026"
    stage_type: str            # "flat" | "hilly" | "mountain" | "itt" | "ttt"
    distance_km: float
    is_ttt: bool
    start_location: str
    finish_location: str
    sprint_points: list[SprintPoint]
    kom_points: list[KOMPoint]
    notes: str                 # e.g. "Summit finish", "Cobbles last 30km"

@dataclass
class SprintPoint:
    location: str
    km_from_start: float
    points_available: list[int]   # [20, 17, 15, 13, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
    is_finish: bool               # True if this is the stage finish sprint

@dataclass
class KOMPoint:
    location: str
    km_from_start: float
    category: str              # "HC" | "1" | "2" | "3" | "4"
    points_available: list[int]
```

### StageResult

```python
@dataclass
class StageResult:
    stage_number: int
    finish_order: list[str]              # holdet_ids in finish order (top 15+)
    times_behind_winner: dict[str, int]  # holdet_id → seconds behind winner
    sprint_point_winners: dict[str, list[int]]  # holdet_id → [pts at each sprint]
    kom_point_winners: dict[str, list[int]]     # holdet_id → [pts at each KOM]
    jersey_winners: dict[str, str]       # "yellow"|"green"|"polkadot"|"white" → holdet_id
    most_aggressive: str | None          # holdet_id
    dnf_riders: list[str]               # holdet_ids
    dns_riders: list[str]               # holdet_ids (deactivated)
    disqualified: list[str]             # holdet_ids
    ttt_team_order: list[str] | None    # team names in placement order (TTT only)
    gc_standings: list[str]             # holdet_ids in GC order after stage
```

### RiderProb

```python
@dataclass
class RiderProb:
    rider_id: str              # holdet_id
    stage_number: int
    p_win: float               # P(finish 1st)
    p_top3: float              # P(finish top 3)
    p_top10: float             # P(finish top 10)
    p_top15: float             # P(finish top 15)
    p_dnf: float               # P(abandons this stage)
    p_jersey_retain: dict[str, float]   # "yellow" → P(holds it at finish)
    expected_sprint_points: float       # ≥ 0 always
    expected_kom_points: float          # ≥ 0 always
    source: str                # "model" | "manual" | "adjusted"
    model_confidence: float    # 0.0–1.0
    manual_overrides: dict     # field → manual_value (audit trail)
```

### GameState

```python
@dataclass
class GameState:
    race: str                          # "giro_2026" | "tdf_2026"
    current_stage: int
    total_stages: int                  # 21
    my_team: list[str]                 # holdet_ids (exactly 8)
    captain: str                       # holdet_id
    bank: float                        # kr in bank
    initial_budget: float              # 50,000,000
    stages_completed: list[int]
    my_rank: int | None
    total_participants: int | None
    # History
    prob_history: dict                 # "stage_N" → {holdet_id → RiderProb}
    result_history: dict               # "stage_N" → StageResult
    value_history: dict                # "stage_N" → {holdet_id → ValueDelta}
```

### RiskProfile

```python
class RiskProfile(Enum):
    ANCHOR     = "anchor"      # Maximise floor (p10) — protect GC riders
    BALANCED   = "balanced"    # Maximise expected value (EV)
    AGGRESSIVE = "aggressive"  # Maximise 80th percentile
    ALL_IN     = "all_in"      # Maximise 95th percentile — conviction bet
```

### ValueDelta (output of scoring engine)

```python
@dataclass
class ValueDelta:
    rider_id: str
    # Rider value components (change rider's market value)
    stage_position_value: int      # from finish position table
    gc_standing_value: int         # from GC position table
    jersey_bonus: int              # from jersey held at finish
    sprint_kom_value: int          # points × 3,000
    late_arrival_penalty: int      # truncated minutes × −3,000, cap −90,000
    dnf_penalty: int               # −50,000 if DNF/DQ, else 0
    dns_penalty: int               # −100,000 × remaining_stages if DNS
    team_bonus: int                # from teammate's top-3 finish
    ttt_value: int                 # from TTT team placement
    total_rider_value_delta: int   # sum of above
    # Bank components (go to bank, not rider value)
    captain_bank_deposit: int      # mirrors positive rider growth if captain
    etapebonus_bank_deposit: int   # from stage depth bonus table
    total_bank_delta: int          # sum of bank components
```

### BriefingOutput

```python
@dataclass
class BriefingOutput:
    stage: Stage
    current_team_ev: float
    suggested_profile: RiskProfile
    suggested_profile_reason: str
    profiles: dict[RiskProfile, ProfileRecommendation]

@dataclass
class ProfileRecommendation:
    profile: RiskProfile
    transfers: list[TransferAction]
    captain: str                   # holdet_id
    expected_value: float
    upside_90pct: float
    downside_10pct: float
    transfer_cost: int             # total fees
    reasoning: str

@dataclass
class TransferAction:
    action: str                    # "sell" | "buy"
    rider_id: str
    rider_name: str
    value: int
    fee: int                       # 0 for sells, 1% of value for buys
    reasoning: str
```

---

## 3. Module Interfaces

### scoring/engine.py — BUILD FIRST

```python
def score_rider(
    rider: Rider,
    stage: Stage,
    result: StageResult,
    my_team: list[str],        # all 8 holdet_ids
    captain: str,              # holdet_id of captain
    stages_remaining: int      # for DNS cascade calculation
) -> ValueDelta:
    """
    Pure function. No side effects. No I/O.
    Returns complete value breakdown for one rider in one stage.
    All scoring logic references rule numbers from RULES.md in comments.
    """
```

### scoring/probabilities.py

```python
def generate_priors(
    riders: list[Rider],
    stage: Stage
) -> dict[str, RiderProb]:
    """
    Generate model probability estimates from stage type + rider data.
    Returns dict of holdet_id → RiderProb with source="model".
    """

def interactive_adjust(
    probs: dict[str, RiderProb],
    stage: Stage
) -> dict[str, RiderProb]:
    """
    CLI interface: display prob table, accept manual adjustments,
    return updated probs with source="adjusted" and audit trail.
    Flags adjusted values with * in display.
    """
```

### scoring/simulator.py

```python
def simulate_rider(
    rider: Rider,
    stage: Stage,
    probs: RiderProb,
    my_team: list[str],
    captain: str,
    n_simulations: int = 10000
) -> SimResult:
    """Monte Carlo simulation. Uses engine internally."""

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

### scoring/optimizer.py

```python
def optimize(
    riders: list[Rider],
    my_team: list[str],
    stage: Stage,
    probs: dict[str, RiderProb],
    sim_results: dict[str, SimResult],   # pre-computed from simulator
    bank: float,
    risk_profile: RiskProfile,
    rank: int | None,
    total_participants: int | None,
    stages_remaining: int
) -> ProfileRecommendation:

def optimize_all_profiles(
    riders: list[Rider],
    my_team: list[str],
    stage: Stage,
    probs: dict[str, RiderProb],
    sim_results: dict[str, SimResult],
    bank: float,
    rank: int | None,
    total_participants: int | None,
    stages_remaining: int
) -> dict[RiskProfile, ProfileRecommendation]:
    """Run all 4 profiles in one pass. Returns all 4 recommendations."""

def suggest_profile(
    rank: int,
    total: int,
    stages_remaining: int,
    target_rank: int = 100
) -> tuple[RiskProfile, str]:
    """Returns (profile, plain_english_reason)."""
```

### ingestion/api.py — PRIMARY DATA SOURCE

```python
def get_riders(game_id: str, cookie: str) -> list[Rider]:
    """
    Fetches from:
    GET https://nexus-app-fantasy-fargate.holdet.dk/api/games/{game_id}/players
    Headers: {"Cookie": cookie}

    Parses items[] joined with _embedded.persons and _embedded.teams.
    Maps isOut=True → status="dns".
    GC position and jerseys not available in this endpoint — set to None/[].
    """

def find_standings_endpoint(game_id: str, cookie: str) -> dict:
    """
    Probe candidate endpoints for GC standings and jersey data:
    - /api/games/{id}/rounds
    - /api/games/{id}/standings
    - /api/games/{id}/statistics
    Returns raw response for inspection.
    """
```

---

## 4. Risk Profile Definitions

**CRITICAL DESIGN PRINCIPLE:** Profiles are defined by SQUAD COMPOSITION
OBJECTIVE, not by transfer count. Transfer count is an OUTPUT of the
optimizer, never an input constraint. On a sprint stage after a mountain
stage, ALL_IN may require 6–7 transfers. On two consecutive sprint stages,
ANCHOR may also require 0 transfers. The profile tells the optimizer WHAT
to optimise for — transfers follow naturally.

### ANCHOR
- **Objective:** Maximise floor value. Prefer certain, repeatable sources.
- **Squad target:** Retain GC riders (guaranteed 60–100k/stage from standing),
  retain jersey holders, fill remaining slots with highest-EV available for
  this stage type.
- **Captain:** Highest EV rider on the team.
- **Transfer logic:** Only transfer if the replacement rider has strictly
  higher EV than the sold rider, net of the 1% fee amortised across
  stages_remaining. Never sacrifice a GC top-10 rider.
- **Optimisation metric:** `percentile_10` (maximise the bad-day outcome)

### BALANCED
- **Objective:** Maximise total expected value across all scoring sources.
- **Squad target:** Best risk-adjusted mix of GC riders + stage-type
  specialists. Targets ~3–5 riders in stage top 15 for moderate Etapebonus.
- **Captain:** Rider with best EV/std_dev ratio for this stage.
- **Transfer logic:** Transfer if `(new_rider_EV − sold_rider_EV) > fee / stages_remaining`
- **Optimisation metric:** `expected_value` (EV)

### AGGRESSIVE
- **Objective:** Maximise ceiling. Overload stage-type specialists to chase
  Etapebonus and stage bonuses, even at the cost of GC certainty.
- **Squad target:**
  - Flat stage: maximise number of sprinters likely to finish top 15
    (chase nonlinear Etapebonus: 6 riders = 120k, 8 = 400k)
  - Mountain stage: load elite climbers who fight for stage + GC time
  - ITT: TT specialists + protect against late-arrival penalty exposure
  - TTT: concentrate on riders from the strongest TTT teams (2× = 400k)
- **Captain:** Highest p90 rider for this specific stage type.
- **Transfer logic:** Accept up to −30k EV reduction per transfer if p90
  improves by +80k or more.
- **Optimisation metric:** `percentile_80`

### ALL_IN
- **Objective:** Maximum upside. Deliberate high-risk, high-reward squad.
  This is a conviction bet, not a random guess.
- **Squad target:** Concentrate on the single most likely scenario for this
  stage — e.g. all sprinters from 2–3 teams most likely to win; or all
  climbers from one dominant team on a summit finish. Explicitly target the
  400k Etapebonus cliff (8 riders top 15).
- **Captain:** Highest p95 rider regardless of EV.
- **Transfer logic:** Optimise purely for p95 outcome. Fee payback is a
  secondary concern.
- **Optimisation metric:** `percentile_95`

### Auto-suggestion logic

```python
top_pct = rank / total
if top_pct < 0.001:                              → ANCHOR     "Top 0.1% — protect elite position"
if top_pct < 0.01:                               → BALANCED   "Top 1% — controlled hunting"
if stages_remaining < 5:                         → ALL_IN     "Running out of time"
if gap > stages_remaining * 80_000:              → AGGRESSIVE "Gap too large for safe play"
else:                                            → BALANCED   "Standard situation"
```

---

## 5. Probability Adjustment Interface (CLI)

```
──────────────────────────────────────────────────────────
  STAGE 12 — PROBABILITY REVIEW
──────────────────────────────────────────────────────────
  Rider                Team    Win%  Top3  Top15  DNF   Conf  Source
  ──────────────────────────────────────────────────────────
  Vingegaard J.        TVL     38%   61%   84%    2%    0.8   model
  Milan J.             LIT     31%   55%   78%    1%    0.9   model
  Almeida J.           UAE     12%   29%   65%    3%    0.7   model
  Groenewegen D.        URR     8%   22%   60%    1%    0.6   model  ← low conf

  Adjust (field rider value) or Enter to accept all:
  > vingegaard win 50
  > groenewegen dnf 8
  > done

  Updated:
  Vingegaard J.        TVL     50%*  ...                      adjusted
  Groenewegen D.        URR     8%   22%   60%    8%*         adjusted

  * = manual override
──────────────────────────────────────────────────────────
```

---

## 6. Probability Calibration (Brier Score)

Track after each stage:

```python
@dataclass
class ProbAccuracy:
    stage: int
    rider_id: str
    event: str             # "win" | "top3" | "top15" | "dnf"
    model_prob: float
    manual_prob: float | None
    actual: float          # 1.0 or 0.0
    model_brier: float     # (model_prob - actual)²
    manual_brier: float | None
```

Season summary: "You beat the model on 8/14 stages" tells you whether
manual adjustments are adding value going into TdF.

---

## 7. Frontend Architecture (Session 9 — TdF)

**Stack:** React + Supabase + existing Python backend

**Supabase tables mirror state.json:**
- `game_state` — one row per race (giro/tdf)
- `riders` — full roster with current values
- `stages` — stage metadata
- `stage_results` — post-stage actuals
- `prob_snapshots` — model + manual probs per stage
- `value_history` — per-rider value deltas per stage

**Frontend pages:**
1. **Briefing** — pre-stage 4-profile recommendation table
2. **My Team** — current squad, values, captain indicator
3. **History** — value over time chart, Brier score tracking
4. **Riders** — full rider market with filters (team, price range, type)

**Shareable:** Other participants can log in and use the same tool.
Game ID is the only parameter that changes between competitions.

---

## 8. Validation Plan (Giro 2026)

Goal: confirm engine matches Holdet site for 5 consecutive stages.

Per stage:
1. `python3 main.py settle --stage N` with actual results
2. Compare `ValueDelta` outputs vs real Holdet value changes
3. Log discrepancies in `tests/validation_log.md`
4. Fix engine before proceeding to next stage

Format for validation_log.md:
```
## Stage N — [date]
| Rider | Engine delta | Holdet delta | Match? | Notes |
|-------|-------------|-------------|--------|-------|
| Vingegaard | +285,000 | +285,000 | ✓ | |
| Milan | +178,000 | +178,000 | ✓ | |
| Almeida | −12,000 | −9,000 | ✗ | Late arrival rounding? |
```
