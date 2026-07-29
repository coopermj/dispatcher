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


def test_post_type_wrapper_class_does_not_disable_link_following():
    """Regression: CMS wrapper classes like post-type-newsletter on <main>
    must not trip the 'newsletter' excluded-region token (that bug silently
    disabled link-following for ALL newsletter articles). Genuine
    newsletter widgets must still be excluded."""
    NEWSLETTER_SAMPLE = """
<html><body>
  <main class="post-type-newsletter single-format-standard">
    <article class="article-content">
      <p>As we covered in <a href="/p/cited-in-newsletter-body-piece">an earlier analysis worth reading</a>,
         the situation continues to develop in interesting ways.</p>
      <div class="newsletter-signup">
        <a href="/p/subscribe-widget-target-page">Subscribe to This Great Newsletter Today</a>
      </div>
    </article>
  </main>
</body></html>
"""
    from modules.link_processor import LinkProcessor
    lp = LinkProcessor(browser_manager=None)
    links = lp.extract_links(BeautifulSoup(NEWSLETTER_SAMPLE, "html.parser"),
                             "https://thedispatch.com/newsletter/morning/some-article/")
    urls = " ".join(l["url"] for l in links)
    assert "cited-in-newsletter-body-piece" in urls   # body citation: kept
    assert "subscribe-widget-target-page" not in urls  # signup widget: excluded


def test_max_linked_pages_is_bounded_global_cap():
    """MAX_LINKED_PAGES is the global cap across all depth levels (spec
    2026-05-29). User raised it to 40 (2026-07-28) to capture effectively
    every citation; the bound only guards against a runaway misconfig."""
    from config.settings import MAX_LINKED_PAGES
    assert 1 <= MAX_LINKED_PAGES <= 50


def test_processing_summary_never_negative():
    """Regression: early-return paths used to leave the page map empty,
    making get_processing_summary() report linked_pages == -1."""
    from modules.link_processor import LinkProcessor
    lp = LinkProcessor(browser_manager=None)
    summary = lp.get_processing_summary()
    assert summary['linked_pages'] >= 0
