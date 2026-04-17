"""
tests/test_ingestion.py — Unit tests for ingestion/api.py

All HTTP calls are mocked — no live cookie required.
The fixture tests/fixtures/players_response.json contains a recorded
sample of the real API response format.
"""
import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from ingestion.api import (
    _parse_players_response,
    fetch_riders,
    load_riders,
    probe_extra_endpoints,
    save_riders,
)
from scoring.engine import Rider


# ── Load the recorded fixture ──────────────────────────────────────────────────

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "players_response.json")

with open(FIXTURE_PATH) as _f:
    SAMPLE_RESPONSE = json.load(_f)


# ═══════════════════════════════════════════════════════════════════════════════
# TestParsePlayersResponse — unit tests on the pure parser
# ═══════════════════════════════════════════════════════════════════════════════

class TestParsePlayersResponse:
    def test_returns_list_of_rider(self):
        riders = _parse_players_response(SAMPLE_RESPONSE)
        assert isinstance(riders, list)
        assert all(isinstance(r, Rider) for r in riders)

    def test_rider_count_matches_items(self):
        riders = _parse_players_response(SAMPLE_RESPONSE)
        assert len(riders) == len(SAMPLE_RESPONSE["items"])

    def test_holdet_id_is_string(self):
        riders = _parse_players_response(SAMPLE_RESPONSE)
        for r in riders:
            assert isinstance(r.holdet_id, str) and r.holdet_id != ""

    def test_name_from_persons_embedded(self):
        riders = _parse_players_response(SAMPLE_RESPONSE)
        # First item: Jonas Vingegaard (personId=23001)
        vingegaard = next(r for r in riders if r.holdet_id == "47372")
        assert vingegaard.name == "Jonas Vingegaard"

    def test_team_from_teams_embedded(self):
        riders = _parse_players_response(SAMPLE_RESPONSE)
        vingegaard = next(r for r in riders if r.holdet_id == "47372")
        assert vingegaard.team == "Team Visma | Lease a Bike"
        assert vingegaard.team_abbr == "TVL"

    def test_value_maps_to_price(self):
        riders = _parse_players_response(SAMPLE_RESPONSE)
        vingegaard = next(r for r in riders if r.holdet_id == "47372")
        assert vingegaard.value == 17_500_000

    def test_start_value_maps_to_start_price(self):
        riders = _parse_players_response(SAMPLE_RESPONSE)
        vingegaard = next(r for r in riders if r.holdet_id == "47372")
        assert vingegaard.start_value == 17_500_000

    def test_points_parsed_correctly(self):
        # Use a synthetic item since points=0 for all riders pre-race
        data = {
            "items": [{"id": 99, "personId": 1, "teamId": 1,
                        "price": 5000000, "startPrice": 5000000,
                        "points": 250, "isOut": False}],
            "_embedded": {
                "persons": {"1": {"firstName": "Test", "lastName": "Rider"}},
                "teams": {"1": {"name": "Team A", "abbreviation": "TA"}},
            }
        }
        riders = _parse_players_response(data)
        assert riders[0].points == 250

    def test_null_points_becomes_zero(self):
        data = {
            "items": [{"id": 1, "personId": 1, "teamId": 1,
                        "price": 1000000, "startPrice": 1000000,
                        "points": None, "isOut": False}],
            "_embedded": {
                "persons": {"1": {"firstName": "Test", "lastName": "Rider"}},
                "teams": {"1": {"name": "Team A", "abbreviation": "TA"}},
            }
        }
        riders = _parse_players_response(data)
        assert riders[0].points == 0

    def test_is_out_true_sets_status_dns(self):
        # Use a synthetic item — no DNS riders pre-race in the real fixture
        data = {
            "items": [{"id": 99, "personId": 1, "teamId": 1,
                        "price": 2500000, "startPrice": 2500000,
                        "points": 0, "isOut": True}],
            "_embedded": {
                "persons": {"1": {"firstName": "Out", "lastName": "Rider"}},
                "teams": {"1": {"name": "Team A", "abbreviation": "TA"}},
            }
        }
        riders = _parse_players_response(data)
        assert riders[0].status == "dns"

    def test_is_out_false_sets_status_active(self):
        riders = _parse_players_response(SAMPLE_RESPONSE)
        vingegaard = next(r for r in riders if r.holdet_id == "47372")
        assert vingegaard.status == "active"

    def test_gc_position_is_none(self):
        riders = _parse_players_response(SAMPLE_RESPONSE)
        for r in riders:
            assert r.gc_position is None

    def test_jerseys_is_empty_list(self):
        riders = _parse_players_response(SAMPLE_RESPONSE)
        for r in riders:
            assert r.jerseys == []

    def test_in_my_team_is_false(self):
        riders = _parse_players_response(SAMPLE_RESPONSE)
        for r in riders:
            assert r.in_my_team is False

    def test_is_captain_is_false(self):
        riders = _parse_players_response(SAMPLE_RESPONSE)
        for r in riders:
            assert r.is_captain is False

    def test_missing_person_id_logs_warning_and_uses_unknown(self):
        data = {
            "items": [{"id": 99, "personId": 9999, "teamId": 5001,
                        "price": 5000000, "startPrice": 5000000,
                        "points": 0, "isOut": False}],
            "_embedded": {
                "persons": {},
                "teams": {"5001": {"name": "Team Visma | Lease a Bike", "abbreviation": "TVL"}},
            }
        }
        riders = _parse_players_response(data)
        assert riders[0].name == "Unknown"

    def test_missing_team_id_logs_warning_and_uses_unknown(self):
        data = {
            "items": [{"id": 99, "personId": 23001, "teamId": 9999,
                        "price": 5000000, "startPrice": 5000000,
                        "points": 0, "isOut": False}],
            "_embedded": {
                "persons": {"23001": {"firstName": "Jonas", "lastName": "Vingegaard"}},
                "teams": {},
            }
        }
        riders = _parse_players_response(data)
        assert riders[0].team == "Unknown"
        assert riders[0].team_abbr == "???"

    def test_integer_person_keys_normalised(self):
        """API may return int keys in _embedded — parser must normalise to str."""
        data = {
            "items": [{"id": 1, "personId": 1, "teamId": 1,
                        "price": 1000000, "startPrice": 1000000,
                        "points": 0, "isOut": False}],
            "_embedded": {
                "persons": {1: {"firstName": "Int", "lastName": "Key"}},
                "teams": {1: {"name": "Team Int", "abbreviation": "TI"}},
            }
        }
        riders = _parse_players_response(data)
        assert riders[0].name == "Int Key"
        assert riders[0].team == "Team Int"

    def test_empty_items_returns_empty_list(self):
        data = {"items": [], "_embedded": {"persons": {}, "teams": {}}}
        riders = _parse_players_response(data)
        assert riders == []

    def test_person_id_stored_on_rider(self):
        riders = _parse_players_response(SAMPLE_RESPONSE)
        vingegaard = next(r for r in riders if r.holdet_id == "47372")
        assert vingegaard.person_id == "4196"  # real personId from API

    def test_team_id_stored_on_rider(self):
        riders = _parse_players_response(SAMPLE_RESPONSE)
        vingegaard = next(r for r in riders if r.holdet_id == "47372")
        assert vingegaard.team_id == "205"  # real teamId from API


