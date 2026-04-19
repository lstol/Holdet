"""
tests/test_autologin.py — Tests for auto-login, session caching, and retry logic.

Covers:
  - login(email, password) in ingestion/api.py:
      CSRF fetch → credentials POST → session confirmation GET
  - get_session() caching and _reset_session() invalidation
  - fetch_riders() auto-retry on 401, with session refresh

All HTTP is mocked — no live API calls. The implementation in ingestion/api.py
does not yet exist; these tests are written against the specified contract and
will pass once the implementation is added.
"""
from __future__ import annotations

import pytest
import requests
from unittest.mock import MagicMock, call, patch


# ── Fixtures and helpers ───────────────────────────────────────────────────────

def _make_response(status_code=200, json_data=None):
    """Return a mock requests.Response with given status and JSON body."""
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = json_data or {}
    mock.raise_for_status = MagicMock()
    if status_code >= 400:
        mock.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=mock
        )
    return mock


def _make_mock_session(get_responses=None, post_responses=None):
    """
    Build a mock requests.Session whose .get() and .post() return
    successive responses from the provided lists.
    """
    session = MagicMock(spec=requests.Session)

    if get_responses:
        session.get.side_effect = get_responses
    else:
        session.get.return_value = _make_response(200, {})

    if post_responses:
        session.post.side_effect = post_responses
    else:
        session.post.return_value = _make_response(200, {})

    return session


# ── Reset cached session before every test in TestGetSession ──────────────────

@pytest.fixture(autouse=False)
def reset_cached_session():
    """Force ingestion.api._cached_session = None before each test that uses it."""
    import ingestion.api as api_module
    api_module._cached_session = None
    yield
    api_module._cached_session = None


# ═══════════════════════════════════════════════════════════════════════════════
# TestLogin
# ═══════════════════════════════════════════════════════════════════════════════

class TestLogin:

    def test_login_returns_session_on_success(self):
        """login() returns a requests.Session when all three steps succeed."""
        from ingestion.api import login

        csrf_response = _make_response(200, {"csrfToken": "tok123"})
        signin_response = _make_response(200, {})
        confirm_response = _make_response(200, {"user": "test@example.com"})

        mock_session = _make_mock_session(
            get_responses=[csrf_response, confirm_response],
            post_responses=[signin_response],
        )

        with patch("requests.Session", return_value=mock_session):
            result = login("test@example.com", "s3cret")

        assert result is mock_session

    def test_login_raises_permission_error_on_wrong_password(self):
        """login() raises PermissionError when credentials POST returns 401."""
        from ingestion.api import login

        csrf_response = _make_response(200, {"csrfToken": "tok123"})
        signin_response = _make_response(401, {})

        mock_session = _make_mock_session(
            get_responses=[csrf_response],
            post_responses=[signin_response],
        )

        with patch("requests.Session", return_value=mock_session):
            with pytest.raises(PermissionError):
                login("test@example.com", "wrong_password")

    def test_login_sends_csrf_token_in_post_body(self):
        """login() includes the csrfToken extracted from step 1 in the POST body."""
        from ingestion.api import login

        csrf_response = _make_response(200, {"csrfToken": "abc-csrf-xyz"})
        signin_response = _make_response(200, {})
        confirm_response = _make_response(200, {"user": "test@example.com"})

        mock_session = _make_mock_session(
            get_responses=[csrf_response, confirm_response],
            post_responses=[signin_response],
        )

        with patch("requests.Session", return_value=mock_session):
            login("test@example.com", "s3cret")

        post_call_kwargs = mock_session.post.call_args
        # The CSRF token must appear in the POST body (json or data kwarg)
        call_json = post_call_kwargs[1].get("json") or post_call_kwargs[1].get("data")
        assert call_json is not None, "POST body should be provided as json= or data="
        assert call_json.get("csrfToken") == "abc-csrf-xyz"

    def test_login_raises_on_session_confirmation_failure(self):
        """login() raises PermissionError when session confirmation GET returns 401."""
        from ingestion.api import login

        csrf_response = _make_response(200, {"csrfToken": "tok123"})
        signin_response = _make_response(200, {})
        confirm_response = _make_response(401, {})

        mock_session = _make_mock_session(
            get_responses=[csrf_response, confirm_response],
            post_responses=[signin_response],
        )

        with patch("requests.Session", return_value=mock_session):
            with pytest.raises(PermissionError):
                login("test@example.com", "s3cret")

    def test_login_raises_connection_error_on_network_failure(self):
        """login() raises ConnectionError when a network-level exception occurs."""
        from ingestion.api import login

        mock_session = MagicMock(spec=requests.Session)
        mock_session.get.side_effect = requests.exceptions.ConnectionError("unreachable")

        with patch("requests.Session", return_value=mock_session):
            with pytest.raises(ConnectionError):
                login("test@example.com", "s3cret")


