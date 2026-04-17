# Validation Log — Scoring Engine vs Holdet Site
# Compare engine ValueDelta output against actual Holdet value changes after each stage.
# Goal: 5 consecutive matching stages before trusting optimizer recommendations.

## How to use

After each Giro stage:
1. Run `python3 main.py settle --stage N` with actual results
2. Note the engine's ValueDelta for each of your team riders
3. Check the actual value change on the Holdet site
4. Record here — match or discrepancy

## Status: NOT STARTED (Giro begins ~May 2026)

---

## Stage 1 — [Date TBC]
| Rider | Engine delta | Holdet delta | Match? | Notes |
|-------|-------------|-------------|--------|-------|
| | | | | |

## Stage 2 — [Date TBC]
| Rider | Engine delta | Holdet delta | Match? | Notes |
|-------|-------------|-------------|--------|-------|
| | | | | |

## Stage 3 — [Date TBC]
| Rider | Engine delta | Holdet delta | Match? | Notes |
|-------|-------------|-------------|--------|-------|
| | | | | |

## Stage 4 — [Date TBC]
| Rider | Engine delta | Holdet delta | Match? | Notes |
|-------|-------------|-------------|--------|-------|
| | | | | |

## Stage 5 — [Date TBC]
| Rider | Engine delta | Holdet delta | Match? | Notes |
|-------|-------------|-------------|--------|-------|
| | | | | |

---

## Known Edge Cases to Watch For

- **Late arrival truncation:** Engine must truncate (not round) minutes.
  4m54s = 4 min = −12,000. If site shows −12,000 and engine shows −15,000,
  this is a rounding bug.

- **Jersey bonus timing:** Rider wears jersey all stage but loses it = 0 bonus.
  If site shows 0 and engine shows 25,000, jersey rule is implemented wrong.

- **DNF team bonus:** DNF rider should NOT get team bonus even if teammate wins.
  If site shows no team bonus for DNF rider and engine gives it, fix the guard.

- **TTT etapebonus:** Should be 0 on TTT stages.
  If engine calculates an etapebonus on TTT, the TTT branch is incomplete.

- **Captain negative day:** Bank should not decrease from captain loss.
  If site shows bank unchanged and engine shows negative captain_bank_deposit, fix it.
