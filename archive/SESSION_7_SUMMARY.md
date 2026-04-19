# Session 7 Summary — Reporting + Brier Score Tracking

**Date:** 2026-04-19
**Tests:** 294/294 passing (265 inherited + 29 new)
**Branch:** claude/session-7 → merged to main via PR #7

---

## What was built

### `output/__init__.py`
Empty package marker.

### `output/report.py`

#### `BriefingOutput` dataclass
Holds all data needed to render a pre-stage briefing:
- `stage`, `my_team`, `captain`, `riders`, `probs`
- `current_team_ev`, `suggested_profile`, `suggested_profile_reason`
- `profiles: dict[RiskProfile, ProfileRecommendation]`

#### `format_briefing(briefing, state) -> str`
Renders full pre-stage briefing. Sections:
1. Stage header: `Stage N — [start] → [finish] ([type], [distance]km)`
2. DNS/injury alerts: any team rider with `status != "active"`, with projected penalty
3. Probability table: `[C]` captain marker, `*` on `source="adjusted"` rows; columns: name | team | value | pWin | pTop3 | pTop15 | pDNF | source
4. Profile recommendation table: ANCHOR / BALANCED / AGGRESSIVE / ALL-IN columns with captain, EV, p90 upside, p10 downside, transfers, transfer cost, net EV
5. Transfer detail lines (sell/buy) per profile
6. Suggested profile with plain-English reasoning

#### `format_status(state, riders) -> str`
Extracted from `cmd_status` inline logic. Shows:
- Stage N/21, bank, rank
- Per-rider: `[C]` captain marker, name, team, value, delta vs `start_value`
- `*** DNS ***` flag and ALERT line for non-active riders
- Total team value

### `output/tracker.py`

#### `ProbAccuracy` dataclass
Matches ARCHITECTURE.md §6:
```python
stage, rider_id, event,          # "win" | "top3" | "top15" | "dnf"
model_prob, manual_prob,          # manual_prob = None if source="model"
actual,                           # 1.0 or 0.0
model_brier, manual_brier         # (prob - actual)²; manual_brier = None if no override
```

#### `record_stage_accuracy(stage_number, probs, actuals, state) -> list[ProbAccuracy]`
After `settle`, computes 4 records per team rider (win, top3, top15, dnf):
- `model_prob` from `probs[rider_id]`
- `manual_prob` populated only when `source="adjusted"` (same value — already the override)
- `actual` derived from `StageResult.finish_order` and `dnf_riders`
- `model_brier = (model_prob - actual)²`
- `manual_brier = (manual_prob - actual)²` if manual_prob else None

#### `format_brier_summary(accuracy_records) -> str`
```
Stage 1 Brier: model=0.142, manual=0.118 ✓ (you beat the model)
Season (1 stage): model avg=0.142, manual avg=0.118
You beat the model on 1/1 stages
```

#### `save_accuracy(records, state) -> dict`
Appends serialised `ProbAccuracy` dicts to `state["brier_history"]`. Creates key if absent. Returns updated state without touching other keys.

---

## main.py changes

### `cmd_brief`
Constructs `BriefingOutput` after `optimize_all_profiles()`, calls `format_briefing()`. Replaces the previous mix of `format_briefing_table()` + inline `suggest_profile` print.

### `cmd_settle`
After scoring, reconstructs `RiderProb` objects from `state["probs_by_stage"][str(stage)]` and calls the full Brier pipeline:
```python
accuracy_records = record_stage_accuracy(stage_number, stage_probs, result, state)
state = save_accuracy(accuracy_records, state)
print(format_brier_summary(accuracy_records))
```
Skips gracefully with a message if no saved probs for the stage (e.g. settle run without prior brief).

### `cmd_status`
Replaced ~40-line inline block with:
```python
riders = load_riders(riders_path) if os.path.exists(riders_path) else []
print("\n" + format_status(state, riders))
```

