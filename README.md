# Holdet — Fantasy Cycling Decision Support Tool
# Giro d'Italia 2026 (test run) → Tour de France 2026 (main competition)

## Purpose

Decision-support system for the Holdet fantasy cycling competition (swush.com / holdet.dk).
Covers scoring logic, probability estimation, optimization across risk profiles,
data ingestion via confirmed API, and performance tracking.

**Core philosophy: Claude recommends, you decide.**
Every output is a briefing. No autonomous actions.

**Debugging workflow:** When something behaves unexpectedly in Claude Code,
stop and bring the code + wrong output to Claude.ai (the Holdet project).
Full rules context and design intent live there. Do not patch in circles.

---

## Competition Facts

| Parameter | Value |
|-----------|-------|
| Platform | swush.com / holdet.dk |
| Competition name | "Holdet" (Danish: "The Team") |
| Language of UI | Danish |
| Language of codebase | English |
| Game ID — Giro 2026 | 612 |
| Game ID — TdF 2026 | TBC (same URL pattern, different number) |
| Starting budget | 50,000,000 kr |
| Team size | Exactly 8 riders |
| Max riders per real-world team | 2 |
| Total stages per race | 21 |
| Estimated participants | 50,000–100,000 |
| Account type | Gold = unlimited transfers |
| Final score | Sum of all rider values + bank balance |

---

## API — CONFIRMED WORKING

All rider data is available in a single authenticated GET request.

```
GET https://nexus-app-fantasy-fargate.holdet.dk/api/games/{GAME_ID}/players
Authentication: Cookie header (session cookie from logged-in browser)
```

**One call returns three things:**
1. `items[]` — all riders with prices and status
2. `_embedded.persons{}` — rider names keyed by personId
3. `_embedded.teams{}` — real-world team names keyed by teamId

**Field mapping:**

| API field | Maps to | Notes |
|-----------|---------|-------|
| `items[].id` | `rider.holdet_id` | Use as primary key |
| `items[].personId` | key into `_embedded.persons` | |
| `items[].teamId` | key into `_embedded.teams` | |
| `items[].price` | `rider.value` | Current value in kr |
| `items[].startPrice` | `rider.start_value` | Value at race start |
| `items[].points` | `rider.points` | Cumulative race points |
| `items[].isOut` | `rider.status == "dns"` | True = deactivated, sell immediately |
| `persons[id].firstName + lastName` | `rider.name` | |
| `teams[id].name` | `rider.team` | Full name, e.g. "Team Visma \| Lease a Bike" |
| `teams[id].abbreviation` | `rider.team_abbr` | e.g. "TVL" |

**Auth setup:** Store cookie in `.env` as `HOLDET_COOKIE`. Never commit to git.
Cookie expires periodically — refresh from Chrome DevTools → Network → players request → Headers.

**Known gap:** GC standings, jersey holders, and sprint/KOM breakdowns are NOT
in this endpoint. Check for `/api/games/612/rounds` or `/api/games/612/standings`.
Until confirmed, these fields are entered manually during `ingest` step.

**Known Giro 2026 riders from API (sample):**

| Name | Team | Start Value |
|------|------|-------------|
| Jonas Vingegaard | Team Visma \| Lease a Bike | 17,500,000 |
| Jonathan Milan | Lidl - Trek | 11,500,000 |
| Joao Almeida | UAE Team Emirates - XRG | 10,000,000 |
| Dylan Groenewegen | Unibet Rose Rockets | 10,000,000 |
| Giulio Ciccone | Lidl - Trek | 8,000,000 |
| Derek Gee | Israel - Premier Tech | 8,000,000 |

---

## Project Structure

```
holdet/
├── README.md                   ← this file
├── RULES.md                    ← complete scoring rules (machine-readable)
├── ARCHITECTURE.md             ← schemas, interfaces, risk profiles
├── SESSION_ROADMAP.md          ← 9 sessions with scope + done conditions
├── API_NOTES.md                ← API endpoints, auth, response format
│
├── main.py                     ← CLI orchestrator
├── config.py                   ← constants, game IDs, budget
├── .env                        ← HOLDET_COOKIE (never commit)
├── .gitignore
│
├── data/
│   ├── state.json              ← live game state (team, bank, history)
│   ├── riders.json             ← cached rider table (refreshed each stage)
│   ├── stages.json             ← stage metadata for full race
│   └── results/
│       └── stage_XX.json
│
├── scoring/
│   ├── engine.py               ← pure scoring function — build first
│   ├── probabilities.py        ← model priors + manual adjustment
│   ├── simulator.py            ← Monte Carlo projections
│   └── optimizer.py            ← recommendations per risk profile
│
├── ingestion/
│   ├── base.py                 ← abstract interface
│   ├── api.py                  ← Holdet API (primary, confirmed working)
│   ├── manual.py               ← paste fallback
│   └── image.py                ← screenshot OCR fallback
│
├── output/
│   ├── report.py               ← pre-stage briefing
│   └── tracker.py              ← accuracy + Brier score
│
└── tests/
    ├── test_engine.py
    ├── test_optimizer.py
    └── validation_log.md       ← engine vs actual, stage by stage
```

---

## Daily Workflow

```bash
python3 main.py ingest --stage 12      # fetch rider data from API
python3 main.py brief  --stage 12      # generate 4-profile briefing
python3 main.py settle --stage 12      # record results, validate engine
python3 main.py status                 # team, bank, rank
```

---

## Tech Stack

| Tool | Purpose |
|------|---------|
| Python 3.14 (macOS: `python3`) | Primary language |
| pytest | Tests |
| rich | Terminal formatting |
| numpy | Monte Carlo math |
| requests | API calls |
| python-dotenv | Load .env |
| Supabase | Future: DB + frontend backend |
| React | Future: shareable web frontend (TdF) |

```bash
pip3 install pytest rich numpy requests python-dotenv
```

---

## Build Phases

| Session | Goal |
|---------|------|
| 1 | Scoring engine + full test suite |
| 2 | Probability layer + manual adjustment CLI |
| 3 | Monte Carlo simulator |
| 4 | Optimizer + 4 risk profiles |
| 5 | API ingestion (confirmed endpoint) |
| 6 | CLI orchestrator + state management |
| 7 | Reporting + Brier score tracking |
| 8 | Giro validation + tuning |
| 9 | TdF frontend (React + Supabase, shareable) |

---

## Security

- `.env` = session cookie = treat as password
- `.gitignore` must include: `.env`, `data/state.json`, `data/riders.json`
- Never log or print the full cookie string
- Refresh cookie from DevTools when requests start returning 401/403
