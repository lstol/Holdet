"""
tests/test_calibrate.py — Session 19: Calibration feedback loop

12 tests covering parse, Brier formula, suggest, holdout, dry-run, history.
"""
from __future__ import annotations

import json
import os
import textwrap

import pytest

from scripts.calibrate import (
    _append_calibration_history,
    _brier_score,
    aggregate_metrics,
    compute_brier_scores,
    evaluate_holdout,
    parse_validation_log,
    run_calibration,
    scenario_frequency_analysis,
    suggest_adjustments,
    Suggestion,
)
from scoring.probabilities import ROLE_TOP15, RiderRole


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _entries(tuples):
    """Convert (stage, role, stage_type, _unused_p, outcome) tuples to entry dicts."""
    return [
        {
            "stage": t[0],
            "role": t[1].lower(),
            "stage_type": t[2],
            "outcome": t[4],
            "actual_delta": t[4] * 100,
            "rider": f"rider_{i}",
        }
        for i, t in enumerate(tuples)
    ]


# ── Case datasets ─────────────────────────────────────────────────────────────

CASE_A = [  # consistent underestimation — should suggest AND pass holdout
    (1, "SPRINTER", "flat", 0.4, 1),
    (2, "SPRINTER", "flat", 0.4, 1),
    (3, "SPRINTER", "flat", 0.4, 1),
    (4, "SPRINTER", "flat", 0.4, 0),
]

CASE_B = [  # training suggests increase, holdout punishes it — reject
    (1, "SPRINTER", "flat", 0.4, 1),
    (2, "SPRINTER", "flat", 0.4, 1),
    (3, "SPRINTER", "flat", 0.4, 1),
    (4, "SPRINTER", "flat", 0.4, 0),
    (5, "SPRINTER", "flat", 0.4, 0),
]

CASE_C = [  # mixed direction — no suggestion
    (1, "SPRINTER", "flat", 0.4, 1),
    (2, "SPRINTER", "flat", 0.4, 0),
    (3, "SPRINTER", "flat", 0.4, 1),
]

CASE_D = [  # overshoot protection — observed=1.0
    (1, "SPRINTER", "flat", 0.4, 1),
    (2, "SPRINTER", "flat", 0.4, 1),
    (3, "SPRINTER", "flat", 0.4, 1),
]


# ── 1. Parse: empty / missing ─────────────────────────────────────────────────

class TestParseValidationLog:
    def test_parse_empty_validation_log_returns_no_entries(self, tmp_path):
        missing = str(tmp_path / "no_such_file.md")
        assert parse_validation_log(missing) == []

    def test_parse_validation_log_returns_correct_fields(self, tmp_path):
        log = tmp_path / "validation_log.md"
        log.write_text(textwrap.dedent("""\
            # Validation Log

            | Timestamp | Stage | Rider | Field | Engine | Actual | Delta | Notes |
            |-----------|-------|-------|-------|--------|--------|-------|-------|
            | 2026-05-01 10:00 | Stage 3 | Tadej Pogacar | total_rider_value_delta | +100,000 | +120,000 | +20,000 |  |
            | 2026-05-01 10:00 | Stage 3 | Cavendish | other_field | +50,000 | +60,000 | +10,000 |  |
        """))
        entries = parse_validation_log(str(log))
        assert len(entries) == 1  # only total_rider_value_delta row
        e = entries[0]
        assert e["stage"] == 3
        assert e["rider"] == "Tadej Pogacar"
        assert e["engine_delta"] == 100_000
        assert e["actual_delta"] == 120_000


# ── 2. Brier formula ──────────────────────────────────────────────────────────

class TestBrierScore:
    def test_brier_score_perfect_prediction_is_zero(self):
        assert _brier_score(1.0, 1) == 0.0
        assert _brier_score(0.0, 0) == 0.0

    def test_brier_score_worst_prediction_is_one(self):
        assert _brier_score(0.0, 1) == 1.0
        assert _brier_score(1.0, 0) == 1.0


# ── 3. Suggest: count guard ───────────────────────────────────────────────────

