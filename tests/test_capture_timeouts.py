"""A single wedged tab must never stall a run.

2026-10-03 07:05 cron run: linked page [8] (a Cloudflare CAPTCHA page) printed
"inserting placeholder page" and then never returned — page.set_content()/page.pdf()
carry no timeout — so asyncio.gather() waited and the wrapper killed the run at 4h.
Every other capture had finished by 07:07.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


async def _never(*args, **kwargs):
    await asyncio.Event().wait()


def _target():
    return SimpleNamespace(url="https://www.city-journal.org/article/x", link_text="X",
                           depth=1, parent_url="https://thedispatch.com/newsletter/m/")


def _page_that_renders_then_wedges():
    page = AsyncMock()
    page.goto = AsyncMock(return_value=MagicMock(status=200))
    page.title = AsyncMock(return_value="City Journal")
    page.evaluate = AsyncMock(return_value="")
    page.content = AsyncMock(return_value="<html><body></body></html>")
    page.pdf = _never                       # renderer wedged: printToPDF never answers
    return page


def _fresh_page_ok(tmp_path):
    fresh = AsyncMock()
    async def pdf(path, **kwargs):
        open(path, "wb").write(b"%PDF placeholder")
    fresh.pdf = pdf
    return fresh


async def test_wedged_capture_resolves_to_placeholder_within_hard_bound(tmp_path):
    from modules.link_processor import LinkProcessor
    bm = MagicMock()
    bm.create_new_page = AsyncMock(side_effect=[_page_that_renders_then_wedges(), _fresh_page_ok(tmp_path)])
    bm.close_page = AsyncMock()
    bm.remove_header_elements_from_page = AsyncMock()

    with patch('modules.link_processor.LINKED_PAGE_HARD_TIMEOUT', 0.3), \
         patch('modules.link_processor.asyncio.sleep', new_callable=AsyncMock):
        result = await asyncio.wait_for(LinkProcessor(bm)._capture_one(_target(), 8, tmp_path), timeout=5)

    assert result is not None
    assert result['blocked'] and 'timed out' in result['blocked']
    assert result['child_links'] == []
    assert bm.close_page.await_count == 2     # wedged tab AND the placeholder tab were closed


async def test_wedged_placeholder_tab_is_dropped_not_awaited_forever(tmp_path):
    from modules.link_processor import LinkProcessor
    fresh_hung = AsyncMock(); fresh_hung.set_content = _never
    bm = MagicMock()
    bm.create_new_page = AsyncMock(side_effect=[_page_that_renders_then_wedges(), fresh_hung])
    bm.close_page = AsyncMock()
    bm.remove_header_elements_from_page = AsyncMock()

    with patch('modules.link_processor.LINKED_PAGE_HARD_TIMEOUT', 0.3), \
         patch('modules.link_processor.PLACEHOLDER_RENDER_TIMEOUT', 0.3), \
         patch('modules.link_processor.asyncio.sleep', new_callable=AsyncMock):
        result = await asyncio.wait_for(LinkProcessor(bm)._capture_one(_target(), 8, tmp_path), timeout=5)

    assert result is None


async def test_main_article_pdf_render_is_bounded(tmp_path):
    """Same hang class for the article itself: page.pdf() has no timeout."""
    from modules.browser_manager import BrowserManager
    bm = BrowserManager()
    bm.save_html_snapshot_from_page = AsyncMock()
    bm.remove_header_elements_from_page = AsyncMock()
    page = AsyncMock(); page.url = "about:blank"; page.pdf = _never

    with patch('modules.browser_manager.PDF_RENDER_TIMEOUT', 0.3), \
         patch('modules.browser_manager.asyncio.sleep', new_callable=AsyncMock):
        ok = await asyncio.wait_for(
            bm.convert_url_to_pdf_with_page("https://thedispatch.com/article/x/", str(tmp_path / "o.pdf"), page),
            timeout=5)

    assert ok is False
