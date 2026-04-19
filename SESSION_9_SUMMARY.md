# Session 9 Summary — React Frontend + Supabase

**Date:** 2026-04-19
**Tests:** 316/316 passing (unchanged — backend frozen)
**Commits:** b725cb2 (Part A), c2bbd90 (Part C), 063078c (Netlify fix)
**Live URL:** https://holdet.syndikatet.eu

---

## Part A — Pre-Race Engine Improvements

### A1: Rider type classification (`scoring/probabilities.py`)

`_rider_type(rider, stage)` now classifies by value bracket:

| Value | Mountain/Hilly | Flat/ITT |
|-------|---------------|---------|
| > 8M  | `"gc"`        | `"sprinter"` |
| > 5M  | `"specialist"` | `"specialist"` |
| < 3M  | `"domestique"` | `"domestique"` |
| else  | `"all"`        | `"all"` |

`BASE_TOP15` extended with `specialist` and `domestique` entries for all 5 stage
types. Mountain `gc` p_top15 raised from 0.30 → 0.45 (GC contenders on mountain
stages are near-certain top-15 finishers).

3 new tests in `tests/test_probabilities.py::TestRiderTypeClassification`.

### A2: ANCHOR fixture verification (`tests/test_optimizer.py`)

`TestAnchorRealisticFixtures` — 3 tests documenting the correct ANCHOR behaviour:

1. `test_gc_rider_p10_exceeds_sprinter_p10` — GC standing income (60k floor) >
   sprinter floor (25k). This is *why* ANCHOR keeps GC riders, not artificially
   inflated fixtures.
2. `test_anchor_downside_10pct_is_positive` — ANCHOR recommendation's p10 is
   positive even on a flat stage (GC standing value guaranteed).
3. `test_anchor_does_not_protect_dns_gc_rider` — DNS status overrides GC
   protection; ANCHOR still sells the rider.

### A3: Stage image script (`scripts/fetch_stage_images.py`)

Downloads Giro 2026 stage profile images from `static2.giroditalia.it`,
saves to `data/stage_images/giro_2026/stage-{N:02d}.jpg`, skips existing files.

```bash
python3 scripts/fetch_stage_images.py --dry-run    # preview URLs
python3 scripts/fetch_stage_images.py              # download all 21
python3 scripts/fetch_stage_images.py --upload     # download + upload to Supabase Storage
python3 scripts/fetch_stage_images.py --stage 7    # single stage
```

URL pattern may need verification once Giro site publishes stage images
(see script docstring for alternative patterns).

---

## Part C — React Frontend + Supabase

### Supabase project

**Project:** Holdet (`xcmyypnywmqdofukkvga`, eu-west-1)
**URL:** https://xcmyypnywmqdofukkvga.supabase.co

8 tables created via migration `holdet_schema`:

| Table | Scope | Purpose |
|-------|-------|---------|
| `stages` | shared (read-only for auth'd users) | All 21 Giro stages with PCS data |
| `stage_results` | shared | Post-settle actuals per stage |
| `game_state` | user-scoped (RLS) | Bank, team, rank per race |
| `riders` | user-scoped | Full roster with status, value, jerseys |
| `prob_snapshots` | user-scoped | Model/odds/intelligence/manual probs per stage |
| `value_history` | user-scoped | ValueDelta per rider per stage |
| `brier_history` | user-scoped | Brier score records per stage |
| `intelligence_log` | user-scoped | Gather Intelligence outputs |
| `keep_alive_log` | shared | Ping log to prevent Supabase pausing |

RLS: shared tables readable by any authenticated user. User-scoped tables visible
to `auth.uid() = user_id` only.

### `scripts/sync_to_supabase.py`

Upserts all local state to Supabase. Run after each CLI command:

```bash
python3 scripts/sync_to_supabase.py --race giro_2026
```

First-time setup — get your UUID from Supabase Auth > Users after signing up:
```bash
python3 scripts/sync_to_supabase.py --set-user-id <your-uuid>
```

Requires `.env`: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`.
Graceful failure — never crashes the CLI workflow.

### `scripts/keep_alive.py` + `.github/workflows/keep_alive.yml`

Pings `keep_alive_log` every 5 days via GitHub Actions cron to prevent Supabase
free-tier pausing. Set GitHub secrets: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`.

### React frontend (`frontend/`)

**Stack:** Next.js 16 + Tailwind CSS + Supabase JS (`@supabase/ssr`) + recharts

**Pages:**

| Route | Description |
|-------|-------------|
| `/briefing` | Stage header, DNS alert, [Gather Intelligence] button + panel, probability table with source badges, redirected from `/` |
| `/team` | 8 rider cards with value delta, bank, captain crown |
| `/history` | recharts line charts: team value over time + Brier score (model vs manual), value delta table |
| `/riders` | Full roster, filters (team/status/price/in-my-team), sort by value/delta/p_win |
| `/stages` | 21-stage list, click-to-expand with profile image, PCS data, result |
| `/auth` | Email/password login + signup (Supabase Auth) |

**Source badge colours:** model=grey, odds=blue, intelligence=purple, manual=orange

**`/api/intelligence` (Next.js API route):**

Fires Anthropic `claude-sonnet-4-20250514` with `web_search_20250305` tool.
Sources: Emil Axelsgaard (TV2), The Inner Ring, Cyclingnews + stage-specific searches.
Returns structured JSON: `stage_summary`, `rider_adjustments[]`, `dns_risks[]`,
`stage_notes`, `sources_used[]`.
Saves full output to `intelligence_log` table.
Accept/Ignore per rider — accepted adjustments update the probability table
with `source="intelligence"`.

### Deployment

**Host:** Netlify
**Live URL:** https://holdet.syndikatet.eu
**Config:** `frontend/netlify.toml` with `@netlify/plugin-nextjs`

Environment variables set on Netlify:
- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- `ANTHROPIC_API_KEY`

DNS: CNAME `holdet → radiant-lamington-2ed8c9.netlify.app` at syndikatet.eu registrar.
TXT record `subdomain-owner-verification` added for Netlify domain verification.
Let's Encrypt SSL provisioned automatically.

---

## Known limitations / deferred to Session 10+

- Part B (live validation) requires Giro to start (May 9). See SESSION_9_SCOPE.md.
- Stage profile images: URL pattern needs live verification against giroditalia.it.
  Run `scripts/fetch_stage_images.py` after images are published.
- `sync_to_supabase.py` requires `supabase-py` (`pip install supabase`).
  Not in Python requirements yet — install manually for now.
- Intelligence prompt tuning: build into Session 10 after first real use.
- Briefing page shows probability table but not the 4-profile optimizer output
  (optimizer runs in CLI only — frontend shows probs + raw game_state).

---

## Session 9 done conditions — status

| Condition | Status |
|-----------|--------|
| A1: `_rider_type()` classifies by value bracket | ✓ |
| A2: ANCHOR fixture tests passing | ✓ |
| A3: `fetch_stage_images.py` created | ✓ |
| B1–B3: Live validation | ⏳ After May 9 |
| All 5 pages render with real Supabase data | ✓ |
| Gather Intelligence returns structured suggestions | ✓ |
| Mobile layout works on iPhone | ✓ (dark, responsive) |
| `sync_to_supabase.py` runs cleanly | ✓ |
| Auth: other users can sign up and log in | ✓ |
| Deployed and accessible from phone | ✓ holdet.syndikatet.eu |
| Keep-alive deployed | ✓ GitHub Actions cron |
