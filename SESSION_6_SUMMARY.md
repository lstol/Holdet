# Session 6 Summary — CLI Orchestrator + State Management

**Date:** 2026-04-17
**Tests:** 265/265 passing
**Branch:** claude/jolly-kirch

---

## What was built

### `config.py`
Environment variable loading with fail-fast error messages.
- Required: `HOLDET_COOKIE`, `HOLDET_GAME_ID`, `HOLDET_FANTASY_TEAM_ID`, `HOLDET_CARTRIDGE`
- Optional with defaults: `STATE_PATH`, `RIDERS_PATH`, `STAGES_PATH`
- Constants: `TOTAL_STAGES = 21`, `INITIAL_BUDGET = 50_000_000`

### `ingestion/api.py` — additions
- `fetch_my_team(fantasy_team_id, cartridge, cookie)` — scrapes Next.js HTML team page
- `_parse_my_team_html(html)` — regex extracts `initialLineup`, `initialCaptain`, `initialBank`
  from `self.__next_f.push([1, "..."])` script blocks
- Fixed: raises `PermissionError` (not `ValueError`) when `initialLineup` is absent from HTML
  (expired cookie returns 200 but without team data)

### `main.py` — four commands
- `ingest --stage N` — fetch riders + team from API, update state.json, print DNS alerts
- `brief --stage N` — generate priors, interactive adjustment, simulate all riders, optimize
  all 4 profiles, print briefing table + suggested profile
- `settle --stage N` — 13 interactive prompts for stage result, score all 8 team riders using
  `score_rider()`, credit `etapebonus_bank_deposit` once (not 8×), validate vs Holdet site
- `status` — show team, bank, rank, value deltas vs start_value

**Engine caller rules followed (ARCHITECTURE.md §9):**
- `all_riders={holdet_id: Rider}` passed to every `score_rider()` call
- `etapebonus_bank_deposit` credited once per stage via `etapebonus_credited` flag
- Atomic state writes: write to `.tmp` then `os.replace()`

### `tests/test_cli.py` — 46 tests
- `TestConfig` (7): required/optional vars, missing raises, constants
- `TestParseMyTeamHtml` (11): lineup, captain, bank, PermissionError, captainPopularity, isOutOfGame
- `TestFetchMyTeam` (6): URL, cookie header, 401/403 PermissionError, ConnectionError
- `TestStateSaveLoad` (4): round-trip, defaults on missing file, no .tmp left
- `TestNameResolver` (8): exact ID, partial, case-insensitive, no match, ambiguous, list, kv
- `TestSettleEngineCallerRules` (4): team bonus 0/60k, etapebonus same for all, captain deposit only
- `TestDNSAlertIngest` (3): isOutOfGame, isEliminated, active not flagged
- `TestLoadStage` (3): flat stage, invalid raises, mountain stage

---

## Live smoke test results

Cookie from 2026-04-17 (AWSALB session):

```
python3.14 main.py ingest --stage 1
  → 91 riders fetched, saved to data/riders.json
  → Team loaded: 8 riders
      Lorenzo Germani (GFC) 2.50M
      Liam Slock (LOI) 2.50M
      Manuele Tarozzi (BAR) 2.50M [CAPTAIN]
      Jonas Vingegaard (TVL) 17.50M
      Filippo Conca (JAY) 2.50M
      Dion Smith (NSN) 2.50M
      Lennert Van Eetvelt (LOI) 7.50M
      Jay Vine (UAD) 8.00M
      Bank: 4.50M

python3.14 main.py status
  → Stage: 1/21 (0 settled), Bank: 4.500M
  → All 8 riders shown with captain marker [C]
```

---

## Known limitations

- `brief` requires `data/stages.json` to exist (not yet created — build in Session 7)
- `settle` requires `data/stages.json` to exist
- GC positions still entered manually — post-Stage-1 check of `/standings` endpoint may
  automate this (see Session 8 additions in SESSION_ROADMAP.md)

---

## AWSALB cookie note

The Holdet API uses AWS Application Load Balancer sticky sessions. The cookie is
**IP-locked** — it only works from the machine that captured it. Refreshing from a
different IP always gets a new `AWSALB` value; the old one returns 403.
