"""Cross-pipeline dedup by the article's real address.

2026-11-02 17:00 run: the email pipeline uploaded Williamson's Wanderland as
"A Mandate for Democrats" (email subject) and two minutes later the website
pipeline uploaded the same piece as "Democrats' Simple Midterms Mandate…"
(web headline). Titles differ, and the email entry stored only the newsletter's
l.thedispatch.com tracking link, so neither title nor URL dedup could match.
Fix: resolve the tracking link in the browser, store the landing URL, and let
both pipelines compare normalized article URLs.
"""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from config.settings import MIN_PDF_SIZE_BYTES


ARTICLE = "https://thedispatch.com/newsletter/wanderland/democrats-donald-trump-congress-midterms/"
TRACKING_LINK = "https://l.thedispatch.com/z/c/27175/b3327bcdcd814b68b8b0af6e78304143/abc"


# --- tracking -------------------------------------------------------------

def test_normalize_article_url_ignores_host_case_query_fragment_and_trailing_slash():
    from modules.tracking import normalize_article_url
    variants = [
        ARTICLE,
        ARTICLE.rstrip("/"),
        ARTICLE + "?utm_source=email&utm_medium=newsletter",
        ARTICLE.replace("thedispatch.com", "TheDispatch.com") + "#comments",
    ]
    assert len({normalize_article_url(u) for u in variants}) == 1
    assert normalize_article_url("") == ""
    assert normalize_article_url(None) == ""


def test_get_processed_urls_includes_the_resolved_article_url(tmp_path):
    from modules.tracking import TrackingManager, normalize_article_url
    tm = TrackingManager(tracking_file=tmp_path / "t.json")
    tm.processed_emails["fp"] = {
        "success": True, "remarkable_uploaded": True,
        "read_online_url": TRACKING_LINK,
        "resolved_url": ARTICLE + "?utm_source=email",
    }
    urls = tm.get_processed_urls()
    assert normalize_article_url(ARTICLE) in urls
    assert normalize_article_url(TRACKING_LINK) in urls, "the raw link still counts"


def test_mark_email_processed_stores_the_resolved_url(tmp_path):
    from modules.tracking import TrackingManager
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"x" * (MIN_PDF_SIZE_BYTES + 1))
    tm = TrackingManager(tracking_file=tmp_path / "t.json")
    email_data = {"subject": "A Mandate for Democrats", "message_id": "m1",
                  "read_online_url": TRACKING_LINK, "resolved_url": ARTICLE}
    assert tm.mark_email_processed(email_data, str(pdf), remarkable_uploaded=True, success=True)
    entry = tm.get_processed_info(email_data)
    assert entry["resolved_url"] == ARTICLE
    assert entry["read_online_url"] == TRACKING_LINK


# --- website scanner ------------------------------------------------------

def _scanner():
    from modules.website_scanner import WebsiteScanner
    return WebsiteScanner(MagicMock(), tracking_manager=None)


def _article(url):
    return {'url': url, 'title': "Democrats' Simple Midterms Mandate", 'date': None,
            'summary': '', 'source': 'homepage'}


def test_scanner_skips_an_article_the_email_pipeline_already_resolved():
    s = _scanner()
    s.add_processed_urls({ARTICLE + "?utm_source=email"})
    with patch('modules.website_scanner.SKIP_KEYWORDS', []):
        assert s.filter_articles([_article(ARTICLE)]) == []


def test_scanner_compares_urls_in_normalized_form():
    from modules.tracking import normalize_article_url
    s = _scanner()
    s.processed_urls = {normalize_article_url(ARTICLE)}
    with patch('modules.website_scanner.SKIP_KEYWORDS', []):
        assert s.filter_articles([_article(ARTICLE.rstrip("/") + "?ref=home")]) == []


# --- browser: resolve the tracking link -----------------------------------

def _page(landing_url):
    page = AsyncMock()
    page.url = landing_url
    return page


async def test_resolve_final_url_returns_where_the_browser_landed():
    from modules.browser_manager import BrowserManager
    page = _page(ARTICLE + "?utm_source=email")
    with patch('modules.browser_manager.asyncio.sleep', new_callable=AsyncMock):
        assert await BrowserManager().resolve_final_url(TRACKING_LINK, page) == ARTICLE + "?utm_source=email"
    page.goto.assert_awaited_once()
    assert page.goto.await_args.args[0] == TRACKING_LINK


async def test_resolve_final_url_is_none_when_still_on_the_tracking_host():
    from modules.browser_manager import BrowserManager
    page = _page(TRACKING_LINK)
    with patch('modules.browser_manager.asyncio.sleep', new_callable=AsyncMock):
        assert await BrowserManager().resolve_final_url(TRACKING_LINK, page) is None


async def test_resolve_final_url_is_none_on_navigation_error():
    from modules.browser_manager import BrowserManager
    page = _page(ARTICLE)
    page.goto = AsyncMock(side_effect=TimeoutError("goto timeout"))
    with patch('modules.browser_manager.asyncio.sleep', new_callable=AsyncMock):
        assert await BrowserManager().resolve_final_url(TRACKING_LINK, page) is None


# --- main.py wiring --------------------------------------------------------

def test_website_scanner_is_told_about_email_pipeline_urls():
    email_urls = {ARTICLE}

    def tracking_factory(tracking_file=None):
        m = MagicMock()
        m.get_processed_urls.return_value = email_urls if tracking_file is not None else set()
        return m

    with patch('main.AuthManager'), patch('main.EmailHandler'), patch('main.BrowserManager'), \
         patch('main.TrackingManager', side_effect=tracking_factory), \
         patch('main.ReMarkableManager'), patch('main.WebsiteScanner') as WS, \
         patch('main.LinkProcessor'), \
         patch('config.settings.OUTPUT_DIR', Path('/tmp/dispatch_test_pdfs')):
        from main import DispatchConverter
        DispatchConverter()

    WS.return_value.add_processed_urls.assert_called_once_with(email_urls)
