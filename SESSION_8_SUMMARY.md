# Session 8 Summary — Odds-Based Probability Inputs

**Date:** 2026-04-19
**Tests:** 310/310 passing (294 inherited + 16 new)
**Branch:** claude/busy-chebyshev → PR to main

---

## What was built

### `scoring/odds.py`

New module that converts bookmaker odds into normalised implied probabilities,
then feeds them into the existing `interactive_adjust()` workflow as pre-filled
starting values rather than flat model priors.

#### `decimal_to_implied(odds: float) -> float`
Converts decimal odds to raw implied probability: `1 / odds`.

#### `normalise(implied: dict[str, float]) -> dict[str, float]`
Strips bookmaker overround by dividing each value by the sum.
Result sums exactly to 1.0.

#### `odds_to_p_win(raw_odds: dict[str, float]) -> dict[str, float]`
Accepts `{rider_name_fragment: decimal_odds}` for an outright win market.
Returns `{fragment: p_win}` normalised across all entrants.

#### `h2h_to_prob(rider_a, odds_a, rider_b, odds_b) -> dict[str, float]`
Converts a head-to-head market to individual win probabilities.
Normalises both implied probs; returns `{rider_a: prob_a, rider_b: prob_b}`.

#### `apply_odds_to_probs(probs, p_win_map, riders_by_id) -> dict[str, RiderProb]`
Patches an existing probs dict produced by `generate_priors()`:
- Sets `rp.p_win` to the supplied value
- Derives `p_top3 = clamp(p_win / 0.35)` (inverse of existing prior ratio)
- Derives `p_top10 = clamp(p_top3 / 0.30)`
- Derives `p_top15 = clamp(p_top10 / 0.65)`
- Sets `rp.source = "odds"` and `rp.model_confidence = 0.8`
- Records all four fields in `rp.manual_overrides`

Rider matching uses the same case-insensitive fragment lookup as `_find_rider`
in `probabilities.py`. Unmatched fragments are silently skipped.

#### `cli_odds_input(probs, stage, riders, _input_fn=input) -> dict[str, RiderProb]`
Interactive CLI that collects bookmaker odds before handing off to
`interactive_adjust()`.

Outright format:
```
milan 4.50
girmay 6.00
```

H2H format:
```
h2h milan 1.80 vs girmay 2.10
```

- Type `done` to apply collected odds and proceed.
- Type `skip` to bypass odds input and use model priors unchanged.
- Invalid lines print an error and continue — valid lines in the same session still apply.
- Outrights are normalised together (across all fragments entered), then applied.
- H2H pairs are applied independently after outrights.

---

### `main.py` changes

Added `--odds` flag to the `brief` subcommand:

```
python3 main.py brief --stage 1 --odds
```

In `cmd_brief`, before calling `interactive_adjust()`:
```python
if getattr(args, "odds", False):
    probs = cli_odds_input(probs, stage, riders)
probs = interactive_adjust(probs, stage, riders)
```

Without `--odds`, behaviour is unchanged.

---

## tests/test_odds.py — 16 tests

| Test | What it verifies |
|---|---|
| `test_decimal_to_implied_2` | 2.0 → 0.5 |
| `test_decimal_to_implied_4` | 4.0 → 0.25 |
| `test_normalise_two_riders_sum_to_one` | overround removed, sum = 1.0 |
| `test_normalise_three_riders_removes_overround` | correct ordering preserved |
| `test_odds_to_p_win_normalised` | 3-rider outright sums to 1.0 |
| `test_h2h_to_prob_sums_to_one` | H2H sum = 1.0 |
| `test_h2h_favourite_gets_higher_prob` | lower odds → higher probability |
| `test_apply_odds_sets_p_win` | p_win set to supplied value |
| `test_apply_odds_hierarchy_consistent` | p_top3 > p_win, p_top10 > p_top3, etc. |
| `test_apply_odds_unmatched_rider_unchanged` | non-matching riders untouched |
| `test_apply_odds_source_set_to_odds` | source = "odds" for matched rider |
| `test_apply_odds_model_confidence_set` | model_confidence = 0.8 |
| `test_cli_skip_returns_unchanged` | skip leaves probs unchanged |
| `test_cli_single_outright_applied` | single entry applied with source="odds" |
| `test_cli_invalid_line_skipped_valid_applied` | bad line skipped, good line still works |
| `test_cli_h2h_applied_to_both_riders` | H2H updates both riders |

---

## Spot-check

```python
>>> from scoring.odds import odds_to_p_win
>>> odds_to_p_win({"Milan": 2.50, "Girmay": 4.00})
{'Milan': 0.6154, 'Girmay': 0.3846}
# sum = 1.0 ✓
```

---

## Workflow in practice

```
$ python3 main.py brief --stage 5 --odds

  STAGE 5 — ODDS INPUT
  ─────────────────────────────────────────────────────────
  Enter outright win odds as: <rider fragment> <decimal odds>
  Enter H2H odds as:          h2h <rider_a fragment> <odds_a> vs <rider_b fragment> <odds_b>
  Type 'done' when finished. Type 'skip' to use model priors only.

  odds> milan 3.20
  odds> girmay 4.50
  odds> cavendish 7.00
  odds> h2h milan 1.70 vs ewan 2.20
  odds> done

# Odds applied → interactive_adjust() opens with pre-filled values
```

---

## Known limitations

- Outright odds entered in one `cli_odds_input` session are normalised together.
  If you enter only 3 riders from a 15-rider market, the probabilities are relative
  only to those 3 — not to the full market. This is intentional: you enter the
  riders you care about for Holdet purposes.
- H2H p_win values are applied as raw overrides independently of any outright entries
  in the same session. If both an outright and a H2H entry match the same rider, the
  H2H value wins (applied last).
- No persistence of raw odds — only the derived probabilities are saved to state.json
  via the existing `save_probs()` flow in `cmd_brief`.
