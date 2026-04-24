"""
tests/test_tracker.py — Unit tests for output/tracker.py.

All mocked — no live API calls, no file I/O side effects.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_rider_prob(rider_id="r1", source="model",
                     p_win=0.3, p_top3=0.5, p_top15=0.7, p_dnf=0.05):
    from scoring.probabilities import RiderProb
    return RiderProb(
        rider_id=rider_id,
        stage_number=1,
        p_win=p_win,
        p_top3=p_top3,
        p_top10=p_top3,
        p_top15=p_top15,
        p_dnf=p_dnf,
        source=source,
    )


def _make_stage_result(finish_order=None, dnf_riders=None):
    from scoring.engine import StageResult
    return StageResult(
        stage_number=1,
        finish_order=finish_order or [],
        times_behind_winner={},
        sprint_point_winners={},
        kom_point_winners={},
        jersey_winners={},
        most_aggressive=None,
        dnf_riders=dnf_riders or [],
        dns_riders=[],
        disqualified=[],
        ttt_team_order=None,
        gc_standings=[],
    )


def _make_state(my_team=None):
    return {
        "my_team": ["r1"] if my_team is None else my_team,
        "captain": "r1",
        "bank": 50_000_000,
        "current_stage": 1,
        "stages_completed": [],
        "brier_history": [],
    }


# ── Tests: record_stage_accuracy ──────────────────────────────────────────────

class TestRecordStageAccuracy(unittest.TestCase):

    def setUp(self):
        from output.tracker import record_stage_accuracy
        self.record = record_stage_accuracy

    def test_returns_list_of_prob_accuracy(self):
        from output.tracker import ProbAccuracy
        probs = {"r1": _make_rider_prob("r1")}
        actuals = _make_stage_result(finish_order=["r1"])
        state = _make_state()
        result = self.record(1, probs, actuals, state)
        self.assertIsInstance(result, list)
        self.assertTrue(len(result) > 0)
        self.assertIsInstance(result[0], ProbAccuracy)

    def test_returns_four_events_per_rider(self):
        """Each rider should produce 4 records: win, top3, top15, dnf."""
        probs = {"r1": _make_rider_prob("r1")}
        actuals = _make_stage_result(finish_order=["r1"])
        state = _make_state()
        result = self.record(1, probs, actuals, state)
        events = {r.event for r in result if r.rider_id == "r1"}
        self.assertEqual(events, {"win", "top3", "top15", "dnf"})

    def test_brier_score_computed_correctly_win(self):
        """prob=0.3, actual=1.0 → brier = (0.3-1.0)² = 0.49"""
        probs = {"r1": _make_rider_prob("r1", p_win=0.3)}
        actuals = _make_stage_result(finish_order=["r1"])
        state = _make_state()
        result = self.record(1, probs, actuals, state)
        win_rec = next(r for r in result if r.rider_id == "r1" and r.event == "win")
        self.assertAlmostEqual(win_rec.model_brier, 0.49, places=6)
        self.assertAlmostEqual(win_rec.actual, 1.0)

    def test_brier_score_dnf_zero_actual(self):
        """prob=0.05, actual=0.0 → brier = (0.05-0.0)² = 0.0025"""
        probs = {"r1": _make_rider_prob("r1", p_dnf=0.05)}
        actuals = _make_stage_result(finish_order=["r1"], dnf_riders=[])
        state = _make_state()
        result = self.record(1, probs, actuals, state)
        dnf_rec = next(r for r in result if r.rider_id == "r1" and r.event == "dnf")
        self.assertAlmostEqual(dnf_rec.model_brier, 0.0025, places=6)
        self.assertAlmostEqual(dnf_rec.actual, 0.0)

    def test_manual_brier_is_none_when_source_is_model(self):
        """Source='model' → manual_prob and manual_brier must be None."""
        probs = {"r1": _make_rider_prob("r1", source="model")}
        actuals = _make_stage_result(finish_order=["r1"])
        state = _make_state()
        result = self.record(1, probs, actuals, state)
        for rec in result:
            if rec.rider_id == "r1":
                self.assertIsNone(rec.manual_prob)
                self.assertIsNone(rec.manual_brier)

    def test_manual_brier_computed_when_adjusted(self):
        """Source='adjusted' → manual_brier must be a float."""
        probs = {"r1": _make_rider_prob("r1", source="adjusted", p_win=0.3)}
        actuals = _make_stage_result(finish_order=["r1"])
        state = _make_state()
        result = self.record(1, probs, actuals, state)
        win_rec = next(r for r in result if r.rider_id == "r1" and r.event == "win")
        self.assertIsNotNone(win_rec.manual_brier)
        self.assertIsInstance(win_rec.manual_brier, float)


# ── Tests: format_brier_summary ───────────────────────────────────────────────

class TestFormatBrierSummary(unittest.TestCase):

    def setUp(self):
        from output.tracker import format_brier_summary, ProbAccuracy
        self.fmt = format_brier_summary
        self.ProbAccuracy = ProbAccuracy

    def _make_rec(self, stage=1, event="win", model_brier=0.2,
                  manual_brier=None, manual_prob=None):
        return self.ProbAccuracy(
            stage=stage,
            rider_id="r1",
            event=event,
            model_prob=0.5,
            manual_prob=manual_prob,
            actual=1.0,
            model_brier=model_brier,
            manual_brier=manual_brier,
        )

    def test_beat_model_message_when_manual_lower(self):
        """When manual_brier < model_brier, output must say 'you beat the model'."""
        recs = [self._make_rec(model_brier=0.2, manual_brier=0.1, manual_prob=0.4)]
        result = self.fmt(recs)
        self.assertIn("you beat the model", result)

    def test_no_beat_message_when_manual_higher(self):
        recs = [self._make_rec(model_brier=0.1, manual_brier=0.2, manual_prob=0.4)]
        result = self.fmt(recs)
        self.assertNotIn("you beat the model", result)

    def test_season_avg_shown(self):
        """Season average must appear in output."""
        recs = [
            self._make_rec(stage=1, model_brier=0.2),
            self._make_rec(stage=2, model_brier=0.1),
        ]
        result = self.fmt(recs)
        self.assertIn("Season", result)
        self.assertIn("model avg", result)

    def test_stage_label_shown(self):
        recs = [self._make_rec(stage=3, model_brier=0.15)]
        result = self.fmt(recs)
        self.assertIn("Stage 3", result)

    def test_empty_records_returns_message(self):
        result = self.fmt([])
        self.assertIn("No accuracy", result)


# ── Tests: save_accuracy ──────────────────────────────────────────────────────

class TestSaveAccuracy(unittest.TestCase):

    def setUp(self):
        from output.tracker import save_accuracy, ProbAccuracy
        self.save = save_accuracy
        self.ProbAccuracy = ProbAccuracy

    def _make_rec(self, stage=1):
        return self.ProbAccuracy(
            stage=stage,
            rider_id="r1",
            event="win",
            model_prob=0.3,
            manual_prob=None,
            actual=1.0,
            model_brier=0.49,
            manual_brier=None,
        )

    def test_appends_to_brier_history(self):
        state = {"brier_history": [], "bank": 50_000_000}
        rec = self._make_rec()
        updated = self.save([rec], state)
        self.assertEqual(len(updated["brier_history"]), 1)

    def test_appends_not_replaces(self):
        """Existing records must be preserved."""
        existing = {"stage": 0, "rider_id": "old", "event": "win",
                    "model_prob": 0.5, "manual_prob": None, "actual": 0.0,
                    "model_brier": 0.25, "manual_brier": None}
        state = {"brier_history": [existing], "bank": 50_000_000}
        rec = self._make_rec(stage=1)
        updated = self.save([rec], state)
        self.assertEqual(len(updated["brier_history"]), 2)

    def test_does_not_overwrite_other_keys(self):
        """Other state keys must be untouched."""
        state = {"brier_history": [], "bank": 99_000_000, "rank": 42}
        rec = self._make_rec()
        updated = self.save([rec], state)
        self.assertEqual(updated["bank"], 99_000_000)
        self.assertEqual(updated["rank"], 42)

    def test_creates_brier_history_if_absent(self):
        state = {"bank": 50_000_000}   # no brier_history key
        rec = self._make_rec()
        updated = self.save([rec], state)
        self.assertIn("brier_history", updated)
        self.assertEqual(len(updated["brier_history"]), 1)


# ── Tests: compute_stage_brier ────────────────────────────────────────────────

class TestComputeStageBrier(unittest.TestCase):

    def _make_accuracy(self, event, model_prob, actual):
        from output.tracker import ProbAccuracy
        return ProbAccuracy(
            stage=1,
            rider_id="r1",
            event=event,
            model_prob=model_prob,
            manual_prob=None,
            actual=actual,
            model_brier=(model_prob - actual) ** 2,
            manual_brier=None,
        )

    def test_brier_score_perfect_predictor_is_zero(self):
        """p_win=1.0 for the actual winner → Brier = 0."""
        from output.tracker import compute_stage_brier
        recs = [self._make_accuracy("win", 1.0, 1.0)]
        result = compute_stage_brier(recs)
        self.assertAlmostEqual(result["brier_p_win"], 0.0, places=10)

    def test_brier_score_uniform_predictor_matches_naive_baseline(self):
        """
        Uniform p_win = 1/n for n riders: Brier = (1/n)^2 * (n-1) + (1 - 1/n)^2 * 1.
        For 8 riders: Brier = 7*(1/8)^2 + (7/8)^2 = 0.10937... + 0.76562... / 8 ≈ 0.109375 + 0.765625 = not 0.25.
        Simpler check: uniform p = 1/n for one winner → model_brier = (1-1/n)^2, for losers (1/n)^2.
        Average over n = (1/n)*(1-1/n)^2 + (n-1)/n*(1/n)^2 = (1-1/n)/n + (n-1)/n^2 ... = (n-1)/n^2.
        This equals 1/n - 1/n^2 which is NOT 0.25.
        We simply test that the function returns the correct mean.
        """
        from output.tracker import compute_stage_brier
        n = 8
        p = 1.0 / n
        # 1 winner, 7 non-winners, all with p_win = 1/n
        recs = [self._make_accuracy("win", p, 1.0)] + \
               [self._make_accuracy("win", p, 0.0) for _ in range(n - 1)]
        result = compute_stage_brier(recs)
        expected = ((p - 1.0) ** 2 + (n - 1) * p ** 2) / n
        self.assertAlmostEqual(result["brier_p_win"], expected, places=8)
        self.assertEqual(result["n_riders_scored"], n)

    def test_brier_stage1_writes_calibration_history(self):
        """save_calibration_history creates calibration_history.json with one entry."""
        import json
        import tempfile
        import os
        from output.tracker import save_calibration_history

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "calibration_history.json")
            save_calibration_history(
                stage=1,
                date="2026-05-09",
                stage_type="flat",
                inferred_scenario="bunch_sprint",
                brier_p_win=0.043,
                brier_p_top15=0.118,
                n_riders_scored=8,
                notes="first baseline — small sample",
                path=path,
            )
            assert os.path.exists(path), "calibration_history.json was not created"
            with open(path) as f:
                history = json.load(f)
            assert len(history) == 1, "Expected exactly 1 entry"

    def test_calibration_history_has_required_fields(self):
        """Entry in calibration_history.json contains all required fields."""
        import json
        import tempfile
        import os
        from output.tracker import save_calibration_history

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "calibration_history.json")
            save_calibration_history(
                stage=1,
                date="2026-05-09",
                stage_type="flat",
                inferred_scenario="bunch_sprint",
                brier_p_win=0.043,
                brier_p_top15=0.118,
                n_riders_scored=8,
                path=path,
            )
            with open(path) as f:
                entry = json.load(f)[0]
            required = {"stage", "date", "stage_type", "inferred_scenario",
                        "brier_p_win", "brier_p_top15", "n_riders_scored"}
            for field in required:
                assert field in entry, f"Missing field: {field}"

    def test_save_calibration_history_includes_recorded_at(self):
        """Entry must have a 'recorded_at' key with an ISO timestamp string."""
        import json, tempfile, os
        from output.tracker import save_calibration_history

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "calibration_history.json")
            save_calibration_history(
                stage=1, date="2026-05-09", stage_type="flat",
                inferred_scenario="bunch_sprint",
                brier_p_win=0.04, brier_p_top15=0.12, n_riders_scored=8,
                path=path,
            )
            entry = json.load(open(path))[0]
        assert "recorded_at" in entry, "Missing 'recorded_at' field"
        assert "T" in entry["recorded_at"], "recorded_at should be an ISO timestamp"

    def test_save_calibration_history_includes_scope(self):
        """Entry must have scope='team_only'."""
        import json, tempfile, os
        from output.tracker import save_calibration_history

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "calibration_history.json")
            save_calibration_history(
                stage=1, date="2026-05-09", stage_type="flat",
                inferred_scenario="gc_day",
                brier_p_win=0.04, brier_p_top15=0.12, n_riders_scored=8,
                path=path,
            )
            entry = json.load(open(path))[0]
        assert entry.get("scope") == "team_only"

    def test_save_calibration_history_rejects_invalid_scenario(self):
        """inferred_scenario='bunch sprint' (with space) must raise ValueError."""
        import tempfile, os
        from output.tracker import save_calibration_history

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "calibration_history.json")
            with self.assertRaises(ValueError):
                save_calibration_history(
                    stage=1, date="2026-05-09", stage_type="flat",
                    inferred_scenario="bunch sprint",   # space instead of underscore
                    brier_p_win=0.04, brier_p_top15=0.12, n_riders_scored=8,
                    path=path,
                )

    def test_save_calibration_history_accepts_none_scenario(self):
        """inferred_scenario=None must not raise and is written as null."""
        import json, tempfile, os
        from output.tracker import save_calibration_history

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "calibration_history.json")
            save_calibration_history(
                stage=1, date="2026-05-09", stage_type="flat",
                inferred_scenario=None,
                brier_p_win=0.04, brier_p_top15=0.12, n_riders_scored=8,
                path=path,
            )
            entry = json.load(open(path))[0]
        assert entry["inferred_scenario"] is None


if __name__ == "__main__":
    unittest.main()
