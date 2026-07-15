"""Captcha/blocked-page detection against canned inputs (spec: Blocked-page handling)."""
from modules.blocked_detection import detect_block, placeholder_html


def test_http_error_status_is_blocked():
    assert detect_block("Any Title", "any body", status=403) == "HTTP 403"
    assert detect_block("Any Title", "any body", status=429) == "HTTP 429"


def test_cloudflare_challenge_title_detected():
    reason = detect_block("Just a moment...", "Checking your browser", status=200)
    assert reason is not None and "just a moment" in reason


def test_captcha_in_body_detected():
    reason = detect_block("News Site", "Please complete the CAPTCHA to continue")
    assert reason is not None and "captcha" in reason


def test_verify_human_detected():
    assert detect_block("Site", "Verify you are human by completing the action") is not None


def test_clean_page_not_blocked():
    assert detect_block("Tariffs Analysis - The Dispatch",
                        "President's new tariff policy takes effect...",
                        status=200) is None


def test_none_inputs_do_not_crash():
    assert detect_block(None, None) is None


def test_placeholder_contains_url_title_reason_escaped():
    out = placeholder_html("https://x.com/a?b=1&c=2", "A <Title>", "HTTP 403")
    assert "HTTP 403" in out
    assert "A &lt;Title&gt;" in out
    assert "b=1&amp;c=2" in out
    assert "Could not capture" in out
