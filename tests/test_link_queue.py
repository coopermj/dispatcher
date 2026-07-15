"""BFS worklist: dedup by normalized URL, global budget, depth limit, BFS order."""
from modules.link_queue import LinkQueue, normalize_url


def test_normalize_strips_query_fragment_slash():
    assert normalize_url("https://a.com/x/?utm=1#frag") == "https://a.com/x"
    assert normalize_url("https://a.com/x") == "https://a.com/x"


def test_dedup_by_normalized_url():
    q = LinkQueue(max_depth=2, budget=10)
    assert q.add("https://a.com/x", 1) is True
    assert q.add("https://a.com/x/?utm=2", 1) is False
    assert q.accepted == 1


def test_excluded_urls_never_enqueued():
    q = LinkQueue(max_depth=2, budget=10, exclude_urls=["https://a.com/main/"])
    assert q.add("https://a.com/main", 1) is False


def test_budget_is_global_across_levels():
    q = LinkQueue(max_depth=3, budget=2)
    assert q.add("https://a.com/1", 1) is True
    assert q.add("https://a.com/2", 2) is True
    assert q.add("https://a.com/3", 1) is False  # budget spent


def test_depth_beyond_max_rejected():
    q = LinkQueue(max_depth=1, budget=10)
    assert q.add("https://a.com/deep", 2) is False


def test_pop_level_returns_shallowest_first():
    q = LinkQueue(max_depth=2, budget=10)
    q.add("https://a.com/l1a", 1)
    q.add("https://a.com/l2", 2, parent_url="https://a.com/l1a")
    q.add("https://a.com/l1b", 1)
    level1 = q.pop_level()
    assert [t.url for t in level1] == ["https://a.com/l1a", "https://a.com/l1b"]
    level2 = q.pop_level()
    assert [t.url for t in level2] == ["https://a.com/l2"]
    assert level2[0].parent_url == "https://a.com/l1a"
    assert not q.pending()
    assert q.pop_level() == []
