"""Unit tests for DispatchConverter logic in main.py"""
import sys
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime


async def test_normal_mode_runs_email_before_website(monkeypatch):
    """In normal (scan) mode the email pipeline runs BEFORE the website pipeline,
    so its clean newsletter renders land on the device first and win the dedup."""
    import main

    calls = []

    class FakeConverter:
        def __init__(self, *a, **k):
            pass
        def print_startup_banner(self):
            pass
        def retry_failed_uploads(self):
            calls.append('retry')
        async def process_content(self, *a, **k):
            calls.append('website')
            return True

    async def fake_email():
        calls.append('email')

    monkeypatch.setattr(main, 'DispatchConverter', FakeConverter)
    monkeypatch.setattr(main, 'run_email_converter', fake_email)
    monkeypatch.setattr(sys, 'argv', ['main.py'])

    await main.main()

    assert calls == ['email', 'website']


async def test_normal_mode_defers_upload_and_force_to_config(monkeypatch):
    """main() must not hardcode upload/force_reprocess — it should defer to config
    (.env) so UPLOAD_TO_REMARKABLE / FORCE_REPROCESS are actually respected."""
    import main
    captured = {}

    class FakeConverter:
        def __init__(self, *a, **k):
            pass
        def print_startup_banner(self):
            pass
        async def process_content(self, *a, **k):
            captured["args"] = a
            captured["kwargs"] = k
            return True

    monkeypatch.setattr(main, "DispatchConverter", FakeConverter)
    monkeypatch.setattr(sys, "argv", ["main.py", "--skip-email"])

    await main.main()

    # No positional/keyword override of these — config governs.
    assert captured.get("kwargs", {}).get("upload_to_remarkable") is None
    assert captured.get("kwargs", {}).get("force_reprocess") is None
    assert captured.get("args", ()) == ()


# ---------------------------------------------------------------------------
# Helpers (no imports from main.py yet — these tests drive the implementation)
# ---------------------------------------------------------------------------

def slug_to_title(url: str) -> str:
    """Extract URL slug and convert to title-case string.

    e.g. https://thedispatch.com/article/neon-genesis-evangelion-american-millennials/
      -> 'Neon Genesis Evangelion American Millennials'
    """
    from urllib.parse import urlparse
    path = urlparse(url).path
    segments = [s for s in path.split('/') if s]
    if not segments:
        return "Article"
    slug = segments[-1]
    return slug.replace('-', ' ').title()


class TestSlugToTitle:
    def test_standard_article_url(self):
        url = "https://thedispatch.com/article/neon-genesis-evangelion-american-millennials/"
        assert slug_to_title(url) == "Neon Genesis Evangelion American Millennials"

    def test_trailing_slash_stripped(self):
        url = "https://thedispatch.com/p/some-article/"
        assert slug_to_title(url) == "Some Article"

    def test_no_trailing_slash(self):
        url = "https://thedispatch.com/article/my-article"
        assert slug_to_title(url) == "My Article"

    def test_root_url_returns_default(self):
        url = "https://thedispatch.com/"
        assert slug_to_title(url) == "Article"


# ---------------------------------------------------------------------------
# Tests for process_single_item_parallel new parameters
# These import from main.py — they will fail until Task 2 is complete.
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_converter():
    """Build a DispatchConverter with all dependencies mocked out."""
    with patch('main.AuthManager'), \
         patch('main.EmailHandler'), \
         patch('main.BrowserManager'), \
         patch('main.TrackingManager'), \
         patch('main.ReMarkableManager'), \
         patch('main.WebsiteScanner'), \
         patch('main.LinkProcessor'), \
         patch('config.settings.OUTPUT_DIR', __import__('pathlib').Path('/tmp/dispatch_test_pdfs')):
        from main import DispatchConverter
        converter = DispatchConverter()
        # Wire up the mocks for easy assertion
        converter.tracking_manager = MagicMock()
        converter.tracking_manager.is_email_processed.return_value = True  # Would skip without force_reprocess
        converter.tracking_manager.get_processed_info.return_value = {'processed_date': '2026-01-01'}
        converter.browser_manager = MagicMock()
        converter.browser_manager.create_new_page = AsyncMock(return_value=MagicMock())
        converter.browser_manager.close_page = AsyncMock()
        converter.browser_manager.close_browser_session = AsyncMock()
        converter.browser_manager.convert_url_to_pdf_with_page = AsyncMock(return_value=False)
        converter.stats = {
            'total_emails': 0,
            'total_articles': 0,
            'successful_conversions': 0,
            'skipped_duplicates': 0,
            'failed_conversions': 0,
            'remarkable_uploads': 0,
            'remarkable_failures': 0,
            'processing_time': 0,
            'total_file_size': 0,
            'processing_mode': 'website',
            'total_linked_pages': 0,
            'follow_links_enabled': False,
            'remarkable_enabled': False,
        }
        converter.processing_mode = 'website'
        converter.output_dir = __import__('pathlib').Path('/tmp/dispatch_test_pdfs')
        yield converter


