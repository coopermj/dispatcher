"""Tests for remediate_news.py target selection + device->URL matching."""
from datetime import datetime, timezone


def _doc(name, starred=False, modified="2026-06-11T00:00:00Z", type_="DocumentType"):
    return {"name": name, "starred": starred, "modifiedClient": modified, "type": type_}


NOW = datetime(2026, 6, 11, tzinfo=timezone.utc)


def test_select_targets_starred_and_recent_only():
    from remediate_news import select_targets
    entries = [
        _doc("dispatch_website_001_Recent-Article", modified="2026-06-10T00:00:00Z"),   # recent -> in
        _doc("dispatch_website_002_Old-Article", modified="2026-04-01T00:00:00Z"),       # old unstarred -> out
        _doc("dispatch_website_003_Starred-Old", starred=True, modified="2026-01-01T00:00:00Z"),  # starred -> in
        _doc("dispatch_001_Email-Sourced", modified="2026-06-10T00:00:00Z"),             # not website -> out
        _doc("Some Folder", type_="CollectionType"),                                      # folder -> out
    ]
    got = {e["name"] for e in select_targets(entries, days=14, now=NOW)}
    assert got == {"dispatch_website_001_Recent-Article", "dispatch_website_003_Starred-Old"}


def test_url_for_matches_via_pdf_path_stem():
    from remediate_news import url_for
    tracking = {
        "fp": {"subject": "Whose Privacy", "read_online_url": "https://thedispatch.com/article/privacy/",
               "pdf_path": "/x/dispatch_pdfs/dispatch_website_002_Whose-Privacy.pdf"},
    }
    # device name (no .pdf) should still match the tracking pdf_path stem
    assert url_for("dispatch_website_002_Whose-Privacy", tracking) == "https://thedispatch.com/article/privacy/"
    # and with a .pdf suffix on the device name
    assert url_for("dispatch_website_002_Whose-Privacy.pdf", tracking) == "https://thedispatch.com/article/privacy/"
    # unknown -> empty
    assert url_for("dispatch_website_999_Unknown", tracking) == ""
