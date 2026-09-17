"""A hung Chromium made `browser.close()` never return: the 2026-08-19 17:00 cron
run had already written its failure report, then sat in close_browser_session()
for 27 days while every later run hit the collision guard. Every await in the
shutdown path must be time-bounded, and on timeout the Playwright driver
process must be killed so the interpreter can exit."""
import asyncio
from unittest.mock import MagicMock

from modules import browser_manager as bm_mod
from modules.browser_manager import BrowserManager

# Generous outer bound: the inner timeouts are patched to ~50ms, so anything
# approaching this means an await was not bounded.
OUTER = 3.0


async def _hang():
    await asyncio.Event().wait()


class FakeProc:
    """Stand-in for the asyncio subprocess Playwright keeps at
    p._impl_obj._connection._transport._proc (the node driver)."""

    def __init__(self, dies_on_terminate=True):
        self.pid = 4242
        self.terminated = False
        self.killed = False
        self.returncode = None
        self._dies_on_terminate = dies_on_terminate

    def terminate(self):
        self.terminated = True
        if self._dies_on_terminate:
            self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9

    async def wait(self):
        while self.returncode is None:
            await asyncio.sleep(0.01)
        return self.returncode


def _manager(monkeypatch, *, browser_close, playwright_stop, proc):
    monkeypatch.setattr(bm_mod, "BROWSER_CLOSE_TIMEOUT", 0.05)
    monkeypatch.setattr(bm_mod, "PLAYWRIGHT_STOP_TIMEOUT", 0.05)
    monkeypatch.setattr(bm_mod, "DRIVER_TERMINATE_GRACE", 0.05)
    monkeypatch.setattr(bm_mod, "PAGE_CLOSE_TIMEOUT", 0.05)
    bm = BrowserManager()
    bm.browser = MagicMock()
    bm.browser.close = browser_close
    bm.p = MagicMock()
    bm.p.stop = playwright_stop
    bm.p._impl_obj._connection._transport._proc = proc
    return bm


async def _ok():
    return None


async def test_close_session_returns_when_browser_close_hangs(monkeypatch):
    proc = FakeProc()
    bm = _manager(monkeypatch, browser_close=_hang, playwright_stop=_ok, proc=proc)
    await asyncio.wait_for(bm.close_browser_session(), OUTER)


async def test_close_session_kills_driver_when_browser_close_hangs(monkeypatch):
    proc = FakeProc()
    bm = _manager(monkeypatch, browser_close=_hang, playwright_stop=_ok, proc=proc)
    await asyncio.wait_for(bm.close_browser_session(), OUTER)
    assert proc.terminated, "driver process should be terminated after browser.close() timed out"
    assert bm.browser is None and bm.p is None, "handles must be dropped so nothing retries the hung close"


async def test_close_session_returns_when_playwright_stop_hangs(monkeypatch):
    proc = FakeProc()
    bm = _manager(monkeypatch, browser_close=_ok, playwright_stop=_hang, proc=proc)
    await asyncio.wait_for(bm.close_browser_session(), OUTER)
    assert proc.terminated


async def test_close_session_escalates_to_kill_when_terminate_is_ignored(monkeypatch):
    proc = FakeProc(dies_on_terminate=False)
    bm = _manager(monkeypatch, browser_close=_hang, playwright_stop=_ok, proc=proc)
    await asyncio.wait_for(bm.close_browser_session(), OUTER)
    assert proc.terminated and proc.killed


async def test_close_session_does_not_kill_on_clean_shutdown(monkeypatch):
    proc = FakeProc()
    bm = _manager(monkeypatch, browser_close=_ok, playwright_stop=_ok, proc=proc)
    await asyncio.wait_for(bm.close_browser_session(), OUTER)
    assert not proc.terminated and not proc.killed


async def test_close_session_returns_when_no_driver_handle_is_available(monkeypatch):
    """Playwright internals are private and may move; a hung close must still return."""
    bm = _manager(monkeypatch, browser_close=_hang, playwright_stop=_ok, proc=None)
    bm.p = MagicMock(spec=[])  # no _impl_obj at all
    bm.p.stop = _ok
    await asyncio.wait_for(bm.close_browser_session(), OUTER)
    assert bm.browser is None


async def test_close_page_returns_when_page_close_hangs(monkeypatch):
    monkeypatch.setattr(bm_mod, "PAGE_CLOSE_TIMEOUT", 0.05)
    bm = BrowserManager()
    bm.page = MagicMock()  # the main page; close_page must not touch it
    page = MagicMock()
    page.close = _hang
    await asyncio.wait_for(bm.close_page(page), OUTER)
