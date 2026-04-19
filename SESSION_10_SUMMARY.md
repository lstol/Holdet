# Session 10 Summary — FastAPI Bridge + Frontend Write Capability

**Date:** 2026-04-19
**Tests:** 341/341 passing (316 backend + 25 new API tests)
**Commit:** 7a55fad
**Branch:** main

---

## What was built

### `api/server.py` — Local FastAPI bridge

Runs on `http://localhost:8000`. The frontend calls it to trigger Python CLI actions from buttons in the browser. Laptop-only by design — no cloud hosting for Giro.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/status` | GET | Current team, bank, rank, DNS alerts |
| `/ingest` | POST | Fetch riders + team from Holdet API, update state.json + riders.json |
| `/brief` | POST | Run full briefing pipeline → 4-profile optimizer table |
| `/settle` | POST | Score team riders for a completed stage, update bank + state |
| `/team` | POST | Update my_team + captain in state.json |
| `/sync` | POST | Push state to Supabase |

All endpoints auto-sync to Supabase (best-effort, never crashes if unavailable).

**`POST /brief` inputs:**
```json
{ "stage": 5, "look_ahead": 5, "captain_override": null }
```
`look_ahead` sets `stages_remaining` for the optimizer (default 5). `captain_override` is a holdet_id.

**`POST /brief` response includes:**
- `profiles`: all 4 profiles (anchor/balanced/aggressive/all_in) with EV, p10, p90, transfer cost, captain, transfers list, reasoning
- `team_sims`: per-rider EV/floor/ceiling for current squad
- `suggested_profile` + `suggested_profile_reason`
- `dns_alerts`

**`POST /settle` inputs:**
```json
{
  "stage": 5,
  "finish_order": ["holdet_id_1", ...],
  "dnf_riders": [],
  "dns_riders": [],
  "gc_standings": ["holdet_id_1", ...],
  "jersey_winners": { "yellow": "holdet_id" },
  "most_aggressive": null,
  "sprint_point_winners": { "holdet_id": 20 },
  "kom_point_winners": {},
  "times_behind_winner": {},
  "holdet_bank": 51234567
}
```
All rider references are holdet_ids (no name resolution). Returns per-rider scoring breakdown + bank delta.

### `scripts/start_api.sh`

```bash
bash scripts/start_api.sh
```
Starts uvicorn on `127.0.0.1:8000` with hot reload.

### `scripts/sync_to_supabase.py` — `sync_all()` added

New `sync_all(race)` function for programmatic calls from the API server. The `main()` CLI entry point is unchanged.

### `tests/test_api.py` — 25 new tests

Uses FastAPI `TestClient` (no real HTTP). Each test class redirects file paths to `tmp_path` via `monkeypatch`. Tests cover:
- `TestStatusEndpoint` — 6 tests
- `TestBriefEndpoint` — 8 tests (including look-ahead, captain override, 4-profile structure)
- `TestTeamEndpoint` — 4 tests (size validation, captain-in-team validation)
- `TestSettleEndpoint` — 6 tests (scoring, bank update, stage marked completed)
- `TestSyncEndpoint` — 1 test (200 or 500 both acceptable)

---

## Frontend changes

`NEXT_PUBLIC_API_URL=http://localhost:8000` added to `frontend/.env.local` (gitignored). All buttons show a graceful error if the server isn't running.

### Briefing page (`/briefing`)

**New actions panel:**
- **[Refresh Riders]** → `POST /ingest` — updates riders.json from Holdet API
- **Look-ahead stages** input (default 5) — controls `stages_remaining` for optimizer
- **Captain override** dropdown (team riders) — overrides optimizer captain pick
- **[Run Briefing]** → `POST /brief` — runs full pipeline, shows results inline

**Briefing result panel (shown after Run Briefing):**
- Suggested profile badge with reason
- Collapsible 4-profile comparison table (EV / p90 / p10 / fee / captain)
- Per-profile transfer details (buy/sell list + reasoning)
- Team simulation table (EV, floor p10, ceiling p90 per rider)

### Team page (`/team`)

**[Update My Team]** — opens rider editor:
- Search/filter box for name or team
- Checkbox list of all riders (caps at 8 selected)
- Captain dropdown (from selected riders)
- Submit → `POST /team` → saves to state.json

**[Sync to Supabase]** → `POST /sync`

### Stages page (`/stages`)

**[Settle Stage N]** — shown for unsettled stages, opens inline form:
- Top-15 finish order (15 rider autocomplete slots)
- GC standings top 10 (10 autocomplete slots)
- Jersey winners (yellow/green/polkadot/white dropdowns)
- Most aggressive (optional)
- Holdet bank validation input
- Submit → `POST /settle`

Rider autocomplete: type ≥2 chars → dropdown of matching riders from Supabase.

### Riders page (`/riders`)

**[Ingest]** button → `POST /ingest` — refresh rider values from Holdet API.

---

## Known limitations / deferred

- `NEXT_PUBLIC_API_URL` must be set in `frontend/.env.local` for local dev. Netlify deployment cannot reach localhost:8000 — buttons show "Server not running?" on the live site. This is intentional for Giro.
- Sprint/KOM point entry not wired in the settle form (passed as empty to API). Enter via CLI for now.
- Times behind winner (late arrival detection) not wired in settle form. Enter via CLI for accurate late arrival penalties.
- Part B (live validation against Holdet) deferred to after Giro start May 9.

---

## Session 10 done conditions — status

| Condition | Status |
|-----------|--------|
| FastAPI starts with `bash scripts/start_api.sh` | ✓ |
| `GET /status` responds correctly | ✓ |
| `POST /ingest` fetches riders | ✓ |
| `POST /brief` returns 4-profile table | ✓ |
| `POST /settle` scores team and updates bank | ✓ |
| `POST /team` saves to state.json | ✓ |
| `POST /sync` pushes to Supabase | ✓ |
| Briefing: [Run Briefing] shows 4-profile table | ✓ |
| Team: [Update My Team] saves correctly | ✓ |
| Stages: [Settle Stage N] works end-to-end | ✓ |
| Look-ahead and captain inputs wired | ✓ |
| 341 tests passing | ✓ |
| Frontend builds cleanly (TypeScript) | ✓ |
| Part B — live validation | ⏳ After May 9 |
