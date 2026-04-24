"""
ingestion/api.py — Live rider data from the Holdet API.

Primary data source. Confirmed working endpoint:
  GET https://nexus-app-fantasy-fargate.holdet.dk/api/games/{game_id}/players

Authentication: email/password via NextAuth credentials provider.
  1. GET  https://www.holdet.dk/api/auth/csrf          → csrfToken
  2. POST https://www.holdet.dk/api/auth/signin/credentials → session cookies
  3. GET  https://nexus-app-fantasy-fargate.holdet.dk/api/games/612/players → confirm valid (200 = ok, 401/403 = failed)
  On 401: re-authenticates automatically and retries once.

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
from dataclasses import asdict, fields
from typing import Optional

import requests

from scoring.engine import Rider

logger = logging.getLogger(__name__)

_HOLDET_URL = "https://www.holdet.dk"
_BASE_URL = "https://nexus-app-fantasy-fargate.holdet.dk"
_SESSION_CONFIRM_URL = f"{_BASE_URL}/api/games/612/players"

# Module-level session cache — call _reset_session() to force re-authentication.
_cached_session: Optional[requests.Session] = None


# ── Authentication ─────────────────────────────────────────────────────────────

def login(email: str, password: str) -> requests.Session:
    """
    Authenticate with Holdet using email/password (NextAuth credentials).

    Step 1: GET /api/auth/csrf → csrfToken
    Step 2: POST /api/auth/signin/credentials with {email, password, csrfToken}
    Step 3: GET /api/session to confirm the session is valid

    Returns
    -------
    requests.Session
        Session with auth cookies set. Reuse for all subsequent calls.

    Raises
    ------
    PermissionError
        On 401/403 — wrong email or password, or confirmation failed.
    ConnectionError
        On network failures.
    """
    session = requests.Session()

    # Step 1 — CSRF token
    try:
        csrf_resp = session.get(f"{_HOLDET_URL}/api/auth/csrf", timeout=30)
    except requests.exceptions.ConnectionError as exc:
        raise ConnectionError(f"Network error fetching CSRF token: {exc}") from exc
    except requests.exceptions.Timeout:
        raise ConnectionError("Request timed out fetching CSRF token")
    csrf_resp.raise_for_status()
    csrf_token = csrf_resp.json()["csrfToken"]

    # Step 2 — Sign in
    try:
        signin_resp = session.post(
            f"{_HOLDET_URL}/api/auth/signin/credentials",
            json={
                "email": email,
                "password": password,
                "csrfToken": csrf_token,
                "redirect": False,
            },
            timeout=30,
        )
    except requests.exceptions.ConnectionError as exc:
        raise ConnectionError(f"Network error during sign-in: {exc}") from exc
    except requests.exceptions.Timeout:
        raise ConnectionError("Request timed out during sign-in")

    if signin_resp.status_code in (401, 403):
        raise PermissionError(
            "Holdet login failed — wrong email or password. "
            "Check HOLDET_EMAIL and HOLDET_PASSWORD in .env"
        )
    signin_resp.raise_for_status()

    # Step 3 — Confirm session is valid
    try:
        confirm_resp = session.get(_SESSION_CONFIRM_URL, timeout=30)
    except requests.exceptions.ConnectionError as exc:
        raise ConnectionError(f"Network error confirming session: {exc}") from exc
    except requests.exceptions.Timeout:
        raise ConnectionError("Request timed out confirming session")

    if confirm_resp.status_code in (401, 403):
        raise PermissionError(
            "Login appeared to succeed but session confirmation failed. "
            "Check HOLDET_EMAIL and HOLDET_PASSWORD in .env"
        )
    confirm_resp.raise_for_status()

    return session


def get_session() -> requests.Session:
    """
    Return the cached authenticated session, creating one if needed.

    Credentials from HOLDET_EMAIL and HOLDET_PASSWORD environment variables.
    Call _reset_session() to force re-authentication on the next call.

    Raises
    ------
    PermissionError
        If login fails (wrong credentials).
    EnvironmentError
        If HOLDET_EMAIL or HOLDET_PASSWORD are not set.
    """
    global _cached_session
    if _cached_session is None:
        import config as _config
        _cached_session = login(_config.get_email(), _config.get_password())
    return _cached_session


def _reset_session() -> None:
    """Clear the cached session so the next get_session() call re-authenticates."""
    global _cached_session
    _cached_session = None


def _get_with_retry(session: requests.Session, url: str) -> requests.Response:
    """
    Perform a GET. On 401, reset cached session, re-login, retry once.
    Two consecutive 401s raise PermissionError.
    403 raises PermissionError immediately (no retry).
    Raises ConnectionError on network failures.
    """
    try:
        resp = session.get(url, timeout=30)
    except requests.exceptions.ConnectionError as exc:
        raise ConnectionError(f"Network error fetching {url}: {exc}") from exc
    except requests.exceptions.Timeout:
        raise ConnectionError(f"Request timed out fetching {url}")

    if resp.status_code == 401:
        # Re-authenticate once and retry
        _reset_session()
        retry_session = get_session()
        try:
            resp = retry_session.get(url, timeout=30)
        except requests.exceptions.ConnectionError as exc:
            raise ConnectionError(f"Network error on retry: {exc}") from exc
        if resp.status_code == 401:
            raise PermissionError(
                "Authentication failed after re-login attempt. "
                "Check HOLDET_EMAIL and HOLDET_PASSWORD in .env"
            )

    if resp.status_code == 403:
        raise PermissionError(
            "Access denied (403). "
            "Check HOLDET_EMAIL and HOLDET_PASSWORD in .env"
        )

    resp.raise_for_status()
    return resp


# ── Rider data ─────────────────────────────────────────────────────────────────

def fetch_riders(game_id: str, session: Optional[requests.Session] = None) -> list:
    """
    Fetch all riders for the given game from the Holdet API.

    Parameters
    ----------
    game_id : str
        Holdet game ID, e.g. "612" for Giro 2026.
    session : requests.Session, optional
        Authenticated session. If None, uses get_session() (auto-login).
        Pass an explicit session in tests to inject a mock.

    Returns
    -------
    list[Rider]
        One Rider per item in the API response. GC position and jerseys
        are not available in this endpoint and are set to None/[].

    Raises
    ------
    PermissionError
        On 401 (after retry) or 403 — authentication failed.
    ConnectionError
        On network-level failures.
    """
    if session is None:
        session = get_session()
    url = f"{_BASE_URL}/api/games/{game_id}/players"
    response = _get_with_retry(session, url)
    return _parse_players_response(response.json())


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


def fetch_my_team(fantasy_team_id: str, cartridge: str,
                  session: Optional[requests.Session] = None) -> dict:
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
    session : requests.Session, optional
        Authenticated session. If None, uses get_session() (auto-login).

    Returns
    -------
    dict with keys:
        "lineup"  : list[dict]  — each player object from initialLineup[]
        "captain" : str         — holdet_id of current captain
        "bank"    : int         — current bank balance in kr

    Raises
    ------
    PermissionError  — 401/403 or page missing initialLineup (session expired)
    ConnectionError  — network failure
    ValueError       — page parsed but expected keys not found
    """
    if session is None:
        session = get_session()
    url = f"{_BASE_URL}/da/{cartridge}/me/fantasyteams/{fantasy_team_id}"
    response = _get_with_retry(session, url)
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
            "Session may have expired — will re-authenticate on next request."
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


def normalise_ownership(riders: list) -> tuple:
    """
    Detect and normalise ownership/popularity values on a list of Rider-like objects.

    The Holdet API may return popularity in 0–100 scale (integers) or 0–1 scale
    (floats). This function detects which scale is in use and normalises to 0–1
    in-place if necessary.

    Parameters
    ----------
    riders : list
        Riders with an optional `popularity` attribute.

    Returns
    -------
    (riders, was_normalised, max_val)
        riders         — same list, popularity values updated if normalised
        was_normalised — True if values were divided by 100
        max_val        — max popularity seen before normalisation (None if no values)
    """
    values = [
        getattr(r, "popularity", None)
        for r in riders
        if getattr(r, "popularity", None) is not None
    ]
    if not values:
        return riders, False, None

    max_val = max(values)
    if max_val > 1.5:
        for r in riders:
            pop = getattr(r, "popularity", None)
            if pop is not None:
                object.__setattr__(r, "popularity", pop / 100.0) if hasattr(r, "__dataclass_fields__") \
                    else setattr(r, "popularity", pop / 100.0)
        return riders, True, max_val

    return riders, False, max_val
