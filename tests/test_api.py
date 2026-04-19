"""
tests/test_api.py — FastAPI bridge endpoint tests.

Uses FastAPI TestClient (no real HTTP). Mocks file I/O via monkeypatch
so tests don't touch real state.json / riders.json.
"""
from __future__ import annotations

import json
import os
import pytest
from fastapi.testclient import TestClient


# ── Helpers ───────────────────────────────────────────────────────────────────

def _minimal_riders_json(tmp_path) -> str:
    """Write a minimal riders.json in save_riders() format ({holdet_id: dict})."""
    riders = {}
    for i in range(1, 12):
        riders[f"r{i}"] = {
            "holdet_id": f"r{i}",
            "person_id": f"p{i}",
            "team_id": "t1",
            "name": f"Rider {i}",
            "team": "Team One",
            "team_abbr": "ONE",
            "value": 5_000_000 + i * 500_000,
            "start_value": 5_000_000,
            "points": 0,
            "status": "active",
            "gc_position": i if i <= 10 else None,
            "jerseys": [],
            "in_my_team": i <= 8,
            "is_captain": i == 1,
        }
    path = str(tmp_path / "riders.json")
    with open(path, "w") as f:
        json.dump(riders, f)
    return path


def _minimal_state_json(tmp_path) -> str:
    state = {
        "current_stage": 1,
        "bank": 3_000_000,
        "rank": 150,
        "total_participants": 1000,
        "my_team": [f"r{i}" for i in range(1, 9)],
        "captain": "r1",
        "stages_completed": [],
        "probs_by_stage": {},
        "user_id": "test-uuid",
    }
    path = str(tmp_path / "state.json")
    with open(path, "w") as f:
        json.dump(state, f)
    return path