async def test_force_reprocess_false_skips_already_processed(mock_converter):
    """Without force_reprocess, already-processed items are skipped."""
    content_data = {
        'subject': 'Test Article',
        'read_online_url': 'https://thedispatch.com/article/test/',
        'message_id': 'test_123',
        'sender': 'test',
        'date': datetime.now().isoformat(),
    }
    mock_converter.tracking_manager.is_email_processed.return_value = True

    result = await mock_converter.process_single_item_parallel(content_data, 1)

    assert result is True
    assert mock_converter.stats['skipped_duplicates'] == 1
    mock_converter.tracking_manager.is_email_processed.assert_called_once()


async def test_force_reprocess_true_bypasses_tracking_check(mock_converter):
    """With force_reprocess=True, already-processed items are NOT skipped."""
    content_data = {
        'subject': 'Test Article',
        'read_online_url': 'https://thedispatch.com/article/test/',
        'message_id': 'test_123',
        'sender': 'test',
        'date': datetime.now().isoformat(),
    }
    mock_converter.tracking_manager.is_email_processed.return_value = True

    result = await mock_converter.process_single_item_parallel(
        content_data, 1, force_reprocess=True
    )

    # Should NOT have incremented skipped_duplicates
    assert mock_converter.stats['skipped_duplicates'] == 0
    # tracking check should not have been called
    mock_converter.tracking_manager.is_email_processed.assert_not_called()


async def test_effective_mode_website_enables_link_following(mock_converter):
    """effective_mode='website' causes link_processor to be instantiated (link following path)."""
    content_data = {
        'subject': 'Test Article',
        'read_online_url': 'https://thedispatch.com/article/test/',
        'message_id': 'test_123',
        'sender': 'test',
        'date': datetime.now().isoformat(),
    }
    mock_converter.processing_mode = 'email'  # Would normally block link following

    with patch('main.FOLLOW_ARTICLE_LINKS', True), \
         patch('main.LinkProcessor') as MockLP:
        mock_lp_instance = MagicMock()
        mock_lp_instance.process_article_with_links = AsyncMock(return_value=True)
        mock_lp_instance.get_processing_summary = MagicMock(return_value={'linked_pages': 0, 'total_pages': 1})
        MockLP.return_value = mock_lp_instance

        await mock_converter.process_single_item_parallel(
            content_data, 1, force_reprocess=True, effective_mode='website'
        )

    # LinkProcessor should have been instantiated (link following ran)
    MockLP.assert_called_once()
    mock_lp_instance.process_article_with_links.assert_called_once()


# ---------------------------------------------------------------------------
# Tests for process_single_url
# ---------------------------------------------------------------------------

async def test_process_single_url_builds_correct_content_data(mock_converter):
    """process_single_url builds content_data from URL and calls process_single_item_parallel."""
    url = "https://thedispatch.com/article/neon-genesis-evangelion-american-millennials/"

    with patch.object(mock_converter, 'process_single_item_parallel', new_callable=AsyncMock) as mock_psi:
        mock_psi.return_value = True
        await mock_converter.process_single_url(url)

    mock_psi.assert_called_once()
    call_args = mock_psi.call_args
    content_data = call_args[0][0]  # first positional arg

    assert content_data['subject'] == 'Neon Genesis Evangelion American Millennials'
    assert content_data['read_online_url'] == url
    assert content_data['source'] == 'cli'
    assert content_data['sender'] == 'CLI'
    assert content_data['is_html'] is True
    # force_reprocess=True must be passed
    assert call_args[1].get('force_reprocess') is True or call_args[0][2] is True
    # effective_mode='website' must be passed
    assert call_args[1].get('effective_mode') == 'website' or call_args[0][3] == 'website'


