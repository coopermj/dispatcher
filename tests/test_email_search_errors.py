"""'No newsletters today' (Sunday) and 'the Gmail call failed' must not look the same."""
from unittest.mock import AsyncMock, MagicMock, patch


def _handler(service):
    from modules.email_handler import EmailHandler
    am = MagicMock(); am.get_gmail_service.return_value = service
    return EmailHandler(am)


def test_search_returns_empty_list_when_gmail_has_nothing():
    svc = MagicMock()
    svc.users().messages().list().execute.return_value = {}
    assert _handler(svc).search_dispatch_emails(5) == []


def test_search_returns_none_on_api_error():
    svc = MagicMock()
    svc.users().messages().list().execute.side_effect = RuntimeError("invalid_grant")
    assert _handler(svc).search_dispatch_emails(5) is None


def test_search_returns_none_without_service():
    assert _handler(None).search_dispatch_emails(5) is None


def _email_pipeline_patches():
    return [patch('email_converter.AuthManager'), patch('email_converter.EmailHandler'),
            patch('email_converter.BrowserManager'), patch('email_converter.TrackingManager'),
            patch('email_converter.ReMarkableManager'), patch('email_converter.alert_on_failures')]


async def _run_email_pipeline_with(messages):
    ps = _email_pipeline_patches()
    mocks = [p.start() for p in ps]
    try:
        A, E, B, T, R, alert = mocks
        A.return_value.authenticate_google.return_value = True
        A.return_value.authenticate_with_dispatch = AsyncMock(return_value=True)
        B.return_value.start_browser_session = AsyncMock(return_value=True)
        B.return_value.close_browser_session = AsyncMock()
        B.return_value.get_page.return_value = MagicMock(); B.return_value.get_context.return_value = MagicMock()
        R.return_value.is_available.return_value = False
        E.return_value.search_dispatch_emails.return_value = messages
        from email_converter import run_email_converter
        await run_email_converter()
        return alert.call_args[0][0] if alert.call_args else []
    finally:
        for p in ps: p.stop()


async def test_email_pipeline_quiet_on_empty_mailbox():
    assert await _run_email_pipeline_with([]) == []


async def test_email_pipeline_alerts_on_search_error():
    failures = await _run_email_pipeline_with(None)
    assert failures and failures[0]["category"] == "scan"