# ═══════════════════════════════════════════════════════════════════════════════
# TestFetchRiders — HTTP layer (mocked)
# ═══════════════════════════════════════════════════════════════════════════════

class TestFetchRiders:
    def _mock_response(self, status_code=200, json_data=None):
        mock = MagicMock()
        mock.status_code = status_code
        mock.json.return_value = json_data or SAMPLE_RESPONSE
        mock.headers = {"content-type": "application/json"}
        mock.raise_for_status = MagicMock()
        return mock

    def test_returns_list_of_rider(self):
        with patch("requests.get", return_value=self._mock_response()) as _:
            riders = fetch_riders("612", "session=abc123")
        assert isinstance(riders, list)
        assert all(isinstance(r, Rider) for r in riders)

    def test_correct_url_called(self):
        with patch("requests.get", return_value=self._mock_response()) as mock_get:
            fetch_riders("612", "session=abc123")
        call_url = mock_get.call_args[0][0]
        assert "api/games/612/players" in call_url

    def test_cookie_sent_in_header(self):
        with patch("requests.get", return_value=self._mock_response()) as mock_get:
            fetch_riders("612", "session=abc123")
        headers = mock_get.call_args[1]["headers"]
        assert headers["Cookie"] == "session=abc123"

    def test_401_raises_permission_error(self):
        mock = self._mock_response(status_code=401)
        mock.raise_for_status = MagicMock()
        with patch("requests.get", return_value=mock):
            with pytest.raises(PermissionError) as exc_info:
                fetch_riders("612", "expired_cookie")
        assert "Cookie expired" in str(exc_info.value)
        assert "DevTools" in str(exc_info.value)

    def test_403_raises_permission_error(self):
        mock = self._mock_response(status_code=403)
        mock.raise_for_status = MagicMock()
        with patch("requests.get", return_value=mock):
            with pytest.raises(PermissionError):
                fetch_riders("612", "bad_cookie")

    def test_network_error_raises_connection_error(self):
        import requests as req
        with patch("requests.get", side_effect=req.exceptions.ConnectionError("timeout")):
            with pytest.raises(ConnectionError):
                fetch_riders("612", "session=abc")

    def test_count_matches_fixture(self):
        with patch("requests.get", return_value=self._mock_response()):
            riders = fetch_riders("612", "session=abc123")
        assert len(riders) == len(SAMPLE_RESPONSE["items"])


