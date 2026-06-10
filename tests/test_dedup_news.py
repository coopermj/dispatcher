"""Tests for dedup_news.py — selecting duplicate reMarkable docs to delete."""


def _doc(name, starred=False, modified="2026-01-01T00:00:00Z", type_="DocumentType"):
    return {"name": name, "starred": starred, "modifiedClient": modified, "type": type_}


def test_distinct_titles_delete_nothing():
    from dedup_news import select_duplicates_to_delete
    entries = [_doc("dispatch_001_Article-One"), _doc("dispatch_001_Article-Two")]
    assert select_duplicates_to_delete(entries) == []


def test_unstarred_duplicates_keep_newest_delete_rest():
    from dedup_news import select_duplicates_to_delete
    older = _doc("dispatch_001_Same-Title", modified="2026-01-01T00:00:00Z")
    newer = _doc("dispatch_website_005_Same-Title", modified="2026-03-01T00:00:00Z")
    to_delete = select_duplicates_to_delete([older, newer])
    names = [e["name"] for e in to_delete]
    assert names == ["dispatch_001_Same-Title"]  # older deleted, newest kept


def test_starred_copy_is_never_deleted():
    from dedup_news import select_duplicates_to_delete
    starred = _doc("dispatch_001_Keep-Me", starred=True)
    dup1 = _doc("dispatch_website_003_Keep-Me", modified="2026-05-01T00:00:00Z")
    dup2 = _doc("dispatch_website_009_Keep-Me", modified="2026-06-01T00:00:00Z")
    to_delete = select_duplicates_to_delete([starred, dup1, dup2])
    names = {e["name"] for e in to_delete}
    # both unstarred copies removed; the starred one survives
    assert names == {"dispatch_website_003_Keep-Me", "dispatch_website_009_Keep-Me"}


def test_prefix_and_extension_collapse_into_one_group():
    from dedup_news import select_duplicates_to_delete
    a = _doc("dispatch_001_Chinas-AI-Embrace.pdf", modified="2026-01-01T00:00:00Z")
    b = _doc("dispatch_website_006_Chinas-AI-Embrace", modified="2026-02-01T00:00:00Z")
    to_delete = select_duplicates_to_delete([a, b])
    assert len(to_delete) == 1  # treated as the same article despite prefix/.pdf


def test_folders_are_ignored():
    from dedup_news import select_duplicates_to_delete
    entries = [
        _doc("Some Folder", type_="CollectionType"),
        _doc("Another Folder", type_="CollectionType"),
    ]
    assert select_duplicates_to_delete(entries) == []