class TestSuggestAdjustments:
    def test_suggest_no_change_when_fewer_than_3_stages(self):
        entries = _entries(CASE_A[:2])  # only 2 stages
        metrics = aggregate_metrics(entries)
        suggestions = suggest_adjustments(metrics)
        assert suggestions == []

    def test_suggest_no_change_when_direction_mixed(self):
        # Case C: outcomes alternate → direction="mixed"
        entries = _entries(CASE_C)
        metrics = aggregate_metrics(entries)
        suggestions = suggest_adjustments(metrics)
        assert suggestions == []

    def test_suggest_does_not_overshoot_observed_mean(self):
        # Case D: all outcomes=1, observed=1.0
        entries = _entries(CASE_D)
        metrics = aggregate_metrics(entries)
        suggestions = suggest_adjustments(metrics)
        assert len(suggestions) == 1
        s = suggestions[0]
        # new must not exceed observed, must not undershoot current
        low = min(s.old, s.observed)
        high = max(s.old, s.observed)
        assert low <= s.new <= high
        assert s.new <= 1.0


# ── 4. Holdout validation ─────────────────────────────────────────────────────

class TestHoldout:
    def test_suggest_change_accepted_when_holdout_passes(self):
        # Case A: 4 stages → LOOCV; suggestion nudges toward observed=0.75
        # ROLE_TOP15["sprinter"]["flat"]=0.45, alpha=0.3 → new=0.54
        entries = _entries(CASE_A)
        suggestion = Suggestion(
            role="sprinter", stage_type="flat",
            old=0.45, new=0.54, observed=0.75, count=4,
        )
        b_before, b_after = evaluate_holdout(entries, suggestion)
        assert b_after < b_before

    def test_holdout_rejects_overfitting_case(self):
        # Case B: 5 stages (≥5) → last stage is holdout; outcome=0 contradicts
        # training trend, so raising p worsens Brier on the holdout stage
        entries = _entries(CASE_B)
        suggestion = Suggestion(
            role="sprinter", stage_type="flat",
            old=0.45, new=0.54, observed=0.75, count=4,
        )
        b_before, b_after = evaluate_holdout(entries, suggestion)
        assert b_after >= b_before  # holdout rejects


# ── 5. Dry-run: no write ──────────────────────────────────────────────────────

class TestDryRun:
    def test_dry_run_does_not_write_calibration_history(self, tmp_path):
        history_path = str(tmp_path / "calibration_history.json")
        entries = _entries(CASE_A)
        run_calibration(entries, dry_run=True, history_path=history_path)
        assert not os.path.exists(history_path)


# ── 6. History: append-only ───────────────────────────────────────────────────

class TestCalibrationHistory:
    def test_calibration_history_appends_not_overwrites(self, tmp_path):
        history_path = str(tmp_path / "calibration_history.json")
        change_a = [{"constant": "ROLE_TOP15.SPRINTER.flat", "old": 0.45,
                     "new": 0.48, "brier_before": 0.21, "brier_after": 0.18}]
        change_b = [{"constant": "ROLE_TOP15.CLIMBER.mountain", "old": 0.40,
                     "new": 0.43, "brier_before": 0.20, "brier_after": 0.17}]

        _append_calibration_history(history_path, [1, 2], change_a)
        _append_calibration_history(history_path, [3, 4], change_b)

        with open(history_path) as fh:
            history = json.load(fh)

        assert len(history) == 2
        assert history[0]["stages_used"] == [1, 2]
        assert history[1]["stages_used"] == [3, 4]
        assert history[0]["changes"][0]["constant"] == "ROLE_TOP15.SPRINTER.flat"
        assert history[1]["changes"][0]["constant"] == "ROLE_TOP15.CLIMBER.mountain"


# ── 7. Scenario frequency gap ────────────────────────────────────────────────

class TestScenarioFrequency:
    def test_scenario_frequency_gap_flagged_when_large(self):
        # flat: bunch_sprint expected=0.65, but all 4 stages are bunch_sprint → observed=1.0
        # gap = 0.35 > 0.25 → flagged
        entries = [
            {"stage": i, "stage_type": "flat", "scenario": "bunch_sprint"}
            for i in range(1, 5)
        ]
        result = scenario_frequency_analysis(entries, [])
        flagged = [r for r in result if r["flagged"]]
        assert any(
            r["scenario"] == "bunch_sprint" and r["stage_type"] == "flat"
            for r in flagged
        )