# ═══════════════════════════════════════════════════════════════════════════════
# TestGetSession
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetSession:

    def test_get_session_calls_login_when_no_cached_session(self, reset_cached_session):
        """get_session() calls login() with config credentials on first invocation."""
        from ingestion.api import get_session

        mock_sess = MagicMock(spec=requests.Session)

        with patch("ingestion.api.login", return_value=mock_sess) as mock_login, \
             patch("config.get_email", return_value="user@example.com"), \
             patch("config.get_password", return_value="pass123"):
            result = get_session()

        mock_login.assert_called_once_with("user@example.com", "pass123")
        assert result is mock_sess

    def test_get_session_returns_cached_session_on_second_call(self, reset_cached_session):
        """get_session() returns the same session on subsequent calls without re-logging in."""
        from ingestion.api import get_session

        mock_sess = MagicMock(spec=requests.Session)

        with patch("ingestion.api.login", return_value=mock_sess) as mock_login, \
             patch("config.get_email", return_value="user@example.com"), \
             patch("config.get_password", return_value="pass123"):
            first = get_session()
            second = get_session()

        assert first is second
        assert mock_login.call_count == 1

    def test_reset_session_forces_relogin_on_next_call(self, reset_cached_session):
        """After _reset_session(), get_session() calls login() again."""
        from ingestion.api import get_session, _reset_session

        first_sess = MagicMock(spec=requests.Session)
        second_sess = MagicMock(spec=requests.Session)

        with patch("ingestion.api.login", side_effect=[first_sess, second_sess]) as mock_login, \
             patch("config.get_email", return_value="user@example.com"), \
             patch("config.get_password", return_value="pass123"):
            s1 = get_session()
            _reset_session()
            s2 = get_session()

        assert s1 is first_sess
        assert s2 is second_sess
        assert mock_login.call_count == 2


# ═══════════════════════════════════════════════════════════════════════════════
# TestAutoRetry
# ═══════════════════════════════════════════════════════════════════════════════

class TestAutoRetry:
    """
    fetch_riders() auto-retry logic when passed a session= that returns 401.

    The new signature accepted by the implementation is:
        fetch_riders(game_id, session=<requests.Session>)

    On 401 the function calls _reset_session() then get_session() to obtain a
    fresh session and retries once. A second 401 raises PermissionError.
    403 never retries. Network errors raise ConnectionError.
    """

    def _make_riders_response(self):
        """Return a minimal valid players API response."""
        return {
            "items": [
                {
                    "id": 1001,
                    "personId": 1,
                    "teamId": 1,
                    "price": 5_000_000,
                    "startPrice": 5_000_000,
                    "points": 0,
                    "isOut": False,
                }
            ],
            "_embedded": {
                "persons": {"1": {"firstName": "Test", "lastName": "Rider"}},
                "teams": {"1": {"name": "Team A", "abbreviation": "TA"}},
            },
        }

    def test_401_triggers_retry_and_succeeds(self):
        """On first 401, fetch_riders resets session and retries; second attempt succeeds."""
        from ingestion.api import fetch_riders

        # First session returns 401
        first_session = MagicMock(spec=requests.Session)
        first_response = _make_response(401)
        first_session.get.return_value = first_response

        # Second session (obtained after re-login) returns 200
        second_session = MagicMock(spec=requests.Session)
        success_response = _make_response(200, self._make_riders_response())
        second_session.get.return_value = success_response

        with patch("ingestion.api._reset_session") as mock_reset, \
             patch("ingestion.api.get_session", return_value=second_session):
            result = fetch_riders("612", session=first_session)

        mock_reset.assert_called_once()
        assert isinstance(result, list)

    def test_two_consecutive_401s_raises_permission_error(self):
        """If retry also returns 401, fetch_riders raises PermissionError."""
        from ingestion.api import fetch_riders

        first_session = MagicMock(spec=requests.Session)
        first_session.get.return_value = _make_response(401)

        second_session = MagicMock(spec=requests.Session)
        second_session.get.return_value = _make_response(401)

        with patch("ingestion.api._reset_session"), \
             patch("ingestion.api.get_session", return_value=second_session):
            with pytest.raises(PermissionError, match="Authentication failed after re-login"):
                fetch_riders("612", session=first_session)

    def test_403_raises_permission_error_without_retry(self):
        """403 raises PermissionError immediately — no retry attempted."""
        from ingestion.api import fetch_riders

        first_session = MagicMock(spec=requests.Session)
        first_session.get.return_value = _make_response(403)

        with patch("ingestion.api._reset_session") as mock_reset, \
             patch("ingestion.api.get_session") as mock_get_session:
            with pytest.raises(PermissionError):
                fetch_riders("612", session=first_session)

        mock_reset.assert_not_called()
        mock_get_session.assert_not_called()

    def test_network_error_raises_connection_error(self):
        """A network-level exception while fetching riders raises ConnectionError."""
        from ingestion.api import fetch_riders

        session = MagicMock(spec=requests.Session)
        session.get.side_effect = requests.exceptions.ConnectionError("network down")

        with pytest.raises(ConnectionError):
            fetch_riders("612", session=session)
