"""Unit tests for DispatchConverter logic in main.py"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime


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