# ═══════════════════════════════════════════════════════════════════════════════
# TestSaveLoadRoundTrip
# ═══════════════════════════════════════════════════════════════════════════════

class TestSaveLoadRoundTrip:
    def _sample_riders(self):
        return _parse_players_response(SAMPLE_RESPONSE)

    def test_round_trip_preserves_count(self):
        riders = self._sample_riders()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            save_riders(riders, path)
            loaded = load_riders(path)
            assert len(loaded) == len(riders)
        finally:
            os.unlink(path)

    def test_round_trip_preserves_holdet_id(self):
        riders = self._sample_riders()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            save_riders(riders, path)
            loaded = load_riders(path)
            original_ids = {r.holdet_id for r in riders}
            loaded_ids = {r.holdet_id for r in loaded}
            assert original_ids == loaded_ids
        finally:
            os.unlink(path)

    def test_round_trip_preserves_name(self):
        riders = self._sample_riders()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            save_riders(riders, path)
            loaded = load_riders(path)
            loaded_map = {r.holdet_id: r for r in loaded}
            for r in riders:
                assert loaded_map[r.holdet_id].name == r.name
        finally:
            os.unlink(path)

    def test_round_trip_preserves_value(self):
        riders = self._sample_riders()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            save_riders(riders, path)
            loaded = load_riders(path)
            loaded_map = {r.holdet_id: r for r in loaded}
            for r in riders:
                assert loaded_map[r.holdet_id].value == r.value
        finally:
            os.unlink(path)

    def test_round_trip_preserves_status(self):
        riders = self._sample_riders()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            save_riders(riders, path)
            loaded = load_riders(path)
            loaded_map = {r.holdet_id: r for r in loaded}
            for r in riders:
                assert loaded_map[r.holdet_id].status == r.status
        finally:
            os.unlink(path)

    def test_save_file_uses_holdet_id_as_key(self):
        riders = self._sample_riders()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            save_riders(riders, path)
            with open(path) as fh:
                data = json.load(fh)
            for r in riders:
                assert r.holdet_id in data
        finally:
            os.unlink(path)

    def test_load_returns_rider_objects(self):
        riders = self._sample_riders()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            save_riders(riders, path)
            loaded = load_riders(path)
            assert all(isinstance(r, Rider) for r in loaded)
        finally:
            os.unlink(path)


# ═══════════════════════════════════════════════════════════════════════════════
# TestProbeExtraEndpoints
# ═══════════════════════════════════════════════════════════════════════════════

class TestProbeExtraEndpoints:
    def _mock_resp(self, status=200, body=None):
        mock = MagicMock()
        mock.status_code = status
        mock.headers = {"content-type": "application/json"}
        mock.json.return_value = body or {"items": []}
        return mock

    def test_returns_dict(self):
        with patch("requests.get", return_value=self._mock_resp()):
            result = probe_extra_endpoints("612", "session=abc")
        assert isinstance(result, dict)

    def test_keys_are_endpoint_paths(self):
        with patch("requests.get", return_value=self._mock_resp()):
            result = probe_extra_endpoints("612", "session=abc")
        for path in ["/api/games/612/rounds", "/api/games/612/standings", "/api/games/612/statistics"]:
            assert path in result

    def test_each_result_has_status_and_data(self):
        with patch("requests.get", return_value=self._mock_resp()):
            result = probe_extra_endpoints("612", "session=abc")
        for path, val in result.items():
            assert "status" in val
            assert "data" in val

    def test_status_code_recorded(self):
        with patch("requests.get", return_value=self._mock_resp(status=404)):
            result = probe_extra_endpoints("612", "session=abc")
        for val in result.values():
            assert val["status"] == 404

    def test_network_failure_recorded(self):
        import requests as req
        with patch("requests.get", side_effect=req.exceptions.ConnectionError("down")):
            result = probe_extra_endpoints("612", "session=abc")
        for val in result.values():
            assert val["status"] is None
            assert isinstance(val["data"], str)
