# Session 9 Scope — React Frontend + Gather Intelligence

**Status:** Planned (after Session 8)
**Dependencies:** Session 8 complete, Supabase connected, stage images downloaded

---

## Goal

A usable web UI for the full decision loop — briefing, intelligence gathering, team
management, and history tracking. Shareable with other participants.

---

## Stack

- React + Tailwind (single-file artifact pattern from existing codebase)
- Supabase (already connected via MCP)
- Anthropic API with web_search tool for Gather Intelligence feature
- Python backend unchanged — frontend reads Supabase, backend syncs state.json to Supabase

---

## Supabase Schema

Mirror state.json into these tables:

```sql
game_state        -- one row per race: stage, bank, rank, budget
riders            -- full roster: holdet_id, name, team, value, start_value,
                     status, in_my_team, is_captain
stages            -- all 21 stages with enriched PCS fields
stage_results     -- post-settle actuals per stage
prob_snapshots    -- model + form + manual probs per rider per stage
value_history     -- per-rider value deltas per stage
brier_history     -- ProbAccuracy records per stage
intelligence_log  -- per-stage intelligence brief: sources, suggestions,
                     accepted adjustments
```

A sync script scripts/sync_to_supabase.py reads state.json + data/riders.json +
data/stages.json and upserts to Supabase after each ingest, settle, and brief run.
Frontend is read-only from Supabase — all writes go through the Python CLI first.

---

## Pages

### 1. Briefing (primary daily use)

- Stage header: number, date, start→finish, distance, type, ProfileScore,
  gradient final km
- Stage profile image (downloaded in Session 8, served from Supabase Storage)
- [Gather Intelligence] button — see detailed spec below
- Intelligence panel (appears after button press):
  per-rider suggestions + reasoning + accept checkboxes
- Probability table: team riders + transfer candidates
  Columns: name | team | value | p_win | p_top3 | p_top15 | p_dnf | source
  Source shown as badge: model / form / intelligence / manual
- 4-profile recommendation table:
  profile | transfers | captain | EV | p90 | p10 | fee
- Suggested profile highlighted with reasoning
- DNS alerts banner (red) if any team rider flagged

### 2. My Team

- 8 rider cards: name, team, value, delta vs start_value, captain badge
- Bank balance + total team value
- Stage progress: N/21 completed
- Quick captain change indicator (actual change still done on Holdet site)

### 3. History

- Value over time chart per rider (recharts LineChart)
- Stage-by-stage value delta table
- Brier score chart: model vs manual per stage, season running average
- "You beat the model on M/N stages" summary

### 4. Riders

- Full roster table with filters: team, price range, status, in_my_team
- Sort by: value, value change, expected value (from last brief)
- For finding transfer candidates

### 5. Stages

- All 21 stages listed: type, date, ProfileScore, gradient, finish location
- Click any stage → detail view: profile image, notes, sprint/KOM points
- Completed stages show actual winner + value impact on team

---

## Gather Intelligence — Detailed Spec

Triggered by button on Briefing page. Fires an Anthropic API call with
web_search tool enabled. Returns structured JSON rendered as an adjustment panel.

### System prompt sent to API:
You are a cycling analyst assistant for a fantasy cycling game.
The next stage is: Stage {N} — {start} → {finish} ({type}, {distance}km).
Key profile data: ProfileScore={ps}, gradient final km={grad}%,
PS final 25k={ps25k}.
My current team: {rider_names_and_teams}.
Transfer candidates being considered: {candidates}.
Search and read the following sources for this stage:

https://sport.tv2.dk/profil/emil-axels — find the latest stage {N} analysis
https://inrng.com — find stage {N} Giro 2026 preview or race coverage
Search: "giro 2026 stage {N} {finish} preview favourites"
Search: "giro 2026 stage {N} team tactics startlist"

Based on what you find, return ONLY a JSON object with this exact structure:
{
"stage_summary": "2-3 sentence tactical overview in English",
"rider_adjustments": [
{
"name": "rider name",
"p_win_suggested": 0.00,
"p_top3_suggested": 0.00,
"p_top15_suggested": 0.00,
"p_dnf_suggested": 0.00,
"reasoning": "1-2 lines citing source",
"confidence": "high|medium|low"
}
],
"dns_risks": ["rider name if mentioned as doubtful"],
"stage_notes": "anything tactically important not captured per-rider",
"sources_used": ["url1", "url2"]
}
Only include riders in rider_adjustments if you found specific information
about them. Do not invent adjustments — if no information found for a rider,
omit them.

### UI rendering:

- Stage summary at top of intelligence panel
- DNS risks shown as yellow warning badges
- Per-rider suggestion rows:
    Vingegaard  p_win: 0.12→0.35 ↑  p_top15: 0.70→0.90 ↑  [Accept] [Ignore]
    reasoning: "Axelsgaard: storfavorit, TVL kører udelukkende for ham"
    confidence: HIGH
- [Accept All] / [Ignore All] buttons
- Accepted adjustments update probability table with source="intelligence"
- Full intelligence log saved to Supabase intelligence_log table

### Sources targeted:

| Source                        | Why                                              |
|-------------------------------|--------------------------------------------------|
| Emil Axelsgaard (TV2 Sport DK)| Best Danish tactical analysis, team insider info |
| The Inner Ring (inrng.com)    | Deep tactical and historical context, reliable   |
| Cyclingnews                   | Fast stage previews, startlist confirmation      |
| VeloNews                      | Good GC and climbing analysis                    |
| DirectVelo                    | French source, excellent on team tactics         |
| Team press conferences        | DNS confirmation, protected rider roles          |

---

## Design Principles

- Mobile-first — used from phone during evening trading window
- Dark mode preferred (post-stage use after 20:30)
- Briefing page renders in <2 seconds without intelligence fetch
- Intelligence fetch is async — spinner shown, page not blocked
- All Holdet actions (trades, captain changes) done on Holdet site — this UI
  is decision support only, never automates Holdet

---

## Stage Image Serving

Images downloaded in Session 8 to data/stage_images/giro_2026/stage-01.jpg etc.
For frontend: upload to Supabase Storage bucket stage-images/giro_2026/.
React component fetches from Supabase Storage public URL per stage number.
For TdF 2026: new bucket stage-images/tdf_2026/, same component unchanged.

---

## Sync Architecture

Python CLI is source of truth. Supabase is a read layer for the frontend.
scripts/sync_to_supabase.py runs after each: ingest, settle, brief.
No two-way sync — avoids conflicts between CLI state and UI state.

---

## Done When

- All 5 pages render with real data from Supabase
- Gather Intelligence returns structured suggestions in <30 seconds
- Mobile layout works on iPhone screen
- Briefing page usable end-to-end:
  intelligence → probability table → profile recommendation
- sync_to_supabase.py runs cleanly after settle and brief
- Deployed (Vercel or similar) so accessible from phone during race

---

## Do Not Build in Session 9

- Automated trading on Holdet (never)
- Multi-user auth (Session 10 if needed)
- Push notifications for DNS alerts (Session 10)

---

## Notes for Session 10+

- Intelligence prompt will need tuning after first real use — build tuning
  into Session 10 scope
- Multi-user: other participants can log in and use the same tool;
  game_id is the only parameter that changes between competitions
- TdF 2026: change race config, re-run fetch_stage_images.py,
  update Supabase game_state row — frontend unchanged
