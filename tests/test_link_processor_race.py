"""Regression test for the parallel content-bleed bug: concurrent LinkProcessor
conversions shared a fixed temp path (debug_html/temp_pdfs/page_1_main.pdf), so
one article's main PDF clobbered another's. Temp paths must be unique per output."""
from pathlib import Path

from config.settings import DEBUG_DIR


def test_temp_dir_unique_per_output_filename():
    from modules.link_processor import LinkProcessor
    a = LinkProcessor._temp_dir_for("dispatch_pdfs/dispatch_website_002_Whose-Privacy.pdf")
    b = LinkProcessor._temp_dir_for("dispatch_pdfs/dispatch_website_003_Groupthink.pdf")
    # Different articles (the concurrent case) must not share a temp dir
    assert a != b
    # Lives under the temp_pdfs area
    assert str(a).startswith(str(Path(DEBUG_DIR) / "temp_pdfs"))
    # Deterministic for the same output
    assert a == LinkProcessor._temp_dir_for("other_dir/dispatch_website_002_Whose-Privacy.pdf") \
        or a == LinkProcessor._temp_dir_for("dispatch_pdfs/dispatch_website_002_Whose-Privacy.pdf")


def test_main_pdf_path_differs_across_concurrent_conversions():
    from modules.link_processor import LinkProcessor
    a = LinkProcessor._temp_dir_for("o/A.pdf") / "page_1_main.pdf"
    b = LinkProcessor._temp_dir_for("o/B.pdf") / "page_1_main.pdf"
    # The exact path that previously collided must now be distinct per article
    assert a != b


async def test_temp_dir_removed_when_main_pdf_fails(tmp_path):
    """The fallback path (main PDF too small) must not leave temp_pdfs/<stem>/ behind.
    1,889 such directories had accumulated from this path."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from modules.link_processor import LinkProcessor

    bm = MagicMock()
    bm.navigate_to_url_with_page = AsyncMock(return_value=True)
    bm.save_html_snapshot_from_page = AsyncMock()
    bm.remove_header_elements_from_page = AsyncMock()
    bm.convert_url_to_pdf_with_page = AsyncMock(return_value=True)
    page = AsyncMock()
    page.content = AsyncMock(return_value="<html><body><p>no links</p></body></html>")
    page.pdf = AsyncMock()  # writes nothing -> main PDF "too small" -> fallback

    out = tmp_path / "dispatch_website_001_X.pdf"
    with patch('modules.link_processor.DEBUG_DIR', tmp_path), \
         patch('modules.link_processor.FOLLOW_ARTICLE_LINKS', True), \
         patch('modules.link_processor.asyncio.sleep', new_callable=AsyncMock):
        ok = await LinkProcessor(bm).process_article_with_links(
            "https://thedispatch.com/article/x/", str(out), page=page)

    assert ok is True
    assert not (tmp_path / "temp_pdfs" / out.stem).exists()


def test_extract_links_searches_class_based_content_areas():
    """find_all() was being handed CSS selectors; content in .entry-content must be searched."""
    from unittest.mock import MagicMock
    from bs4 import BeautifulSoup
    from modules.link_processor import LinkProcessor

    html = """<html><body>
      <div class="hero"><a href="/article/hero-teaser-outside-body-slug/">Hero teaser</a></div>
      <div class="entry-content"><p>See <a href="/article/a-long-descriptive-slug/">the analysis</a>.</p></div>
    </body></html>"""
    links = LinkProcessor(MagicMock()).extract_links(BeautifulSoup(html, 'html.parser'),
                                                     "https://thedispatch.com/article/main/")
    assert [l['url'] for l in links] == ["https://thedispatch.com/article/a-long-descriptive-slug/"]