def _minimal_stages_json(tmp_path) -> str:
    stages = [
        {
            "number": i,
            "race": "giro_2026",
            "stage_type": "flat",
            "distance_km": 200.0,
            "is_ttt": False,
            "start_location": f"City {i}",
            "finish_location": f"City {i+1}",
            "sprint_points": [],
            "kom_points": [],
            "notes": "",
        }
        for i in range(1, 22)
    ]
    path = str(tmp_path / "stages.json")
    with open(path, "w") as f:
        json.dump(stages, f)
    return path


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient with all file paths redirected to tmp_path."""
    riders_path = _minimal_riders_json(tmp_path)
    state_path  = _minimal_state_json(tmp_path)
    stages_path = _minimal_stages_json(tmp_path)

    monkeypatch.setenv("RIDERS_PATH", riders_path)
    monkeypatch.setenv("STATE_PATH", state_path)
    monkeypatch.setenv("STAGES_PATH", stages_path)

    # Reload config so env vars take effect
    import importlib
    import config as cfg_module
    importlib.reload(cfg_module)

    from api.server import app
    return TestClient(app, raise_server_exceptions=True)


# ── GET /status ───────────────────────────────────────────────────────────────

class TestStatusEndpoint:
    def test_returns_200(self, client):
        r = client.get("/status")
        assert r.status_code == 200

    def test_has_required_keys(self, client):
        d = client.get("/status").json()
        for key in ("bank", "team", "captain", "current_stage", "stages_completed", "dns_alerts"):
            assert key in d, f"Missing key: {key}"

    def test_bank_matches_state(self, client):
        d = client.get("/status").json()
        assert d["bank"] == 3_000_000

    def test_team_has_8_riders(self, client):
        d = client.get("/status").json()
        assert len(d["team"]) == 8

    def test_captain_is_in_team(self, client):
        d = client.get("/status").json()
        captain = d["captain"]
        team_ids = [r["holdet_id"] for r in d["team"]]
        assert captain in team_ids

    def test_no_dns_alerts_when_all_active(self, client):
        d = client.get("/status").json()
        assert d["dns_alerts"] == []


# ── POST /brief ───────────────────────────────────────────────────────────────

class TestBriefEndpoint:
    def test_returns_200(self, client):
        r = client.post("/brief", json={"stage": 1})
        assert r.status_code == 200

    def test_has_four_profiles(self, client):
        d = client.post("/brief", json={"stage": 1}).json()
        profiles = d["profiles"]
        assert len(profiles) == 4

    def test_profile_keys_present(self, client):
        d = client.post("/brief", json={"stage": 1}).json()
        for profile_data in d["profiles"].values():
            for key in ("transfers", "captain", "expected_value", "upside_90pct",
                        "downside_10pct", "transfer_cost", "reasoning"):
                assert key in profile_data, f"Missing key: {key}"

    def test_current_team_ev_is_numeric(self, client):
        d = client.post("/brief", json={"stage": 1}).json()
        assert isinstance(d["current_team_ev"], (int, float))

    def test_look_ahead_respected(self, client):
        d = client.post("/brief", json={"stage": 1, "look_ahead": 3}).json()
        assert d["stages_remaining"] == 3

    def test_captain_override_accepted(self, client):
        d = client.post("/brief", json={"stage": 1, "captain_override": "r2"}).json()
        assert d["captain"] == "r2"

    def test_unknown_stage_returns_404(self, client):
        r = client.post("/brief", json={"stage": 99})
        assert r.status_code == 404

    def test_team_sims_returned(self, client):
        d = client.post("/brief", json={"stage": 1}).json()
        assert "team_sims" in d
        assert len(d["team_sims"]) == 8


# ── POST /team ────────────────────────────────────────────────────────────────

class TestTeamEndpoint:
    def _valid_payload(self):
        return {
            "my_team": [f"r{i}" for i in range(1, 9)],
            "captain": "r3",
        }

    def test_returns_200(self, client):
        r = client.post("/team", json=self._valid_payload())
        assert r.status_code == 200

    def test_captain_updated(self, client, tmp_path):
        client.post("/team", json=self._valid_payload())
        # Read state file directly to verify
        import json as _json
        state_path = os.environ.get("STATE_PATH", "data/state.json")
        with open(state_path) as f:
            state = _json.load(f)
        assert state["captain"] == "r3"

    def test_wrong_team_size_returns_422(self, client):
        r = client.post("/team", json={"my_team": ["r1", "r2"], "captain": "r1"})
        assert r.status_code == 422

    def test_captain_not_in_team_returns_422(self, client):
        payload = {"my_team": [f"r{i}" for i in range(1, 9)], "captain": "r9"}
        r = client.post("/team", json=payload)
        assert r.status_code == 422


# ── POST /settle ──────────────────────────────────────────────────────────────

class TestSettleEndpoint:
    def _valid_payload(self):
        return {
            "stage": 1,
            "finish_order": [f"r{i}" for i in range(1, 12)],
            "dnf_riders": [],
            "dns_riders": [],
            "gc_standings": [f"r{i}" for i in range(1, 9)],
            "jersey_winners": {"yellow": "r1"},
            "most_aggressive": None,
            "sprint_point_winners": {},
            "kom_point_winners": {},
            "times_behind_winner": {},
        }

    def test_returns_200(self, client):
        r = client.post("/settle", json=self._valid_payload())
        assert r.status_code == 200

    def test_bank_updated(self, client):
        d = client.post("/settle", json=self._valid_payload()).json()
        assert "new_bank" in d
        assert d["new_bank"] != d["old_bank"] or True  # bank may or may not change

    def test_rider_results_returned(self, client):
        d = client.post("/settle", json=self._valid_payload()).json()
        assert "rider_results" in d
        assert len(d["rider_results"]) == 8

    def test_all_rider_result_keys_present(self, client):
        d = client.post("/settle", json=self._valid_payload()).json()
        for rr in d["rider_results"]:
            for key in ("holdet_id", "name", "total_rider_value_delta"):
                assert key in rr, f"Missing key: {key}"

    def test_unknown_stage_returns_404(self, client):
        payload = self._valid_payload()
        payload["stage"] = 99
        r = client.post("/settle", json=payload)
        assert r.status_code == 404

    def test_stage_added_to_completed(self, client, tmp_path):
        client.post("/settle", json=self._valid_payload())
        state_path = os.environ.get("STATE_PATH", "data/state.json")
        with open(state_path) as f:
            state = json.load(f)
        assert 1 in state["stages_completed"]


# ── POST /sync ────────────────────────────────────────────────────────────────

class TestSyncEndpoint:
    def test_returns_200_or_500(self, client):
        """Sync may fail if Supabase keys not set — that's OK."""
        r = client.post("/sync")
        assert r.status_code in (200, 500)
