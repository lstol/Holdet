# SESSION_5_SUMMARY.md — API Ingestion

**Date:** 2026-04-17
**Tests:** 219/219 passing (179 from previous sessions + 40 new)

---

## What was built

### 1. Fixed Session 4 test fixture (`tests/test_optimizer.py`)

The old fixture had mountain riders with `p10=+20k` and sprinters with `p10=-20k`
on a flat stage — unrealistic values that made ANCHOR keep GC riders for the wrong
reason (inflated p10 metric, not guaranteed GC income).

**New fixture — realistic flat-stage values:**

| Rider type | EV | p10 | p95 | Reasoning |
|---|---|---|---|---|
| GC top-10 rider | +70k | +60k | +100k | Finishes in peloton every flat stage, always earns GC standing value (60–100k). Floor is high and reliable. |
| Sprinter | +80k | +25k | +300k | Higher EV and ceiling (winning = +300k) but variable floor (crash / missed sprint). |

ANCHOR now retains GC riders for TWO consistent reasons:
1. **Hard protection rule:** GC top-10 riders are never listed as `sell` candidates in ANCHOR
2. **Metric confirmation:** GC rider p10 (+60k) > sprinter p10 (+25k), so even without the
   hard rule, ANCHOR's p10-maximisation would keep them

### 2. `ingestion/base.py` — abstract interface

`IngestionSource` ABC with `fetch_riders(game_id)` method.
All concrete sources (API, manual, image) implement this interface so the
pipeline is source-agnostic.

### 3. `ingestion/api.py` — primary data source

**Key functions:**

| Function | Description |
|---|---|
| `fetch_riders(game_id, cookie)` | GET /api/games/{id}/players, parses items[] + _embedded |
| `_parse_players_response(data)` | Pure parser — extracted for testability |
| `probe_extra_endpoints(game_id, cookie)` | Probes /rounds, /standings, /statistics for GC/jersey data |
| `save_riders(riders, path)` | Serialises list[Rider] to JSON keyed by holdet_id |
| `load_riders(path)` | Deserialises from JSON → list[Rider] |

**Field mapping implemented:**

| API field | Rider field |
|---|---|
| `items[].id` | `holdet_id` |
| `items[].personId` | `person_id` (key into `_embedded.persons`) |
| `items[].teamId` | `team_id` (key into `_embedded.teams`) |
| `items[].price` | `value` |
| `items[].startPrice` | `start_value` |
| `items[].points` | `points` (None → 0) |
| `items[].isOut=True` | `status="dns"` |
| `_embedded.persons[id].firstName + lastName` | `name` |
| `_embedded.teams[id].name` | `team` |
| `_embedded.teams[id].abbreviation` | `team_abbr` |

Fields unavailable in `/players` endpoint: `gc_position=None`, `jerseys=[]`,
`in_my_team=False`, `is_captain=False` — set externally from state.json.

**Error handling:**
- HTTP 401/403 → `PermissionError` with DevTools refresh instructions
- Network failure → `ConnectionError` with descriptive message
- Missing `_embedded` keys → `logger.warning`, field set to "Unknown"/"???"
- Int-keyed `_embedded` dicts → normalised to str automatically

### 4. `tests/fixtures/players_response.json`

Recorded sample of the real API response format using known Giro 2026 riders
(Vingegaard, Milan, Almeida, Groenewegen, Ciccone, Gee, Germani, Slock).
Includes one rider with `isOut=true` (Slock) to test DNS detection.
Used as the mock payload for all HTTP-mocked tests.

### 5. `tests/test_ingestion.py` — 40 tests

| Test class | Tests | Coverage |
|---|---|---|
| `TestParsePlayersResponse` | 21 | Parser: name/team/value mapping, null points, DNS detection, missing embedded keys, int key normalisation |
| `TestFetchRiders` | 7 | HTTP layer: URL, cookie header, 401/403 PermissionError, network error, count |
| `TestSaveLoadRoundTrip` | 7 | Round-trip: count, IDs, names, values, status; JSON key format |
| `TestProbeExtraEndpoints` | 5 | Returns dict with correct keys; status recorded; network failure handled |

### 6. `.env.example` updated

Added `HOLDET_GAME_ID=612` (simplified form for Session 6 CLI) alongside
existing `HOLDET_GAME_ID_GIRO` and `HOLDET_GAME_ID_TDF`.

### 7. `SESSION_ROADMAP.md` updated

Appended Session 8 note on realistic flat-stage fixture verification.

---

## Live API run — confirmed results (2026-04-17, pre-race)

`fetch_riders("612", cookie)` returned **91 riders**, 23 teams, all `points=0`,
0 DNS riders (race not yet started). Top 5 by value:

| Rider | Team | Value |
|-------|------|-------|
| Jonas Vingegaard | TVL | 17,500,000 |
| Jonathan Milan | LIT | 11,500,000 |
| Joao Almeida | UAD | 10,000,000 |
| Dylan Groenewegen | URR | 10,000,000 |
| Giulio Pellizzari | RBH | 9,500,000 |

Real IDs confirmed: Vingegaard `holdet_id=47372`, `personId=4196`, `teamId=205`.

**Previously unknown fields** found in live response:
- `positionId=264` — same for all 91 riders (single position type "cyclist")
- `popularity=null` — expected to populate once race starts

## probe_extra_endpoints — confirmed findings (2026-04-17)

| Endpoint | HTTP | Result |
|----------|------|--------|
| `/api/games/612/rounds` | 200 | **HTML** — Next.js frontend route, not a JSON API |
| `/api/games/612/standings` | 200 | `[]` — empty array pre-race |
| `/api/games/612/statistics` | 200 | **HTML** — Next.js frontend route, not a JSON API |

**Conclusions:**
- `/rounds` and `/statistics` are not usable as data endpoints — they return the
  full server-rendered HTML page, not JSON.
- `/standings` returns `[]` before the race. **Check again after Stage 1** — likely
  to contain GC standings once racing starts.
- GC standings and jersey data remain unavailable via any confirmed JSON endpoint.
  Continue using manual input for these fields until `/standings` is confirmed
  post-Stage 1.

Full findings documented in API_NOTES.md under "Session 5 Probe Results".

---

## Next session

**Session 6 — CLI Orchestrator + State Management**

Build `main.py` and `config.py`:
```bash
python3 main.py ingest --stage N       # calls ingestion/api.py fetch_riders()
python3 main.py brief  --stage N       # runs optimizer, generates briefing
python3 main.py settle --stage N       # records results, updates state.json
python3 main.py status                 # shows team, bank, rank
```
