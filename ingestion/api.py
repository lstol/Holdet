"""
ingestion/api.py — Live rider data from the Holdet API.

Primary data source. Confirmed working endpoint:
  GET https://nexus-app-fantasy-fargate.holdet.dk/api/games/{game_id}/players

One call returns:
  items[]              — all riders with prices and status
  _embedded.persons{}  — rider names keyed by personId
  _embedded.teams{}    — team names and abbreviations keyed by teamId

GC position and jerseys are NOT available in this endpoint.
Those fields are set to None/[] and populated externally from state.json.

Fantasy team data is scraped from the Next.js HTML team page:
  GET https://nexus-app-fantasy-fargate.holdet.dk/da/{cartridge}/me/fantasyteams/{id}
  Data embedded in self.__next_f.push([1, "..."]) script blocks.
"""
from __future__ import annotations

import json
import logging
import warnings
from dataclasses import asdict, fields
from typing import Optional

import requests

from scoring.engine import Rider

logger = logging.getLogger(__name__)

_BASE_URL = "https://nexus-app-fantasy-fargate.holdet.dk"


def fetch_riders(game_id: str, cookie: str) -> list:
    """
    Fetch all riders for the given game from the Holdet API.

    Parameters
    ----------
    game_id : str
        Holdet game ID, e.g. "612" for Giro 2026.
    cookie : str
        Full session cookie string from a logged-in browser.
        Refresh from Chrome DevTools → Network → players request → Cookie header.

    Returns
    -------
    list[Rider]
        One Rider per item in the API response. GC position and jerseys
        are not available in this endpoint and are set to None/[].

    Raises
    ------
    PermissionError
        On HTTP 401/403 — cookie has expired.
    ConnectionError
        On network-level failures.
    """
    url = f"{_BASE_URL}/api/games/{game_id}/players"
    try:
        response = requests.get(url, headers={"Cookie": cookie}, timeout=30)
    except requests.exceptions.ConnectionError as exc:
        raise ConnectionError(
            f"Network error fetching riders from {url}: {exc}"
        ) from exc
    except requests.exceptions.Timeout as exc:
        raise ConnectionError(
            f"Request timed out fetching riders from {url}"
        ) from exc

    if response.status_code in (401, 403):
        raise PermissionError(
            "Cookie expired. Refresh from Chrome DevTools → "
            "Network → players request → Cookie header. "
            "Update HOLDET_COOKIE in .env"
        )
    response.raise_for_status()

    data = response.json()
    return _parse_players_response(data)


def _parse_players_response(data: dict) -> list:
    """
    Parse the raw /players API response into a list of Rider objects.

    Extracted as a separate function to allow testing with recorded fixtures.
    """
    embedded = data.get("_embedded", {})
    persons_raw = embedded.get("persons", {})
    teams_raw = embedded.get("teams", {})

    # API may key persons/teams by int or str; normalise to str.
    persons = {str(k): v for k, v in persons_raw.items()}
    teams = {str(k): v for k, v in teams_raw.items()}

    riders = []
    for item in data.get("items", []):
        pid = str(item.get("personId", ""))
        tid = str(item.get("teamId", ""))

        person = persons.get(pid)
        if person is None:
            logger.warning("Person id=%s not found in _embedded.persons — using 'Unknown'", pid)
            person = {}

        team = teams.get(tid)
        if team is None:
            logger.warning("Team id=%s not found in _embedded.teams — using 'Unknown'", tid)
            team = {}

        first = person.get("firstName", "")
        last = person.get("lastName", "")
        name = f"{first} {last}".strip() or "Unknown"

        points_raw = item.get("points")
        points = int(points_raw) if points_raw is not None else 0

        riders.append(Rider(
            holdet_id=str(item["id"]),
            person_id=pid,
            team_id=tid,
            name=name,
            team=team.get("name", "Unknown"),
            team_abbr=team.get("abbreviation", "???"),
            value=int(item.get("price", 0)),
            start_value=int(item.get("startPrice", 0)),
            points=points,
            status="dns" if item.get("isOut") else "active",
            gc_position=None,
            jerseys=[],
            in_my_team=False,
            is_captain=False,
        ))

    return riders


def probe_extra_endpoints(game_id: str, cookie: str) -> dict:
    """
    Probe candidate endpoints for GC standings, jersey data, and statistics.

    This is discovery work — responses are returned raw for inspection.
    Findings are documented in API_NOTES.md.

    Probed endpoints:
      /api/games/{id}/rounds
      /api/games/{id}/standings
      /api/games/{id}/statistics

    Returns
    -------
    dict
        Mapping of endpoint path → {"status": int, "data": dict|str}
        "data" is the parsed JSON body if content-type is application/json,
        else the raw text.
    """
    candidate_paths = [
        f"/api/games/{game_id}/rounds",
        f"/api/games/{game_id}/standings",
        f"/api/games/{game_id}/statistics",
    ]
    results = {}
    for path in candidate_paths:
        url = f"{_BASE_URL}{path}"
        try:
            resp = requests.get(url, headers={"Cookie": cookie}, timeout=30)
            content_type = resp.headers.get("content-type", "")
            if "application/json" in content_type:
                try:
                    body = resp.json()
                except ValueError:
                    body = resp.text
            else:
                body = resp.text
            results[path] = {"status": resp.status_code, "data": body}
        except requests.exceptions.RequestException as exc:
            results[path] = {"status": None, "data": str(exc)}

    return results


