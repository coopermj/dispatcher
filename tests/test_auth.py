"""Dispatch login detection must read the page's own login state and fail closed.

The account dropdown (Log Out / My Account) is present in the DOM for every
visitor and only *shown* via Alpine `x-show="$store.user.loaded && $store.user.valid"`,
so no static-HTML check can work (a dummy-cookie trial proved that). The page's
Alpine user store is the authoritative signal.
"""
from unittest.mock import AsyncMock, MagicMock, patch


def _auth_manager():
    with patch('modules.auth.build'):
        from modules.auth import AuthManager
        return AuthManager()


def _page(state):
    page = AsyncMock()
    page.evaluate = AsyncMock(return_value=state)
    return page


class TestIsLoggedIn:
    async def test_valid_user_store_is_logged_in(self):
        from modules.auth import is_logged_in
        assert await is_logged_in(_page({'loaded': True, 'valid': True})) is True

    async def test_loaded_but_invalid_is_logged_out(self):
        """Dummy/expired cookies: the store loads but the session is not valid."""
        from modules.auth import is_logged_in
        assert await is_logged_in(_page({'loaded': True, 'valid': False})) is False

    async def test_store_never_loads_fails_closed(self):
        from modules.auth import is_logged_in
        assert await is_logged_in(_page({'loaded': False, 'valid': False})) is False

    async def test_evaluate_error_fails_closed(self):
        from modules.auth import is_logged_in
        page = AsyncMock()
        page.evaluate = AsyncMock(side_effect=RuntimeError("Target closed"))
        assert await is_logged_in(page) is False

    async def test_non_dict_result_fails_closed(self):
        from modules.auth import is_logged_in
        assert await is_logged_in(_page(None)) is False


async def test_test_dispatch_authentication_rejects_invalid_session():
    am = _auth_manager()
    with patch('modules.auth.asyncio.sleep', new_callable=AsyncMock):
        assert await am.test_dispatch_authentication(_page({'loaded': True, 'valid': False})) is False
    assert am.authenticated_with_dispatch is False


async def test_test_dispatch_authentication_accepts_valid_session():
    am = _auth_manager()
    with patch('modules.auth.asyncio.sleep', new_callable=AsyncMock):
        assert await am.test_dispatch_authentication(_page({'loaded': True, 'valid': True})) is True
    assert am.authenticated_with_dispatch is True


async def test_non_interactive_auth_fails_closed_without_waiting_or_saving_cookies():
    """On the headless box there is no window to log into: return False at once
    and leave the cookie file alone rather than overwriting it with anonymous cookies."""
    am = _auth_manager()
    am.load_dispatch_cookies = AsyncMock(return_value=True)
    am.test_dispatch_authentication = AsyncMock(return_value=False)
    am.save_dispatch_cookies = AsyncMock()
    page, ctx = AsyncMock(), MagicMock()

    with patch('modules.auth.asyncio.sleep', new_callable=AsyncMock) as sleep:
        result = await am.authenticate_with_dispatch(page, ctx, interactive=False)

    assert result is False
    am.save_dispatch_cookies.assert_not_called()
    sleep.assert_not_called()
    assert am.authenticated_with_dispatch is False


async def test_interactive_auth_timeout_returns_false_and_keeps_cookies():
    am = _auth_manager()
    am.load_dispatch_cookies = AsyncMock(return_value=False)
    am.test_dispatch_authentication = AsyncMock(return_value=False)
    am._check_logged_in_quietly = AsyncMock(return_value=False)
    am.save_dispatch_cookies = AsyncMock()
    page, ctx = AsyncMock(), MagicMock()

    with patch('modules.auth.asyncio.sleep', new_callable=AsyncMock):
        result = await am.authenticate_with_dispatch(page, ctx, interactive=True)

    assert result is False
    am.save_dispatch_cookies.assert_not_called()


async def test_auth_exception_returns_false():
    am = _auth_manager()
    am.load_dispatch_cookies = AsyncMock(side_effect=RuntimeError("browser gone"))
    result = await am.authenticate_with_dispatch(AsyncMock(), MagicMock(), interactive=False)
    assert result is False
    assert am.authenticated_with_dispatch is False


