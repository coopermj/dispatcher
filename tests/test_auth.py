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
# Cookie jar helpers (_save_cookies / _load_cookies), exercised via the
# Dispatch jar — the only jar left after The Atlantic was dropped.
# ---------------------------------------------------------------------------
import json
import time


def _cookie(name, domain, expires=-1):
    return {"name": name, "value": "v", "domain": domain, "path": "/", "expires": expires,
            "httpOnly": False, "secure": True, "sameSite": "Lax"}


async def test_save_dispatch_cookies_keeps_only_dispatch_domain(tmp_path):
    am = _auth_manager()
    ctx = AsyncMock()
    ctx.cookies = AsyncMock(return_value=[_cookie("a", ".thedispatch.com"), _cookie("x", ".google.com"),
                                          _cookie("b", "www.thedispatch.com")])
    jar = tmp_path / "dispatch_cookies.json"
    with patch('modules.auth.COOKIES_FILE', jar):
        assert await am.save_dispatch_cookies(ctx) is True
    assert sorted(c["name"] for c in json.loads(jar.read_text())) == ["a", "b"]


async def test_load_dispatch_cookies_missing_jar_is_false_and_quiet(tmp_path):
    am = _auth_manager()
    ctx = AsyncMock()
    with patch('modules.auth.COOKIES_FILE', tmp_path / "nope.json"):
        assert await am.load_dispatch_cookies(ctx) is False
    ctx.add_cookies.assert_not_called()


async def test_load_dispatch_cookies_drops_expired(tmp_path):
    """Short-lived cookies (Cloudflare __cf_bm lives ~30 min) must not poison add_cookies."""
    am = _auth_manager()
    ctx = AsyncMock()
    jar = tmp_path / "dispatch_cookies.json"
    jar.write_text(json.dumps([_cookie("live_session", ".thedispatch.com", -1),
                               _cookie("live_dated", ".thedispatch.com", time.time() + 86400),
                               _cookie("__cf_bm", ".thedispatch.com", time.time() - 60)]))
    with patch('modules.auth.COOKIES_FILE', jar):
        assert await am.load_dispatch_cookies(ctx) is True
    loaded = ctx.add_cookies.call_args[0][0]
    assert sorted(c["name"] for c in loaded) == ["live_dated", "live_session"]
