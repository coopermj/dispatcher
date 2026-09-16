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


# ---- local PDF cleanup ----

def test_local_prune_deletes_old_uploaded_pdfs(tmp_path):
    import os, time
    from prune_news import select_local_prunable
    old = tmp_path / "dispatch_001_Old.pdf"
    old.write_bytes(b"x")
    eleven_days = 11 * 86400
    os.utime(old, (time.time() - eleven_days, time.time() - eleven_days))
    tracking = {str(old): {"remarkable_uploaded": True}}
    assert select_local_prunable([old], tracking, days=10) == [old]


def test_local_prune_keeps_recent_and_pending(tmp_path):
    import os, time
    from prune_news import select_local_prunable
    recent = tmp_path / "dispatch_002_Recent.pdf"     # uploaded but young
    recent.write_bytes(b"x")
    pending = tmp_path / "dispatch_003_Pending.pdf"   # old but NOT uploaded
    pending.write_bytes(b"x")
    old = time.time() - 11 * 86400
    os.utime(pending, (old, old))
    tracking = {str(recent): {"remarkable_uploaded": True},
                str(pending): {"remarkable_uploaded": False}}
    assert select_local_prunable([recent, pending], tracking, days=10) == []


def test_local_prune_expired_and_orphans_go(tmp_path):
    import os, time
    from prune_news import select_local_prunable
    expired = tmp_path / "dispatch_004_Expired.pdf"   # pruned from device
    expired.write_bytes(b"x")
    orphan = tmp_path / "dispatch_005_Orphan.pdf"     # no tracking entry
    orphan.write_bytes(b"x")
    old = time.time() - 11 * 86400
    for f in (expired, orphan):
        os.utime(f, (old, old))
    tracking = {str(expired): {"remarkable_uploaded": False,
                               "remarkable_expired": True}}
    got = select_local_prunable([expired, orphan], tracking, days=10)
    assert sorted(p.name for p in got) == ["dispatch_004_Expired.pdf",
                                           "dispatch_005_Orphan.pdf"]


# ---------------------------------------------------------------------------
# Debug artefact hygiene: debug_html/ snapshots and temp_pdfs/ leftovers
# ---------------------------------------------------------------------------

def _age(path, days):
    import os, time
    t = time.time() - days * 86400
    os.utime(path, (t, t))


def test_debug_prune_removes_old_snapshots_and_temp_dirs_only(tmp_path):
    from prune_news import run_debug_prune
    old_html = tmp_path / "page_20260401_000000_before_cleanup.html"; old_html.write_text("x"); _age(old_html, 30)
    new_html = tmp_path / "page_20260916_000000_before_cleanup.html"; new_html.write_text("x")
    tmp = tmp_path / "temp_pdfs"
    old_dir = tmp / "old_article"; old_dir.mkdir(parents=True); (old_dir / "page_1_main.pdf").write_bytes(b"x"); _age(old_dir, 30)
    new_dir = tmp / "new_article"; new_dir.mkdir(); (new_dir / "page_1_main.pdf").write_bytes(b"x")

    removed = run_debug_prune(days=10, debug_dir=tmp_path)

    assert removed == 2
    assert not old_html.exists() and new_html.exists()
    assert not old_dir.exists() and new_dir.exists()


def test_debug_prune_missing_dir_is_noop(tmp_path):
    from prune_news import run_debug_prune
    assert run_debug_prune(days=10, debug_dir=tmp_path / "nope") == 0
