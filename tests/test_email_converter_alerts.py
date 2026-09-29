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


async def test_email_pipeline_never_checks_the_atlantic(tmp_path, monkeypatch):
    """The Atlantic was dropped (its bot protection blocks automated browsers):
    even with link following on and an old jar on disk, no Atlantic check runs
    and no Atlantic auth failure is recorded."""
    jar = tmp_path / "atlantic_cookies.json"
    jar.write_text("[]")
    monkeypatch.setattr("modules.auth.ATLANTIC_COOKIES_FILE", jar, raising=False)
    alerts = []
    with patch('email_converter.AuthManager') as A, \
         patch('email_converter.EmailHandler') as E, \
         patch('email_converter.BrowserManager') as B, \
         patch('email_converter.TrackingManager'), \
         patch('email_converter.ReMarkableManager'), \
         patch('email_converter.FOLLOW_ARTICLE_LINKS', True), \
         patch('email_converter.alert_on_failures', side_effect=lambda f, run_label="": alerts.append(list(f))):
        A.return_value.authenticate_google.return_value = True
        A.return_value.authenticate_with_dispatch = AsyncMock(return_value=True)
        A.return_value.authenticate_with_atlantic = AsyncMock(return_value=False)
        B.return_value.start_browser_session = AsyncMock(return_value=True)
        B.return_value.close_browser_session = AsyncMock()
        B.return_value.get_page.return_value = MagicMock()
        B.return_value.get_context.return_value = MagicMock()
        E.return_value.search_dispatch_emails.return_value = []

        from email_converter import run_email_converter
        await run_email_converter()

    A.return_value.authenticate_with_atlantic.assert_not_called()
    assert not [f for batch in alerts for f in batch if "Atlantic" in f["item"]]