def fetch_my_team(fantasy_team_id: str, cartridge: str, cookie: str) -> dict:
    """
    Fetch current team state by scraping the Next.js HTML team page.

    There is no clean REST endpoint for team composition. The data is embedded
    in the server-rendered HTML as self.__next_f.push(...) payloads.

    Parameters
    ----------
    fantasy_team_id : str
        e.g. "6796783"
    cartridge : str
        e.g. "giro-d-italia-2026"
    cookie : str
        Full session cookie string.

    Returns
    -------
    dict with keys:
        "lineup"  : list[dict]  — each player object from initialLineup[]
        "captain" : str         — holdet_id of current captain
        "bank"    : int         — current bank balance in kr

    Raises
    ------
    PermissionError  — 401/403 or page missing initialLineup (expired cookie)
    ConnectionError  — network failure
    ValueError       — page parsed but expected keys not found
    """
    url = f"{_BASE_URL}/da/{cartridge}/me/fantasyteams/{fantasy_team_id}"
    try:
        response = requests.get(url, headers={"Cookie": cookie}, timeout=30)
    except requests.exceptions.ConnectionError as exc:
        raise ConnectionError(f"Network error fetching team page: {exc}") from exc
    except requests.exceptions.Timeout as exc:
        raise ConnectionError("Request timed out fetching team page") from exc

    if response.status_code in (401, 403):
        raise PermissionError(
            "Cookie expired. Refresh from Chrome DevTools → "
            "Network → players request → Cookie header. "
            "Update HOLDET_COOKIE in .env"
        )
    response.raise_for_status()

    return _parse_my_team_html(response.text)


def _parse_my_team_html(html: str) -> dict:
    """
    Parse initialLineup, initialCaptain, initialBank from Next.js HTML.

    The page embeds data in self.__next_f.push([1, "..."]) script blocks.
    We find the block containing 'initialLineup' and extract the JSON.

    Extracted as a separate function to allow unit testing without HTTP.
    """
    import re

    if "initialLineup" not in html:
        raise PermissionError(
            "Team page returned HTML without team data (initialLineup missing). "
            "Cookie has likely expired — refresh from Chrome DevTools → "
            "Network → players request → Cookie header. "
            "Update HOLDET_COOKIE in .env"
        )

    # Extract all self.__next_f.push([1, "..."]) payloads
    chunks = re.findall(
        r'self\.__next_f\.push\(\[1,\s*"((?:[^"\\]|\\.)*)"\]\)',
        html,
        re.DOTALL,
    )

    for chunk in chunks:
        if "initialLineup" not in chunk:
            continue
        try:
            # Unescape the JSON-encoded string
            raw = chunk.encode("utf-8").decode("unicode_escape")
        except (UnicodeDecodeError, ValueError):
            raw = chunk

        # Find the JSON object containing initialLineup
        match = re.search(r'\{"fantasyTeamId":\d+.*\}', raw, re.DOTALL)
        if not match:
            match = re.search(r'\{.*"initialLineup".*\}', raw, re.DOTALL)
        if not match:
            continue

        try:
            data = json.loads(match.group())
        except (json.JSONDecodeError, ValueError):
            continue

        lineup = data.get("initialLineup", [])
        captain_raw = data.get("initialCaptain")
        bank = data.get("initialBank", 0)

        # initialCaptain may be the holdet_id directly or an object
        if isinstance(captain_raw, dict):
            captain = str(captain_raw.get("id", ""))
        else:
            captain = str(captain_raw) if captain_raw is not None else ""

        return {
            "lineup": lineup,
            "captain": captain,
            "bank": int(bank) if bank is not None else 0,
        }

    raise ValueError(
        "Could not extract team data from page HTML. "
        "Found 'initialLineup' text but could not parse the JSON block. "
        "Check API_NOTES.md for the expected page structure."
    )


def save_riders(riders: list, path: str) -> None:
    """
    Serialise riders to JSON at the given file path.

    Uses holdet_id as the top-level key for O(1) lookups.

    Parameters
    ----------
    riders : list[Rider]
    path : str
        File path, e.g. "data/riders.json"
    """
    data = {r.holdet_id: asdict(r) for r in riders}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


def load_riders(path: str) -> list:
    """
    Deserialise riders from a JSON file written by save_riders().

    Parameters
    ----------
    path : str
        File path, e.g. "data/riders.json"

    Returns
    -------
    list[Rider]
    """
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)

    rider_fields = {f.name for f in fields(Rider)}
    riders = []
    for raw in data.values():
        # Drop keys not in dataclass (forward-compat if JSON has extra fields)
        filtered = {k: v for k, v in raw.items() if k in rider_fields}
        riders.append(Rider(**filtered))
    return riders
