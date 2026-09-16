"""MIN_PDF_SIZE_BYTES must govern every 'is this PDF real' check, not just dead code."""
from unittest.mock import AsyncMock, MagicMock, patch


async def test_browser_manager_uses_configured_min_pdf_size(tmp_path):
    from modules.browser_manager import BrowserManager
    bm = BrowserManager()
    out = tmp_path / "out.pdf"
    page = AsyncMock()
    page.url = "about:blank"

    async def fake_pdf(path, **kwargs):
        with open(path, "wb") as f:
            f.write(b"x" * 8000)  # above the old hardcoded 5000, below the configured floor
    page.pdf = fake_pdf

    with patch('modules.browser_manager.MIN_PDF_SIZE_BYTES', 20000), \
         patch('modules.browser_manager.asyncio.sleep', new_callable=AsyncMock):
        bm.save_html_snapshot_from_page = AsyncMock()
        bm.remove_header_elements_from_page = AsyncMock()
        ok = await bm.convert_url_to_pdf_with_page("https://thedispatch.com/article/x/", str(out), page)

    assert ok is False


def test_tracking_uses_configured_min_pdf_size(tmp_path):
    from modules.tracking import TrackingManager
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"x" * 8000)
    tm = TrackingManager(tracking_file=tmp_path / "t.json")
    with patch('modules.tracking.MIN_PDF_SIZE_BYTES', 20000, create=True):
        ok = tm.mark_email_processed({'subject': 's', 'message_id': 'm'}, str(pdf))
    assert ok is False
