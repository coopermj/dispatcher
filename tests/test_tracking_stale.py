"""A tracked-but-not-uploaded item whose PDF is gone: re-gather it if recent
(regenerate + upload is the right recovery), but if it's older than the prune
window mark it expired instead — otherwise 'done' depends on a local file forever
and the prune can never delete it."""
import json
from datetime import datetime, timedelta
from unittest.mock import patch


def _tm(tmp_path, days_old, pdf_exists):
    from modules.tracking import TrackingManager
    pdf = tmp_path / "a.pdf"
    if pdf_exists: pdf.write_bytes(b"x" * 6000)
    data = {"subject": "S", "sender": "x", "date": "d", "message_id": "m"}
    tm = TrackingManager(tracking_file=tmp_path / "t.json")
    fp = tm.get_email_fingerprint(data)
    tm.processed_emails[fp] = {"subject": "S", "pdf_path": str(pdf), "success": True, "remarkable_uploaded": False,
                               "processed_date": (datetime.now() - timedelta(days=days_old)).isoformat()}
    return tm, data, fp


def test_recent_pending_with_missing_pdf_is_regathered(tmp_path):
    tm, data, fp = _tm(tmp_path, days_old=2, pdf_exists=False)
    with patch("modules.tracking.PRUNE_NEWS_DAYS", 10, create=True):
        assert tm.is_email_processed(data) is False
    assert fp not in tm.processed_emails


def test_stale_pending_with_missing_pdf_is_marked_expired_not_regathered(tmp_path):
    tm, data, fp = _tm(tmp_path, days_old=20, pdf_exists=False)
    with patch("modules.tracking.PRUNE_NEWS_DAYS", 10, create=True):
        assert tm.is_email_processed(data) is True
    assert tm.processed_emails[fp]["remarkable_expired"] is True
    assert json.loads((tmp_path / "t.json").read_text())[fp]["remarkable_expired"] is True  # persisted


def test_cleanup_marks_stale_missing_pending_expired_instead_of_dropping(tmp_path):
    tm, data, fp = _tm(tmp_path, days_old=20, pdf_exists=False)
    with patch("modules.tracking.PRUNE_NEWS_DAYS", 10, create=True):
        tm.cleanup_tracking_data()
    assert fp in tm.processed_emails and tm.processed_emails[fp]["remarkable_expired"] is True


def test_cleanup_still_drops_recent_missing_pending(tmp_path):
    tm, data, fp = _tm(tmp_path, days_old=2, pdf_exists=False)
    with patch("modules.tracking.PRUNE_NEWS_DAYS", 10, create=True):
        tm.cleanup_tracking_data()
    assert fp not in tm.processed_emails