# ---------------------------------------------------------------------------
# Review fix 1: --url mode must honor UPLOAD_TO_REMARKABLE and alert on failure
# ---------------------------------------------------------------------------

async def test_process_single_url_enables_upload_from_config(mock_converter):
    """--url bypasses process_content(), so process_single_url must set
    remarkable_enabled itself or the upload block is silently skipped."""
    with patch('main.DEFAULT_UPLOAD_TO_REMARKABLE', True), \
         patch.object(mock_converter, 'process_single_item_parallel', new_callable=AsyncMock) as mock_psi:
        mock_psi.return_value = True
        await mock_converter.process_single_url("https://thedispatch.com/article/x/")

    assert mock_converter.stats['remarkable_enabled'] is True


async def test_process_single_url_returns_item_result(mock_converter):
    """The --url path needs the conversion result to set the exit code."""
    with patch.object(mock_converter, 'process_single_item_parallel', new_callable=AsyncMock) as mock_psi:
        mock_psi.return_value = False
        result = await mock_converter.process_single_url("https://thedispatch.com/article/x/")

    assert result is False


async def test_url_mode_alerts_on_failures(monkeypatch):
    """main() --url must flush the converter's failure records through alerting."""
    import main

    class FakeConverter:
        def __init__(self, *a, **k):
            self.failures = [{"category": "conversion", "item": "x", "detail": "boom"}]
        def print_startup_banner(self):
            pass
        async def initialize(self):
            return True
        async def process_single_url(self, url):
            return False
        def print_final_summary(self):
            pass
        async def cleanup(self):
            pass

    alert = MagicMock()
    monkeypatch.setattr(main, 'DispatchConverter', FakeConverter)
    monkeypatch.setattr(main, 'alert_on_failures', alert)
    monkeypatch.setattr(sys, 'argv', ['main.py', '--url', 'https://thedispatch.com/article/x/', '--skip-email'])

    await main.main()

    alert.assert_called_once()
    assert alert.call_args[0][0][0]["item"] == "x"


# ---------------------------------------------------------------------------
# Review fix 2: init failures, empty scans and crashes must not be silent
# ---------------------------------------------------------------------------

async def test_initialize_records_auth_failure(mock_converter):
    """A failed Google login must leave an 'auth' failure record behind."""
    mock_converter.processing_mode = 'email'
    mock_converter.auth_manager = MagicMock()
    mock_converter.auth_manager.authenticate_google.return_value = False

    with patch('main.check_dependencies', return_value=True):
        ok = await mock_converter.initialize()

    assert ok is False
    assert any(f["category"] == "auth" for f in mock_converter.failures)


async def test_process_content_alerts_when_initialize_fails(mock_converter):
    """process_content() returning False from a failed initialize() must still alert."""
    with patch.object(mock_converter, 'initialize', new_callable=AsyncMock, return_value=False), \
         patch('main.alert_on_failures') as alert:
        result = await mock_converter.process_content()

    assert result is False
    alert.assert_called_once()


async def test_process_content_alerts_when_scan_finds_nothing(mock_converter):
    """Zero articles from a website scan usually means we're blocked — alert."""
    with patch.object(mock_converter, 'initialize', new_callable=AsyncMock, return_value=True), \
         patch.object(mock_converter, 'get_website_content', new_callable=AsyncMock, return_value=[]), \
         patch('main.alert_on_failures') as alert:
        result = await mock_converter.process_content()

    assert result is False
    alert.assert_called_once()
    failures = alert.call_args[0][0]
    assert failures and failures[0]["category"] == "scan"


async def test_main_alerts_on_unhandled_exception(monkeypatch):
    """A crash escaping process_content() must still produce an alert."""
    import main

    class FakeConverter:
        def __init__(self, *a, **k):
            self.failures = []
        def print_startup_banner(self):
            pass
        async def process_content(self, *a, **k):
            raise RuntimeError("browser exploded")

    alert = MagicMock()
    monkeypatch.setattr(main, 'DispatchConverter', FakeConverter)
    monkeypatch.setattr(main, 'alert_on_failures', alert)
    monkeypatch.setattr(sys, 'argv', ['main.py', '--skip-email'])

    await main.main()

    alert.assert_called_once()
    assert "browser exploded" in alert.call_args[0][0][0]["detail"]


