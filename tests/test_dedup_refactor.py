"""Tests for #6 refactor: configurable tracking path, success backfill,
and live reMarkable inventory dedup."""
import json
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# TrackingManager: configurable tracking-file path
# ---------------------------------------------------------------------------

def test_tracking_manager_default_file_unchanged():
    from modules.tracking import TrackingManager
    from config.settings import TRACKING_FILE
    tm = TrackingManager()
    assert tm.tracking_file == TRACKING_FILE


def test_tracking_manager_uses_custom_file(tmp_path):
    from modules.tracking import TrackingManager
    custom = tmp_path / "email_tracking.json"
    custom.write_text(json.dumps({
        "fp1": {"subject": "A", "remarkable_uploaded": True, "success": True}
    }))
    tm = TrackingManager(tracking_file=custom)
    assert tm.tracking_file == custom
    assert "fp1" in tm.processed_emails


def test_custom_file_save_roundtrip(tmp_path):
    from modules.tracking import TrackingManager
    custom = tmp_path / "email_tracking.json"
    tm = TrackingManager(tracking_file=custom)
    tm.processed_emails["fpX"] = {"subject": "Z", "success": True}
    tm.save_tracking_data()
    assert json.loads(custom.read_text())["fpX"]["subject"] == "Z"


# ---------------------------------------------------------------------------
# TrackingManager: one-time `success` backfill for legacy entries
# ---------------------------------------------------------------------------

def test_backfill_success_on_load(tmp_path):
    """Legacy entries without `success` are treated as success=True after load."""
    from modules.tracking import TrackingManager
    custom = tmp_path / "email_tracking.json"
    custom.write_text(json.dumps({
        "fp_legacy": {  # no `success` field — old email-pipeline schema
            "subject": "Legacy Article",
            "remarkable_uploaded": True,
            "pdf_path": "/nonexistent/x.pdf",
        }
    }))
    tm = TrackingManager(tracking_file=custom)
    assert tm.processed_emails["fp_legacy"]["success"] is True


def test_backfill_preserves_existing_success_false(tmp_path):
    """An explicit success=False is not overwritten by the backfill."""
    from modules.tracking import TrackingManager
    custom = tmp_path / "email_tracking.json"
    custom.write_text(json.dumps({
        "fp_failed": {"subject": "Bad", "success": False}
    }))
    tm = TrackingManager(tracking_file=custom)
    assert tm.processed_emails["fp_failed"]["success"] is False


# ---------------------------------------------------------------------------
# ReMarkableManager: live inventory dedup
# ---------------------------------------------------------------------------

def _make_manager():
    """Construct a ReMarkableManager without touching rmapi during __init__."""
    from modules.remarkable import ReMarkableManager
    with patch("modules.remarkable.os.path.exists", return_value=True), \
         patch("modules.remarkable.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mgr = ReMarkableManager(rmapi_path="/fake/rmapi")
    return mgr


def test_refresh_inventory_parses_json_and_strips_prefix():
    mgr = _make_manager()
    fake = json.dumps([
        {"name": "dispatch_012_Morning-Dispatch", "type": "DocumentType"},
        {"name": "dispatch_website_003_Some-Great-Article", "type": "DocumentType"},
        {"name": "Newsletters", "type": "CollectionType"},  # folder, ignored
    ])
    with patch("modules.remarkable.subprocess.run") as m:
        m.return_value = MagicMock(returncode=0, stdout=fake, stderr="")
        mgr.refresh_inventory("News")

    # Matches on normalized title, ignoring the dispatch_NNN_ filename prefix
    assert mgr.document_exists("Morning Dispatch") is True
    assert mgr.document_exists("Some Great Article!") is True
    # Folder name is not a document; unrelated title absent
    assert mgr.document_exists("Newsletters") is False
    assert mgr.document_exists("Unrelated Headline") is False


def test_refresh_inventory_fails_open_on_error():
    mgr = _make_manager()
    with patch("modules.remarkable.subprocess.run") as m:
        m.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
        mgr.refresh_inventory("News")
    assert mgr._inventory == set()
    # Never block an upload because the inventory check broke
    assert mgr.document_exists("Anything") is False


def test_document_exists_lazy_refreshes_once():
    mgr = _make_manager()
    assert mgr._inventory is None  # not fetched yet
    fake = json.dumps([{"name": "dispatch_001_Hello-World", "type": "DocumentType"}])
    with patch("modules.remarkable.subprocess.run") as m:
        m.return_value = MagicMock(returncode=0, stdout=fake, stderr="")
        first = mgr.document_exists("Hello World")
        again = mgr.document_exists("Hello World")
    assert first is True and again is True
    # subprocess called once (cached after first refresh)
    assert m.call_count == 1
