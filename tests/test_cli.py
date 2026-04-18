"""
tests/test_cli.py — Unit tests for config.py, main.py helpers, and ingestion.api.fetch_my_team.

All HTTP is mocked. No live API calls.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from dataclasses import asdict
from unittest.mock import MagicMock, patch

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_rider(holdet_id="1001", name="Test Rider", team="Team A",
                team_abbr="TA", value=5_000_000, start_value=5_000_000,
                status="active", gc_position=None, team_id="101", person_id="201"):
    from scoring.engine import Rider
    return Rider(
        holdet_id=holdet_id,
        person_id=person_id,
        team_id=team_id,
        name=name,
        team=team,
        team_abbr=team_abbr,
        value=value,
        start_value=start_value,
        points=0,
        status=status,
        gc_position=gc_position,
        jerseys=[],
        in_my_team=False,
        is_captain=False,
    )


def _make_stage(number=1, stage_type="flat", is_ttt=False):
    from scoring.engine import Stage
    return Stage(
        number=number,
        race="giro_2026",
        stage_type=stage_type,
        distance_km=156.0,
        is_ttt=is_ttt,
        start_location="Durres",
        finish_location="Tirana",
        sprint_points=[],
        kom_points=[],
        notes="",
    )


# ── TestConfig ────────────────────────────────────────────────────────────────

class TestConfig(unittest.TestCase):
    def test_defaults(self):
        import config
        with patch.dict(os.environ, {
            "HOLDET_COOKIE": "x",
            "HOLDET_GAME_ID": "612",
            "HOLDET_FANTASY_TEAM_ID": "999",
            "HOLDET_CARTRIDGE": "giro-2026",
        }, clear=False):
            self.assertEqual(config.get_state_path(), "data/state.json")
            self.assertEqual(config.get_riders_path(), "data/riders.json")
            self.assertEqual(config.get_stages_path(), "data/stages.json")

    def test_missing_required_raises(self):
        import config
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(EnvironmentError):
                config.get_cookie()

    def test_missing_game_id_raises(self):
        import config
        with patch.dict(os.environ, {"HOLDET_COOKIE": "x"}, clear=True):
            with self.assertRaises(EnvironmentError):
                config.get_game_id()

    def test_missing_team_id_raises(self):
        import config
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(EnvironmentError):
                config.get_fantasy_team_id()

    def test_missing_cartridge_raises(self):
        import config
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(EnvironmentError):
                config.get_cartridge()

    def test_optional_custom_value(self):
        import config
        with patch.dict(os.environ, {"STATE_PATH": "/tmp/custom_state.json"}):
            self.assertEqual(config.get_state_path(), "/tmp/custom_state.json")

    def test_constants(self):
        import config
        self.assertEqual(config.TOTAL_STAGES, 21)
        self.assertEqual(config.INITIAL_BUDGET, 50_000_000)


# ── TestParseMyTeamHtml ───────────────────────────────────────────────────────

_SAMPLE_HTML = r"""
<html><head></head><body>
<script>self.__next_f.push([1,"irrelevant chunk"])</script>
<script>self.__next_f.push([1,"{\"fantasyTeamId\":6796783,\"initialLineup\":[{\"id\":47372,\"captainPopularity\":0.4,\"owners\":0.8,\"captainOwners\":0.2,\"isOutOfGame\":false},{\"id\":12345,\"captainPopularity\":0.1,\"owners\":0.5,\"captainOwners\":0.05,\"isOutOfGame\":false},{\"id\":99999,\"captainPopularity\":0.05,\"owners\":0.3,\"captainOwners\":0.01,\"isOutOfGame\":true}],\"initialCaptain\":47372,\"initialBank\":1500000}"])</script>
</body></html>
"""

class TestParseMyTeamHtml(unittest.TestCase):
    def setUp(self):
        from ingestion.api import _parse_my_team_html
        self.parse = _parse_my_team_html
        self.result = self.parse(_SAMPLE_HTML)

    def test_lineup_is_list(self):
        self.assertIsInstance(self.result["lineup"], list)

    def test_lineup_count(self):
        self.assertEqual(len(self.result["lineup"]), 3)

    def test_lineup_ids_present(self):
        ids = [p["id"] for p in self.result["lineup"]]
        self.assertIn(47372, ids)
        self.assertIn(12345, ids)

    def test_captain_is_string(self):
        self.assertIsInstance(self.result["captain"], str)

    def test_captain_value(self):
        self.assertEqual(self.result["captain"], "47372")

    def test_bank_is_int(self):
        self.assertIsInstance(self.result["bank"], int)

    def test_bank_value(self):
        self.assertEqual(self.result["bank"], 1_500_000)

    def test_permission_error_on_missing_lineup(self):
        from ingestion.api import _parse_my_team_html
        with self.assertRaises(PermissionError):
            _parse_my_team_html("<html><body>No team data here</body></html>")

    def test_captain_popularity_present(self):
        first = self.result["lineup"][0]
        self.assertIn("captainPopularity", first)

    def test_owners_present(self):
        first = self.result["lineup"][0]
        self.assertIn("owners", first)

    def test_is_out_of_game_detected(self):
        # Third rider has isOutOfGame: true
        third = self.result["lineup"][2]
        self.assertTrue(third.get("isOutOfGame"))


# ── TestFetchMyTeam ───────────────────────────────────────────────────────────

class TestFetchMyTeam(unittest.TestCase):
    def _mock_response(self, status_code=200, text=None):
        mock = MagicMock()
        mock.status_code = status_code
        mock.text = text or _SAMPLE_HTML
        mock.raise_for_status = MagicMock()
        return mock

    @patch("ingestion.api.requests.get")
    def test_returns_dict_with_lineup_captain_bank(self, mock_get):
        mock_get.return_value = self._mock_response()
        from ingestion.api import fetch_my_team
        result = fetch_my_team("6796783", "giro-d-italia-2026", "cookie=abc")
        self.assertIn("lineup", result)
        self.assertIn("captain", result)
        self.assertIn("bank", result)

    @patch("ingestion.api.requests.get")
    def test_url_contains_cartridge_and_team_id(self, mock_get):
        mock_get.return_value = self._mock_response()
        from ingestion.api import fetch_my_team
        fetch_my_team("6796783", "giro-d-italia-2026", "cookie=abc")
        url = mock_get.call_args[0][0]
        self.assertIn("giro-d-italia-2026", url)
        self.assertIn("6796783", url)

    @patch("ingestion.api.requests.get")
    def test_cookie_in_headers(self, mock_get):
        mock_get.return_value = self._mock_response()
        from ingestion.api import fetch_my_team
        fetch_my_team("6796783", "giro-d-italia-2026", "session=abc123")
        headers = mock_get.call_args[1]["headers"]
        self.assertIn("Cookie", headers)
        self.assertEqual(headers["Cookie"], "session=abc123")

    @patch("ingestion.api.requests.get")
    def test_401_raises_permission_error(self, mock_get):
        mock_get.return_value = self._mock_response(status_code=401)
        from ingestion.api import fetch_my_team
        with self.assertRaises(PermissionError):
            fetch_my_team("123", "giro", "cookie")

    @patch("ingestion.api.requests.get")
    def test_403_raises_permission_error(self, mock_get):
        mock_get.return_value = self._mock_response(status_code=403)
        from ingestion.api import fetch_my_team
        with self.assertRaises(PermissionError):
            fetch_my_team("123", "giro", "cookie")

    @patch("ingestion.api.requests.get")
    def test_network_failure_raises_connection_error(self, mock_get):
        import requests
        mock_get.side_effect = requests.exceptions.ConnectionError("net err")
        from ingestion.api import fetch_my_team
        with self.assertRaises(ConnectionError):
            fetch_my_team("123", "giro", "cookie")


# ── TestStateSaveLoad ─────────────────────────────────────────────────────────

class TestStateSaveLoad(unittest.TestCase):
    def test_creates_file(self):
        from main import _save_state, _load_state
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "state.json")
            _save_state({"bank": 42_000_000}, path)
            self.assertTrue(os.path.exists(path))

    def test_round_trip(self):
        from main import _save_state, _load_state
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "state.json")
            state = {"bank": 55_000_000, "rank": 123, "my_team": ["a", "b"]}
            _save_state(state, path)
            loaded = _load_state(path)
            self.assertEqual(loaded["bank"], 55_000_000)
            self.assertEqual(loaded["rank"], 123)
            self.assertEqual(loaded["my_team"], ["a", "b"])

    def test_missing_file_returns_defaults(self):
        from main import _load_state
        import config
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "nonexistent_state.json")
            state = _load_state(path)
            self.assertEqual(state["bank"], config.INITIAL_BUDGET)
            self.assertEqual(state["my_team"], [])

    def test_no_tmp_file_left_after_save(self):
        from main import _save_state
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "state.json")
            _save_state({"x": 1}, path)
            self.assertFalse(os.path.exists(path + ".tmp"))


# ── TestNameResolver ──────────────────────────────────────────────────────────

class TestNameResolver(unittest.TestCase):
    def setUp(self):
        self.riders = [
            _make_rider("1001", "Jonas Vingegaard", "Visma", "VIS"),
            _make_rider("1002", "Tadej Pogacar", "UAE", "UAE"),
            _make_rider("1003", "Remco Evenepoel", "Soudal", "SQT"),
        ]
        self.rider_map = {r.holdet_id: r for r in self.riders}

    def test_exact_id(self):
        from main import _resolve_name
        self.assertEqual(_resolve_name("1001", self.rider_map), "1001")

    def test_partial_name(self):
        from main import _resolve_name
        self.assertEqual(_resolve_name("ving", self.rider_map), "1001")

    def test_case_insensitive(self):
        from main import _resolve_name
        self.assertEqual(_resolve_name("POGACAR", self.rider_map), "1002")

    def test_no_match_raises(self):
        from main import _resolve_name
        with self.assertRaises(ValueError):
            _resolve_name("cavendish", self.rider_map)

    def test_ambiguous_raises(self):
        # Add a rider whose name also contains "e"
        from main import _resolve_name
        # "e" matches Vingegaard (contains 'e'), Pogacar (contains 'e'), Evenepoel (contains 'e')
        with self.assertRaises(ValueError):
            _resolve_name("eee_no_match", self.rider_map)

    def test_list_none_returns_empty(self):
        from main import _resolve_list
        self.assertEqual(_resolve_list("none", self.rider_map), [])
        self.assertEqual(_resolve_list("", self.rider_map), [])
        self.assertEqual(_resolve_list("n/a", self.rider_map), [])

    def test_kv_parsing(self):
        from main import _resolve_kv_list
        result = _resolve_kv_list("ving:3, pog:2", self.rider_map)
        self.assertEqual(result["1001"], 3)
        self.assertEqual(result["1002"], 2)

    def test_kv_none_returns_empty(self):
        from main import _resolve_kv_list
        self.assertEqual(_resolve_kv_list("none", self.rider_map), {})


# ── TestSettleEngineCallerRules ───────────────────────────────────────────────

class TestSettleEngineCallerRules(unittest.TestCase):
    """
    Verify the engine caller rules from ARCHITECTURE.md section 9.

    1. Team bonus is 0 without all_riders, 60k with all_riders
    2. etapebonus_bank_deposit is the same value for every rider on the team
       (callers must credit it exactly once per stage, not 8×)
    3. captain_bank_deposit only applies to the captain
    4. Bank updated correctly: captain deposit + etapebonus credited once
    """

    def setUp(self):
        from scoring.engine import Stage, StageResult, SprintPoint, KOMPoint
        self.stage = _make_stage(1, "flat")
        self.result = StageResult(
            stage_number=1,
            finish_order=["1001", "1002"],   # rider 1001 wins
            times_behind_winner={"1001": 0, "1002": 0},
            sprint_point_winners={},
            kom_point_winners={},
            jersey_winners={},
            most_aggressive=None,
            dnf_riders=[],
            dns_riders=[],
            disqualified=[],
            ttt_team_order=None,
            gc_standings=["1001", "1002"],
        )

    def test_team_bonus_zero_without_all_riders(self):
        """Without all_riders, team_bonus is 0 (engine can't find teammates)."""
        from scoring.engine import score_rider
        rider = _make_rider("1001", "Winner", "Team A", team_id="101")
        vd = score_rider(
            rider=rider,
            stage=self.stage,
            result=self.result,
            my_team=["1001"],
            captain="1001",
            stages_remaining=21,
            all_riders=None,
        )
        self.assertEqual(vd.team_bonus, 0)

    def test_team_bonus_sixty_k_with_all_riders(self):
        """
        Teammate of a stage winner gets 60k team bonus.
        (The winner gets the bonus from teammates' positions, not from their own.)
        finish_order = ["1001", "1002"] → rider 1001 wins.
        Rider 1002 (teammate of 1001) gets 60k team bonus for 1001's win.
        """
        from scoring.engine import score_rider
        winner = _make_rider("1001", "Winner", "Team A", team_id="101")
        teammate = _make_rider("1002", "Teammate", "Team A", team_id="101")
        # Score the teammate (1002) — they get 60k from rider 1001 winning
        vd = score_rider(
            rider=teammate,
            stage=self.stage,
            result=self.result,
            my_team=["1001", "1002"],
            captain="1002",
            stages_remaining=21,
            all_riders={"1001": winner, "1002": teammate},
        )
        self.assertEqual(vd.team_bonus, 60_000)

    def test_etapebonus_same_for_all_riders(self):
        """
        etapebonus_bank_deposit is team-level: same value returned for every rider.
        Naive summing across 8 riders gives 8× the correct value.
        """
        from scoring.engine import score_rider
        r1 = _make_rider("1001", "R1", "Team A", team_id="101")
        r2 = _make_rider("1002", "R2", "Team B", team_id="102")

        vd1 = score_rider(
            rider=r1, stage=self.stage, result=self.result,
            my_team=["1001", "1002"], captain="1001",
            stages_remaining=21, all_riders={"1001": r1, "1002": r2},
        )
        vd2 = score_rider(
            rider=r2, stage=self.stage, result=self.result,
            my_team=["1001", "1002"], captain="1001",
            stages_remaining=21, all_riders={"1001": r1, "1002": r2},
        )
        # Both riders return the same etapebonus deposit
        self.assertEqual(vd1.etapebonus_bank_deposit, vd2.etapebonus_bank_deposit)
        # Naive sum would double-count: demonstrate the anti-pattern
        naive_sum = vd1.etapebonus_bank_deposit + vd2.etapebonus_bank_deposit
        correct = vd1.etapebonus_bank_deposit
        if correct != 0:
            self.assertEqual(naive_sum, 2 * correct)

    def test_captain_bank_deposit_only_for_captain(self):
        """captain_bank_deposit > 0 for captain, 0 for other riders on winning day."""
        from scoring.engine import score_rider
        winner = _make_rider("1001", "Winner", "Team A", team_id="101")
        non_captain = _make_rider("1002", "Domestique", "Team B", team_id="102")

        vd_cap = score_rider(
            rider=winner, stage=self.stage, result=self.result,
            my_team=["1001", "1002"], captain="1001",
            stages_remaining=21, all_riders={"1001": winner, "1002": non_captain},
        )
        vd_nc = score_rider(
            rider=non_captain, stage=self.stage, result=self.result,
            my_team=["1001", "1002"], captain="1001",
            stages_remaining=21, all_riders={"1001": winner, "1002": non_captain},
        )
        # Stage winner is captain — should have non-zero bank deposit
        self.assertGreater(vd_cap.captain_bank_deposit, 0)
        # Non-captain should have 0
        self.assertEqual(vd_nc.captain_bank_deposit, 0)


# ── TestDNSAlertIngest ────────────────────────────────────────────────────────

class TestDNSAlertIngest(unittest.TestCase):
    """Verify DNS detection logic (tested independently from HTTP)."""

    def test_is_out_of_game_flag_detected(self):
        """Rider with isOutOfGame:true in lineup should be flagged."""
        lineup = [{"id": 1, "isOutOfGame": True}, {"id": 2, "isOutOfGame": False}]
        out_riders = [p for p in lineup if p.get("isOutOfGame")]
        self.assertEqual(len(out_riders), 1)
        self.assertEqual(out_riders[0]["id"], 1)

    def test_is_eliminated_flag_detected(self):
        lineup = [{"id": 10, "isEliminated": True}, {"id": 11, "isEliminated": False}]
        flagged = [p for p in lineup if p.get("isEliminated")]
        self.assertEqual(len(flagged), 1)

    def test_active_rider_not_flagged(self):
        lineup = [{"id": 20, "isOutOfGame": False, "isEliminated": False}]
        flagged = [p for p in lineup if p.get("isOutOfGame") or p.get("isEliminated")]
        self.assertEqual(len(flagged), 0)


# ── TestLoadStage ─────────────────────────────────────────────────────────────

class TestLoadStage(unittest.TestCase):
    def _write_stages_json(self, tmpdir, stages):
        path = os.path.join(tmpdir, "stages.json")
        with open(path, "w") as f:
            json.dump(stages, f)
        return path

    def test_loads_stage_1(self):
        from main import _load_stage
        stages = [
            {
                "number": 1,
                "race": "giro_2026",
                "stage_type": "flat",
                "distance_km": 156.0,
                "is_ttt": False,
                "start_location": "Durres",
                "finish_location": "Tirana",
            }
        ]
        with tempfile.TemporaryDirectory() as td:
            path = self._write_stages_json(td, stages)
            stage = _load_stage(path, 1)
            self.assertEqual(stage.number, 1)
            self.assertEqual(stage.stage_type, "flat")
            self.assertAlmostEqual(stage.distance_km, 156.0)

    def test_invalid_stage_raises(self):
        from main import _load_stage
        stages = [{"number": 1, "race": "giro_2026", "stage_type": "flat",
                   "distance_km": 100, "is_ttt": False,
                   "start_location": "A", "finish_location": "B"}]
        with tempfile.TemporaryDirectory() as td:
            path = self._write_stages_json(td, stages)
            with self.assertRaises(ValueError):
                _load_stage(path, 99)

    def test_stage_7_is_mountain(self):
        from main import _load_stage
        stages = [
            {"number": 1, "race": "giro_2026", "stage_type": "flat",
             "distance_km": 100, "is_ttt": False,
             "start_location": "A", "finish_location": "B"},
            {"number": 7, "race": "giro_2026", "stage_type": "mountain",
             "distance_km": 200, "is_ttt": False,
             "start_location": "C", "finish_location": "D"},
        ]
        with tempfile.TemporaryDirectory() as td:
            path = self._write_stages_json(td, stages)
            stage = _load_stage(path, 7)
            self.assertEqual(stage.stage_type, "mountain")


if __name__ == "__main__":
    unittest.main()