# ---------------------------------------------------------------------------
# The Atlantic: second cookie jar so linked Atlantic pages render unpaywalled.
# Probe (2026-09-16) showed the login state is server-rendered: pages embed
# "isLoggedIn": true/false and the nav swaps Sign In (accounts.../login/) for
# My Account (accounts.../accounts/details/). Static checks are valid here.
# ---------------------------------------------------------------------------
import json
import time

LOGGED_IN_ATLANTIC = '<script>window.x={"isLoggedIn":true}</script><a href="https://accounts.theatlantic.com/accounts/details/">My Account</a>'
LOGGED_OUT_ATLANTIC = '<script>window.x={"isLoggedIn": false}</script><a href="https://accounts.theatlantic.com/login/">Sign In</a>'


class TestAtlanticLooksLoggedIn:
    def test_flag_true(self):
        from modules.auth import atlantic_looks_logged_in
        assert atlantic_looks_logged_in(LOGGED_IN_ATLANTIC) is True

    def test_flag_false(self):
        from modules.auth import atlantic_looks_logged_in
        assert atlantic_looks_logged_in(LOGGED_OUT_ATLANTIC) is False

    def test_account_details_link_alone_counts(self):
        from modules.auth import atlantic_looks_logged_in
        assert atlantic_looks_logged_in('<a href="https://accounts.theatlantic.com/accounts/details/">My Account</a>') is True

    def test_explicit_false_flag_beats_link(self):
        from modules.auth import atlantic_looks_logged_in
        html = '<script>{"isLoggedIn":false}</script><a href="https://accounts.theatlantic.com/accounts/details/">x</a>'
        assert atlantic_looks_logged_in(html) is False

    def test_no_signal_is_logged_out(self):
        from modules.auth import atlantic_looks_logged_in
        assert atlantic_looks_logged_in("<html><body>Subscribe! My account. Sign in.</body></html>") is False
        assert atlantic_looks_logged_in("") is False


def _cookie(name, domain, expires=-1):
    return {"name": name, "value": "v", "domain": domain, "path": "/", "expires": expires,
            "httpOnly": False, "secure": True, "sameSite": "Lax"}


async def test_save_atlantic_cookies_keeps_only_atlantic_domain(tmp_path):
    am = _auth_manager()
    ctx = AsyncMock()
    ctx.cookies = AsyncMock(return_value=[_cookie("a", ".theatlantic.com"), _cookie("d", ".thedispatch.com"),
                                          _cookie("b", "accounts.theatlantic.com")])
    jar = tmp_path / "atlantic_cookies.json"
    with patch('modules.auth.ATLANTIC_COOKIES_FILE', jar):
        assert await am.save_atlantic_cookies(ctx) is True
    assert sorted(c["name"] for c in json.loads(jar.read_text())) == ["a", "b"]


async def test_load_atlantic_cookies_missing_jar_is_false_and_quiet(tmp_path):
    am = _auth_manager()
    ctx = AsyncMock()
    with patch('modules.auth.ATLANTIC_COOKIES_FILE', tmp_path / "nope.json"):
        assert await am.load_atlantic_cookies(ctx) is False
    ctx.add_cookies.assert_not_called()


async def test_load_atlantic_cookies_drops_expired(tmp_path):
    """Short-lived cookies (Cloudflare __cf_bm lives ~30 min) must not poison add_cookies."""
    am = _auth_manager()
    ctx = AsyncMock()
    jar = tmp_path / "atlantic_cookies.json"
    jar.write_text(json.dumps([_cookie("live_session", ".theatlantic.com", -1),
                               _cookie("live_dated", ".theatlantic.com", time.time() + 86400),
                               _cookie("__cf_bm", ".theatlantic.com", time.time() - 60)]))
    with patch('modules.auth.ATLANTIC_COOKIES_FILE', jar):
        assert await am.load_atlantic_cookies(ctx) is True
    loaded = ctx.add_cookies.call_args[0][0]
    assert sorted(c["name"] for c in loaded) == ["live_dated", "live_session"]


async def test_test_atlantic_authentication_reads_homepage_flag():
    am = _auth_manager()
    page = AsyncMock()
    page.content = AsyncMock(return_value=LOGGED_IN_ATLANTIC)
    with patch('modules.auth.asyncio.sleep', new_callable=AsyncMock):
        assert await am.test_atlantic_authentication(page) is True
    page.goto.assert_called_once()
    page.content = AsyncMock(return_value=LOGGED_OUT_ATLANTIC)
    with patch('modules.auth.asyncio.sleep', new_callable=AsyncMock):
        assert await am.test_atlantic_authentication(page) is False


