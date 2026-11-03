"""Unit tests for DispatchPersistentConverter.process_emails in email_converter.py"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime


@pytest.fixture
def mock_email_converter():
    """Build a DispatchPersistentConverter with all dependencies mocked out,
    wired so a single email reaches the post-conversion (upload/tracking) step."""
    with patch('email_converter.AuthManager'), \
         patch('email_converter.EmailHandler'), \
         patch('email_converter.BrowserManager'), \
         patch('email_converter.TrackingManager'), \
         patch('email_converter.ReMarkableManager'), \
         patch('email_converter.LinkProcessor'):
        from email_converter import DispatchPersistentConverter
        converter = DispatchPersistentConverter(rmapi_path='/fake/rmapi')

        converter.auth_manager = MagicMock()
        converter.auth_manager.authenticate_google.return_value = True
        converter.auth_manager.authenticate_with_dispatch = AsyncMock(return_value=True)

        converter.browser_manager = MagicMock()
        converter.browser_manager.start_browser_session = AsyncMock(return_value=True)
        converter.browser_manager.close_browser_session = AsyncMock()
        converter.browser_manager.convert_url_to_pdf_with_page = AsyncMock(return_value=True)
        converter.browser_manager.resolve_final_url = AsyncMock(return_value=None)

        email_data = {
            'subject': 'Test Newsletter',
            'sender': 'The Dispatch',
            'date': datetime.now().isoformat(),
            'message_id': 'msg_123',
        }
        converter.email_handler = MagicMock()
        converter.email_handler.search_dispatch_emails.return_value = [{'id': 'msg_123'}]
        converter.email_handler.get_message_content.return_value = MagicMock()
        converter.email_handler.extract_email_data.return_value = email_data
        converter.email_handler.extract_read_online_url.return_value = 'https://thedispatch.com/newsletter/test/'

        converter.tracking_manager = MagicMock()
        converter.tracking_manager.is_email_processed.return_value = False

        converter.remarkable_manager = MagicMock()
        converter.remarkable_manager.is_available.return_value = True
        converter.remarkable_manager.upload_if_new.return_value = True

        converter.web_processed_urls = set()
        converter.failures = []
        converter._test_email_data = email_data
        yield converter


async def _run(converter, tmp_path):
    with patch('email_converter.FOLLOW_ARTICLE_LINKS', False), \
         patch('email_converter.alert_on_failures'), \
         patch('email_converter.asyncio.sleep', new_callable=AsyncMock):
        await converter.process_emails(
            output_dir=str(tmp_path), max_emails=1,
            upload_to_remarkable=True, force_reprocess=False,
        )


async def test_rejected_pdf_is_not_uploaded_and_counts_as_failure(mock_email_converter, tmp_path, capsys):
    """mark_email_processed() rejecting the PDF (too small/missing) must block the
    upload and be reported as a failed conversion, not a success."""
    mock_email_converter.tracking_manager.mark_email_processed.return_value = False

    await _run(mock_email_converter, tmp_path)

    mock_email_converter.remarkable_manager.upload_if_new.assert_not_called()
    assert any(f["category"] == "conversion" and "validation" in f["detail"]
               for f in mock_email_converter.failures)
    out = capsys.readouterr().out
    assert "Successfully converted: 0/1" in out
    assert "Failed: 1/1" in out


async def test_valid_pdf_upload_updates_tracking_status(mock_email_converter, tmp_path):
    """When the PDF validates and uploads, tracking must be written before the upload
    (remarkable_uploaded=False) and then flipped to uploaded=True afterwards."""
    tm = mock_email_converter.tracking_manager
    tm.mark_email_processed.return_value = True
    email_data = mock_email_converter._test_email_data

    await _run(mock_email_converter, tmp_path)

    mock_email_converter.remarkable_manager.upload_if_new.assert_called_once()
    tm.mark_email_processed.assert_called_once()
    assert tm.mark_email_processed.call_args.kwargs.get('remarkable_uploaded') is False
    tm.update_remarkable_status.assert_called_once_with(email_data, True)
    assert mock_email_converter.failures == []


async def test_valid_pdf_upload_failure_still_saves_tracking(mock_email_converter, tmp_path):
    """If the upload fails after a valid PDF, tracking is saved (uploaded=False)
    and an 'upload' failure is recorded."""
    tm = mock_email_converter.tracking_manager
    tm.mark_email_processed.return_value = True
    mock_email_converter.remarkable_manager.upload_if_new.return_value = False

    await _run(mock_email_converter, tmp_path)

    tm.update_remarkable_status.assert_not_called()
    tm.save_tracking_data.assert_called()
    assert any(f["category"] == "upload" for f in mock_email_converter.failures)


async def test_dispatch_auth_failure_alert_carries_the_managers_reason(mock_email_converter, tmp_path):
    """The alert must say whether the site was unreachable or said we're logged
    out, so a transient timeout doesn't read as 'go refresh your cookies'."""
    am = mock_email_converter.auth_manager
    am.authenticate_with_dispatch = AsyncMock(return_value=False)
    am.dispatch_auth_failure = "site unreachable after 2 attempts: goto timeout 30000ms"

    await _run(mock_email_converter, tmp_path)

    auth = [f for f in mock_email_converter.failures if f["category"] == "auth"]
    assert len(auth) == 1
    assert "unreachable" in auth[0]["detail"]
    assert "expired" not in auth[0]["detail"]


ARTICLE = "https://thedispatch.com/newsletter/wanderland/democrats-donald-trump-congress-midterms/"


async def test_email_whose_article_the_web_pipeline_already_did_is_skipped(mock_email_converter, tmp_path):
    """2026-11-02: same Williamson piece, two titles. The resolved landing URL
    is what both pipelines share, so compare on that before converting."""
    from modules.tracking import normalize_article_url
    c = mock_email_converter
    c.browser_manager.resolve_final_url = AsyncMock(return_value=ARTICLE + "?utm_source=email")
    c.web_processed_urls = {normalize_article_url(ARTICLE)}

    await _run(c, tmp_path)

    c.browser_manager.convert_url_to_pdf_with_page.assert_not_called()
    c.tracking_manager.mark_email_processed.assert_not_called()
    c.remarkable_manager.upload_if_new.assert_not_called()


async def test_email_conversion_uses_and_records_the_resolved_url(mock_email_converter, tmp_path):
    c = mock_email_converter
    c.browser_manager.resolve_final_url = AsyncMock(return_value=ARTICLE)

    await _run(c, tmp_path)

    assert c._test_email_data["resolved_url"] == ARTICLE
    c.browser_manager.convert_url_to_pdf_with_page.assert_called_once()
    assert c.browser_manager.convert_url_to_pdf_with_page.call_args.args[0] == ARTICLE