async def test_email_pipeline_wrapper_alerts_on_exception(monkeypatch):
    """_run_email_pipeline swallows exceptions; it must alert rather than just print."""
    import main

    async def boom():
        raise RuntimeError("gmail down")

    alert = MagicMock()
    monkeypatch.setattr(main, 'run_email_converter', boom)
    monkeypatch.setattr(main, 'alert_on_failures', alert)

    ok = await main._run_email_pipeline()

    assert ok is False
    alert.assert_called_once()
    assert "gmail down" in alert.call_args[0][0][0]["detail"]


# ---------------------------------------------------------------------------
# Review fix 3: validate the PDF (via tracking) BEFORE uploading it
# ---------------------------------------------------------------------------

def _converted_item(mock_converter):
    mock_converter.browser_manager.convert_url_to_pdf_with_page = AsyncMock(return_value=True)
    mock_converter.stats['remarkable_enabled'] = True
    mock_converter.remarkable_manager = MagicMock()
    mock_converter.remarkable_manager.is_available.return_value = True
    mock_converter.remarkable_manager.upload_if_new.return_value = True
    return {
        'subject': 'Test Article',
        'read_online_url': 'https://thedispatch.com/article/test/',
        'message_id': 'test_123',
        'sender': 'test',
        'date': datetime.now().isoformat(),
    }


async def test_rejected_pdf_is_not_uploaded_and_counts_as_failure(mock_converter):
    """mark_email_processed() rejecting the PDF (too small/missing) must block the
    upload and be reported as a failed conversion, not a success."""
    content_data = _converted_item(mock_converter)
    mock_converter.tracking_manager.mark_email_processed.return_value = False

    with patch('main.FOLLOW_ARTICLE_LINKS', False):
        result = await mock_converter.process_single_item_parallel(content_data, 1, force_reprocess=True)

    assert result is False
    mock_converter.remarkable_manager.upload_if_new.assert_not_called()
    assert mock_converter.stats['failed_conversions'] == 1
    assert mock_converter.stats['successful_conversions'] == 0
    assert any(f["category"] == "conversion" for f in mock_converter.failures)


async def test_valid_pdf_upload_updates_tracking_status(mock_converter):
    """When the PDF validates and uploads, tracking must end up remarkable_uploaded=True."""
    content_data = _converted_item(mock_converter)
    mock_converter.tracking_manager.mark_email_processed.return_value = True

    with patch('main.FOLLOW_ARTICLE_LINKS', False):
        result = await mock_converter.process_single_item_parallel(content_data, 1, force_reprocess=True)

    assert result is True
    mock_converter.remarkable_manager.upload_if_new.assert_called_once()
    mock_converter.tracking_manager.update_remarkable_status.assert_called_once_with(content_data, True)
    assert mock_converter.stats['remarkable_uploads'] == 1


# ---------------------------------------------------------------------------
# Review fix 4: exit code must reflect the run outcome
# ---------------------------------------------------------------------------

def _fake_converter_returning(value):
    class FakeConverter:
        def __init__(self, *a, **k):
            self.failures = []
        def print_startup_banner(self):
            pass
        async def process_content(self, *a, **k):
            return value
    return FakeConverter


async def test_main_returns_zero_on_success(monkeypatch):
    import main
    monkeypatch.setattr(main, 'DispatchConverter', _fake_converter_returning(True))
    monkeypatch.setattr(sys, 'argv', ['main.py', '--skip-email'])
    assert await main.main() == 0


async def test_main_returns_nonzero_when_processing_fails(monkeypatch):
    import main
    monkeypatch.setattr(main, 'DispatchConverter', _fake_converter_returning(False))
    monkeypatch.setattr(sys, 'argv', ['main.py', '--skip-email'])
    # 2 = "failed, already alerted" so run_pipeline.sh doesn't send a second email
    assert await main.main() == main.EXIT_FAILURE_ALERTED == 2