### `_load_stage` fix
Previous code did `list(stages_data.values())` on any dict, which would iterate over string/int/list metadata keys and crash on `.get("number")`. Fixed to:
```python
if isinstance(stages_data, list):
    stages_list = stages_data
elif isinstance(stages_data, dict) and "stages" in stages_data:
    stages_list = stages_data["stages"]   # handles actual data/stages.json format
elif isinstance(stages_data, dict):
    stages_list = [v for v in stages_data.values() if isinstance(v, dict)]
```
Also added `if not isinstance(s, dict): continue` guard in the loop.

---

## data/stages.json (pre-existing, not rebuilt)

Already present in the repo from a prior session — all 21 Giro 2026 stages with:
- Official Holdet event IDs (48281–48301)
- Dates and `trading_close` timestamps
- Official Danish stage type names (`stage_type_da`): `Flad`, `Medium bjerg`, `Bjerg`, `Enkeltstart`
- Mapped `stage_type`: `flat`, `hilly`, `mountain`, `itt`
- Notes on key strategy points (early trading windows, TTT risk, rest days)

Key route facts:
- Stages 1–3: Bulgaria (Nessebar→Burgas, Burgas→Veliko Tarnovo, Plovdiv→Sofia)
- Stage 7: Blockhaus summit finish (mountain) — early trading window 08:45
- Stage 10: 40.2km ITT Viareggio→Massa — significant late-arrival risk for sprinters
- Stage 14: Aosta→Pila summit finish (mountain)
- Stages 16, 19, 20: Mountain stages post-second rest day
- Stage 19: Dolomites queen stage (Passo Giau, 4,834m gain)
- Stage 21: Ceremonial sprint in Rome

---

## Tests

### `tests/test_report.py` — 14 tests

`TestFormatBriefing` (7):
- `test_returns_string`
- `test_stage_header_present` — number, start, finish, type, distance
- `test_dns_alert_when_rider_not_active`
- `test_no_dns_alert_for_active_rider`
- `test_adjusted_probs_marked_with_asterisk`
- `test_four_profile_rows_present` — ANCHOR / BALANCED / AGGRESSIVE / ALL-IN
- `test_suggested_profile_present`

`TestFormatStatus` (7):
- `test_returns_string`
- `test_captain_marker_present` — `[C]` shown
- `test_dns_alert_in_status`
- `test_no_dns_alert_for_active_in_status`
- `test_total_value_shown`
- `test_bank_shown`
- `test_no_team_message_when_empty`

### `tests/test_tracker.py` — 15 tests

`TestRecordStageAccuracy` (6):
- `test_returns_list_of_prob_accuracy`
- `test_returns_four_events_per_rider`
- `test_brier_score_computed_correctly_win` — prob=0.3, actual=1.0 → 0.49
- `test_brier_score_dnf_zero_actual` — prob=0.05, actual=0.0 → 0.0025
- `test_manual_brier_is_none_when_source_is_model`
- `test_manual_brier_computed_when_adjusted`

`TestFormatBrierSummary` (5):
- `test_beat_model_message_when_manual_lower`
- `test_no_beat_message_when_manual_higher`
- `test_season_avg_shown`
- `test_stage_label_shown`
- `test_empty_records_returns_message`

`TestSaveAccuracy` (4):
- `test_appends_to_brier_history`
- `test_appends_not_replaces`
- `test_does_not_overwrite_other_keys`
- `test_creates_brier_history_if_absent`

---

## Known limitations

- `format_briefing` probability table shows only riders in `probs` dict — riders with no prior (e.g. new signing not in `riders.json`) are silently skipped
- `manual_prob` in `ProbAccuracy` is set to the same value as `model_prob` when `source="adjusted"` — this is correct because `RiderProb.p_win` etc. already hold the override value after `interactive_adjust()`; the distinction between original model value and override is not currently stored
- Brier tracking requires `brief` to have been run first (saves `probs_by_stage`); `settle` without a prior `brief` skips Brier gracefully
