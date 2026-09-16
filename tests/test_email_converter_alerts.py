"""The email pipeline's early exits (auth/browser failures) must alert, not just print."""
from unittest.mock import AsyncMock, MagicMock, patch


async def test_dispatch_auth_failure_alerts():
    with patch('email_converter.AuthManager') as A, \
         patch('email_converter.EmailHandler'), \
         patch('email_converter.BrowserManager') as B, \
         patch('email_converter.TrackingManager'), \
         patch('email_converter.ReMarkableManager'), \
         patch('email_converter.alert_on_failures') as alert:
        A.return_value.authenticate_google.return_value = True
        A.return_value.authenticate_with_dispatch = AsyncMock(return_value=False)
        B.return_value.start_browser_session = AsyncMock(return_value=True)
        B.return_value.close_browser_session = AsyncMock()
        B.return_value.get_page.return_value = MagicMock()
        B.return_value.get_context.return_value = MagicMock()

        from email_converter import run_email_converter
        await run_email_converter()

    alert.assert_called_once()
    failures = alert.call_args[0][0]
    assert failures and failures[0]["category"] == "auth"


async def test_google_auth_failure_alerts():
    with patch('email_converter.AuthManager') as A, \
         patch('email_converter.EmailHandler'), \
         patch('email_converter.BrowserManager') as B, \
         patch('email_converter.TrackingManager'), \
         patch('email_converter.ReMarkableManager'), \
         patch('email_converter.alert_on_failures') as alert:
        A.return_value.authenticate_google.return_value = False
        B.return_value.close_browser_session = AsyncMock()

        from email_converter import run_email_converter
        await run_email_converter()

    alert.assert_called_once()
    assert alert.call_args[0][0][0]["category"] == "auth"


async def test_browser_start_failure_alerts():
    with patch('email_converter.AuthManager') as A, \
         patch('email_converter.EmailHandler'), \
         patch('email_converter.BrowserManager') as B, \
         patch('email_converter.TrackingManager'), \
         patch('email_converter.ReMarkableManager'), \
         patch('email_converter.alert_on_failures') as alert:
        A.return_value.authenticate_google.return_value = True
        B.return_value.start_browser_session = AsyncMock(return_value=False)
        B.return_value.close_browser_session = AsyncMock()

        from email_converter import run_email_converter
        await run_email_converter()

    alert.assert_called_once()
    assert alert.call_args[0][0][0]["item"] == "initialization"
