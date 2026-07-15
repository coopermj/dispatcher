"""Link-following should keep genuine in-text citations but exclude
related/recommended/comments widgets (the cause of 80-page PDFs)."""
from bs4 import BeautifulSoup


SAMPLE = """
<html><body>
  <article class="article-content">
    <p>As I noted in <a href="/p/cited-tariffs-analysis-piece">my earlier analysis of tariffs</a>
       last week, the policy backfires in several ways worth examining.</p>
    <section class="related-articles">
      <a href="/p/related-suggested-one">Some Related Suggested Article Title</a>
    </section>
    <div class="comments">
      <a href="/p/comment-linked-thing">A Commenter Linked Article Title</a>
    </div>
  </article>
  <aside class="more-from-the-dispatch">
    <a href="/p/popular-trending-piece">A Trending Popular Article Title</a>
  </aside>
</body></html>
"""


def test_extract_links_keeps_in_text_excludes_widgets():
    from modules.link_processor import LinkProcessor
    lp = LinkProcessor(browser_manager=None)
    links = lp.extract_links(BeautifulSoup(SAMPLE, "html.parser"),
                             "https://thedispatch.com/article/main/")
    urls = " ".join(l["url"] for l in links)

    assert "cited-tariffs-analysis-piece" in urls   # genuine in-text citation: kept
    assert "related-suggested-one" not in urls       # related widget: excluded
    assert "comment-linked-thing" not in urls        # comments: excluded
    assert "popular-trending-piece" not in urls      # more-from aside: excluded


def test_max_linked_pages_is_bounded_global_cap():
    """MAX_LINKED_PAGES is the global cap across all depth levels (spec
    2026-05-29): big enough to be useful, small enough to keep PDFs sane."""
    from config.settings import MAX_LINKED_PAGES
    assert 1 <= MAX_LINKED_PAGES <= 15