async def test_main_returns_nonzero_on_unhandled_exception(monkeypatch):
    import main

    class FakeConverter:
        def __init__(self, *a, **k):
            self.failures = []
        def print_startup_banner(self):
            pass
        async def process_content(self, *a, **k):
            raise RuntimeError("boom")

    monkeypatch.setattr(main, 'DispatchConverter', FakeConverter)
    monkeypatch.setattr(main, 'alert_on_failures', MagicMock())
    monkeypatch.setattr(sys, 'argv', ['main.py', '--skip-email'])
    # main() alerted on the crash itself, so it reports the "already alerted" code
    assert await main.main() == main.EXIT_FAILURE_ALERTED


# ---------------------------------------------------------------------------
# Module review: blocking rmapi calls must not stall the event loop
# ---------------------------------------------------------------------------

async def test_upload_runs_off_the_event_loop_thread(mock_converter):
    """upload_if_new is a blocking subprocess; it must run via asyncio.to_thread
    so the other concurrent conversions keep making progress."""
    import threading
    content_data = _converted_item(mock_converter)
    mock_converter.tracking_manager.mark_email_processed.return_value = True
    seen = {}

    def upload(*a, **k):
        seen['thread'] = threading.current_thread()
        return True
    mock_converter.remarkable_manager.upload_if_new.side_effect = upload

    with patch('main.FOLLOW_ARTICLE_LINKS', False):
        await mock_converter.process_single_item_parallel(content_data, 1, force_reprocess=True)

    assert seen['thread'] is not threading.main_thread()


async def test_inventory_refresh_runs_off_the_event_loop_thread(mock_converter):
    import threading
    seen = {}

    def refresh(*a, **k):
        seen['thread'] = threading.current_thread()
    mock_converter.remarkable_manager = MagicMock()
    mock_converter.remarkable_manager.is_available.return_value = True
    mock_converter.remarkable_manager.refresh_inventory.side_effect = refresh
    item = {'subject': 'S', 'read_online_url': 'https://thedispatch.com/article/s/',
            'message_id': 'm', 'sender': 'x', 'date': 'd'}
    mock_converter.tracking_manager.is_email_processed.return_value = False

    with patch.object(mock_converter, 'initialize', new_callable=AsyncMock, return_value=True), \
         patch.object(mock_converter, 'get_website_content', new_callable=AsyncMock, return_value=[item]), \
         patch.object(mock_converter, 'process_items_parallel', new_callable=AsyncMock), \
         patch('main.PRUNE_NEWS_ENABLED', False), \
         patch('main.alert_on_failures'):
        await mock_converter.process_content(upload_to_remarkable=True)

    assert seen['thread'] is not threading.main_thread()


# ---------------------------------------------------------------------------
# The Atlantic session check runs during initialize() when link following is on,
# and is non-fatal: the run proceeds, an expired jar just records an alert.
# ---------------------------------------------------------------------------

def _init_ready(mock_converter):
    mock_converter.auth_manager = MagicMock()
    mock_converter.auth_manager.authenticate_with_dispatch = AsyncMock(return_value=True)
    mock_converter.browser_manager.start_browser_session = AsyncMock(return_value=True)
    mock_converter.browser_manager.get_page.return_value = MagicMock()
    mock_converter.browser_manager.get_context.return_value = MagicMock()


async def test_initialize_runs_non_fatal_atlantic_check_when_following_links(mock_converter):
    _init_ready(mock_converter)

    async def fake_check(auth_manager, page, context, failures):
        failures.append({"category": "auth", "item": "The Atlantic", "detail": "expired"})

    with patch('main.check_dependencies', return_value=True), \
         patch('main.FOLLOW_ARTICLE_LINKS', True), \
         patch('main.check_atlantic_session', side_effect=fake_check) as chk:
        ok = await mock_converter.initialize()

    assert ok is True                      # non-fatal
    chk.assert_called_once()
    assert any(f["item"] == "The Atlantic" for f in mock_converter.failures)


async def test_initialize_skips_atlantic_check_without_link_following(mock_converter):
    _init_ready(mock_converter)
    with patch('main.check_dependencies', return_value=True), \
         patch('main.FOLLOW_ARTICLE_LINKS', False), \
         patch('main.check_atlantic_session', new_callable=AsyncMock) as chk:
        assert await mock_converter.initialize() is True
    chk.assert_not_called()
