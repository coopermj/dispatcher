"""Non-article URL filtering must match path segments, not substrings."""
from unittest.mock import MagicMock, patch


def _scanner():
    from modules.website_scanner import WebsiteScanner
    return WebsiteScanner(MagicMock(), tracking_manager=None)


def _article(url, title="Pentagon Budget Fight"):
    return {'url': url, 'title': title, 'date': None, 'summary': '', 'source': 'homepage'}


def _filter(urls):
    with patch('modules.website_scanner.SKIP_KEYWORDS', []):
        kept = _scanner().filter_articles([_article(u) for u in urls])
    return [a['url'] for a in kept]


def test_slug_containing_tag_author_search_is_kept():
    urls = [
        "https://thedispatch.com/article/pentagon-budget-fight/",
        "https://thedispatch.com/article/heritage-foundation-report/",
        "https://thedispatch.com/article/authoritarian-turn/",
        "https://thedispatch.com/article/new-research-shows/",
        "https://thedispatch.com/newsletter/category-five-storm/",
    ]
    assert _filter(urls) == urls


def test_real_index_pages_are_skipped():
    urls = [
        "https://thedispatch.com/author/jonah-goldberg/",
        "https://thedispatch.com/tag/politics/",
        "https://thedispatch.com/category/newsletters/",
        "https://thedispatch.com/search?q=budget",
        "https://thedispatch.com/search",
    ]
    assert _filter(urls) == []