async def test_atlantic_non_interactive_fails_closed_without_saving():
    am = _auth_manager()
    am.load_atlantic_cookies = AsyncMock(return_value=True)
    am.test_atlantic_authentication = AsyncMock(return_value=False)
    am.save_atlantic_cookies = AsyncMock()
    with patch('modules.auth.asyncio.sleep', new_callable=AsyncMock) as sleep:
        assert await am.authenticate_with_atlantic(AsyncMock(), MagicMock(), interactive=False) is False
    am.save_atlantic_cookies.assert_not_called()
    sleep.assert_not_called()


async def test_atlantic_no_jar_non_interactive_does_not_touch_network():
    am = _auth_manager()
    am.load_atlantic_cookies = AsyncMock(return_value=False)
    am.test_atlantic_authentication = AsyncMock()
    page = AsyncMock()
    assert await am.authenticate_with_atlantic(page, MagicMock(), interactive=False) is False
    am.test_atlantic_authentication.assert_not_called()
    page.goto.assert_not_called()


async def test_atlantic_interactive_login_saves_jar_on_detection():
    am = _auth_manager()
    am.load_atlantic_cookies = AsyncMock(return_value=False)
    am.save_atlantic_cookies = AsyncMock(return_value=True)
    page = AsyncMock()
    page.content = AsyncMock(return_value=LOGGED_IN_ATLANTIC)  # user has completed login
    with patch('modules.auth.asyncio.sleep', new_callable=AsyncMock):
        assert await am.authenticate_with_atlantic(page, MagicMock(), interactive=True) is True
    am.save_atlantic_cookies.assert_called_once()
    assert page.goto.call_args[0][0].startswith("https://accounts.theatlantic.com/login")


async def test_atlantic_interactive_timeout_returns_false_without_saving():
    am = _auth_manager()
    am.load_atlantic_cookies = AsyncMock(return_value=False)
    am.save_atlantic_cookies = AsyncMock()
    page = AsyncMock()
    page.content = AsyncMock(return_value=LOGGED_OUT_ATLANTIC)
    with patch('modules.auth.asyncio.sleep', new_callable=AsyncMock):
        assert await am.authenticate_with_atlantic(page, MagicMock(), interactive=True) is False
    am.save_atlantic_cookies.assert_not_called()


# Shared, non-fatal session check used by both pipelines -----------------------

async def test_check_atlantic_session_records_failure_when_jar_exists_but_invalid(tmp_path):
    from modules.auth import check_atlantic_session
    am = MagicMock()
    am.authenticate_with_atlantic = AsyncMock(return_value=False)
    jar = tmp_path / "atlantic_cookies.json"; jar.write_text("[]")
    failures = []
    with patch('modules.auth.ATLANTIC_COOKIES_FILE', jar):
        await check_atlantic_session(am, AsyncMock(), MagicMock(), failures)
    assert failures and failures[0]["category"] == "auth" and "Atlantic" in failures[0]["item"]
    am.authenticate_with_atlantic.assert_called_once()
    assert am.authenticate_with_atlantic.call_args[1]["interactive"] is False


async def test_check_atlantic_session_silent_when_no_jar(tmp_path):
    """Never set up → a hint at most, no alert every night."""
    from modules.auth import check_atlantic_session
    am = MagicMock()
    am.authenticate_with_atlantic = AsyncMock()
    failures = []
    with patch('modules.auth.ATLANTIC_COOKIES_FILE', tmp_path / "nope.json"):
        await check_atlantic_session(am, AsyncMock(), MagicMock(), failures)
    assert failures == []
    am.authenticate_with_atlantic.assert_not_called()


async def test_check_atlantic_session_ok_records_nothing(tmp_path):
    from modules.auth import check_atlantic_session
    am = MagicMock()
    am.authenticate_with_atlantic = AsyncMock(return_value=True)
    jar = tmp_path / "atlantic_cookies.json"; jar.write_text("[]")
    failures = []
    with patch('modules.auth.ATLANTIC_COOKIES_FILE', jar):
        await check_atlantic_session(am, AsyncMock(), MagicMock(), failures)
    assert failures == []
