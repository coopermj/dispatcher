"""Selection logic for automatic News-folder pruning (unstarred, older than N days)."""
from datetime import datetime, timezone

from prune_news import select_prunable

NOW = datetime(2026, 7, 29, 12, 0, 0, tzinfo=timezone.utc)


def doc(name, modified, starred=False):
    return {"type": "DocumentType", "name": name,
            "modifiedClient": modified, "starred": starred}


def test_old_unstarred_is_pruned():
    docs = [doc("old-news", "2026-07-01T08:00:00Z")]
    starred, kept_recent, prunable = select_prunable(docs, days=10, now=NOW)
    assert [e["name"] for e in prunable] == ["old-news"]
    assert kept_recent == 0 and starred == []


def test_recent_unstarred_is_kept():
    docs = [doc("fresh-news", "2026-07-25T08:00:00Z")]
    starred, kept_recent, prunable = select_prunable(docs, days=10, now=NOW)
    assert prunable == [] and kept_recent == 1


def test_starred_is_kept_regardless_of_age():
    docs = [doc("keeper", "2025-01-01T08:00:00Z", starred=True)]
    starred, kept_recent, prunable = select_prunable(docs, days=10, now=NOW)
    assert prunable == [] and [e["name"] for e in starred] == ["keeper"]


def test_unparseable_timestamp_is_pruned():
    """Docs with missing/garbled modifiedClient can't be proven recent — prune."""
    docs = [doc("mystery", "not-a-date")]
    _, _, prunable = select_prunable(docs, days=10, now=NOW)
    assert [e["name"] for e in prunable] == ["mystery"]


def test_exact_boundary_is_pruned():
    """A doc modified exactly `days` ago is not newer than the cutoff — prune."""
    docs = [doc("boundary", "2026-07-19T12:00:00Z")]
    _, _, prunable = select_prunable(docs, days=10, now=NOW)
    assert [e["name"] for e in prunable] == ["boundary"]


def test_auto_prune_scope_is_pipeline_docs_only():
    """Automatic pruning must not touch manually-added files (WSJ papers,
    Atlantic articles) — only pipeline-generated dispatch_* docs qualify."""
    from prune_news import is_pipeline_doc
    assert is_pipeline_doc({"name": "dispatch_001_The-Worst-Lady"})
    assert is_pipeline_doc({"name": "dispatch_website_015_Some-Article"})
    assert not is_pipeline_doc({"name": "tB7eMT-WSJNewsPaper-4-1-2026.pdf"})
    assert not is_pipeline_doc({"name": "I Found It - The Atlantic.pdf"})
    assert not is_pipeline_doc({"name": ""})


def _tracker_with(tmp_path, entry):
    """TrackingManager backed by a temp file holding one entry."""
    import json
    from modules.tracking import TrackingManager
    f = tmp_path / "tracking.json"
    f.write_text(json.dumps({"fp1": entry}))
    return TrackingManager(tracking_file=str(f))


def test_expired_url_is_never_regathered(tmp_path):
    """A pruned (remarkable_expired) article must count as processed even
    though its device copy is gone and its local PDF may be missing."""
    tm = _tracker_with(tmp_path, {
        "read_online_url": "https://thedispatch.com/article/pruned-piece/",
        "remarkable_expired": True,
        "remarkable_uploaded": False,
        "success": False,
        "pdf_path": "/nonexistent/pruned.pdf",
    })
    assert tm.is_url_processed("https://thedispatch.com/article/pruned-piece/")
    assert "https://thedispatch.com/article/pruned-piece/" in tm.get_processed_urls()


def test_cleanup_keeps_expired_entries(tmp_path):
    """cleanup_tracking_data must not drop expired entries (dropping one
    would make the article look new and get re-downloaded)."""
    tm = _tracker_with(tmp_path, {
        "read_online_url": "https://thedispatch.com/article/pruned-piece/",
        "remarkable_expired": True,
        "success": False,
        "pdf_path": "/nonexistent/pruned.pdf",
    })
    tm.cleanup_tracking_data()
    assert "fp1" in tm.processed_emails
