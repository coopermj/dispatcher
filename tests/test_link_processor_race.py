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
