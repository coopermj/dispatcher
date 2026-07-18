# LinkProcessor Race Fix + CLI URL Argument Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix a race condition where parallel PDF conversions share a single `LinkProcessor` instance (causing wrong article content in PDFs), and add a `--url` CLI argument that bypasses scanning and converts a specific URL directly.

**Architecture:** Both changes are confined to `main.py`. The race fix removes a shared `LinkProcessor` instance from `DispatchConverter` and creates one per article inside `process_single_item_parallel`. The CLI feature adds `argparse` to `main()`, a new `process_single_url` method, and two new parameters on `process_single_item_parallel` (`force_reprocess`, `effective_mode`).

**Tech Stack:** Python 3, asyncio, argparse (stdlib), pytest + pytest-asyncio + unittest.mock (tests only)

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `main.py` | Modify | All logic changes (race fix + CLI feature) |
| `tests/test_main.py` | Create | Unit tests for pure logic (slug conversion, force_reprocess, effective_mode) |
| `requirements.txt` | Modify | Add `pytest` and `pytest-asyncio` |

---

## Task 1: Add test infrastructure

**Files:**
- Modify: `requirements.txt`
- Create: `tests/__init__.py`
- Create: `tests/test_main.py`

- [ ] **Step 1: Add pytest dependencies to requirements.txt**

Append to the end of `requirements.txt`:
```
# Testing
pytest
pytest-asyncio
```

- [ ] **Step 2: Install the new dependencies**

```bash
cd /Users/micahcooper/PycharmProjects/dispatchweb
source .venv/bin/activate && pip install pytest pytest-asyncio
```
Expected: both packages install successfully.

- [ ] **Step 3: Create tests/__init__.py**

Create an empty file at `tests/__init__.py`.

- [ ] **Step 4: Write failing tests for URL slug conversion**

Create `tests/test_main.py` with:

```python
"""Unit tests for DispatchConverter logic in main.py"""
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime


# ---------------------------------------------------------------------------
# Helpers (no imports from main.py yet — these tests drive the implementation)
# ---------------------------------------------------------------------------

def slug_to_title(url: str) -> str:
    """Extract URL slug and convert to title-case string.

    e.g. https://thedispatch.com/article/neon-genesis-evangelion-american-millennials/
      -> 'Neon Genesis Evangelion American Millennials'
    """
    from urllib.parse import urlparse
    path = urlparse(url).path
    segments = [s for s in path.split('/') if s]
    if not segments:
        return "Article"
    slug = segments[-1]
    return slug.replace('-', ' ').title()


class TestSlugToTitle:
    def test_standard_article_url(self):
        url = "https://thedispatch.com/article/neon-genesis-evangelion-american-millennials/"
        assert slug_to_title(url) == "Neon Genesis Evangelion American Millennials"

    def test_trailing_slash_stripped(self):
        url = "https://thedispatch.com/p/some-article/"
        assert slug_to_title(url) == "Some Article"

    def test_no_trailing_slash(self):
        url = "https://thedispatch.com/article/my-article"
        assert slug_to_title(url) == "My Article"

    def test_root_url_returns_default(self):
        url = "https://thedispatch.com/"
        assert slug_to_title(url) == "Article"
```

- [ ] **Step 5: Run tests to verify they pass (these are pure logic, no implementation needed)**

```bash
cd /Users/micahcooper/PycharmProjects/dispatchweb
source .venv/bin/activate && python -m pytest tests/test_main.py::TestSlugToTitle -v
```
Expected: 4 tests PASS. (These tests exercise a standalone helper — no changes to main.py yet.)

- [ ] **Step 6: Add failing tests for force_reprocess and effective_mode (will fail until Task 2)**

Append to `tests/test_main.py`:

```python
# ---------------------------------------------------------------------------
# Tests for process_single_item_parallel new parameters
# These import from main.py — they will fail until Task 2 is complete.
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_converter():
    """Build a DispatchConverter with all dependencies mocked out."""
    with patch('main.AuthManager'), \
         patch('main.EmailHandler'), \
         patch('main.BrowserManager'), \
         patch('main.TrackingManager'), \
         patch('main.ReMarkableManager'), \
         patch('main.WebsiteScanner'), \
         patch('main.LinkProcessor'), \
         patch('config.settings.OUTPUT_DIR', __import__('pathlib').Path('/tmp/dispatch_test_pdfs')):
        from main import DispatchConverter
        converter = DispatchConverter()
        # Wire up the mocks for easy assertion
        converter.tracking_manager = MagicMock()
        converter.tracking_manager.is_email_processed.return_value = True  # Would skip without force_reprocess
        converter.tracking_manager.get_processed_info.return_value = {'processed_date': '2026-01-01'}
        converter.browser_manager = MagicMock()
        converter.browser_manager.create_new_page = AsyncMock(return_value=MagicMock())
        converter.browser_manager.close_page = AsyncMock()
        converter.browser_manager.convert_url_to_pdf_with_page = AsyncMock(return_value=False)
        converter.stats = {
            'skipped_duplicates': 0,
            'failed_conversions': 0,
            'successful_conversions': 0,
            'remarkable_enabled': False,
            'total_file_size': 0,
            'total_linked_pages': 0,
        }
        converter.processing_mode = 'website'
        converter.output_dir = __import__('pathlib').Path('/tmp/dispatch_test_pdfs')
        yield converter


@pytest.mark.asyncio
async def test_force_reprocess_false_skips_already_processed(mock_converter):
    """Without force_reprocess, already-processed items are skipped."""
    content_data = {
        'subject': 'Test Article',
        'read_online_url': 'https://thedispatch.com/article/test/',
        'message_id': 'test_123',
        'sender': 'test',
        'date': datetime.now().isoformat(),
    }
    mock_converter.tracking_manager.is_email_processed.return_value = True

    result = await mock_converter.process_single_item_parallel(content_data, 1)

    assert result is True
    assert mock_converter.stats['skipped_duplicates'] == 1


@pytest.mark.asyncio
async def test_force_reprocess_true_bypasses_tracking_check(mock_converter):
    """With force_reprocess=True, already-processed items are NOT skipped."""
    content_data = {
        'subject': 'Test Article',
        'read_online_url': 'https://thedispatch.com/article/test/',
        'message_id': 'test_123',
        'sender': 'test',
        'date': datetime.now().isoformat(),
    }
    mock_converter.tracking_manager.is_email_processed.return_value = True

    result = await mock_converter.process_single_item_parallel(
        content_data, 1, force_reprocess=True
    )

    # Should NOT have incremented skipped_duplicates
    assert mock_converter.stats['skipped_duplicates'] == 0
    # tracking check should not have been called
    mock_converter.tracking_manager.is_email_processed.assert_not_called()


@pytest.mark.asyncio
async def test_effective_mode_website_enables_link_following(mock_converter):
    """effective_mode='website' causes link_processor to be instantiated (link following path)."""
    content_data = {
        'subject': 'Test Article',
        'read_online_url': 'https://thedispatch.com/article/test/',
        'message_id': 'test_123',
        'sender': 'test',
        'date': datetime.now().isoformat(),
    }
    mock_converter.processing_mode = 'email'  # Would normally block link following

    with patch('main.FOLLOW_ARTICLE_LINKS', True), \
         patch('main.LinkProcessor') as MockLP:
        mock_lp_instance = MagicMock()
        mock_lp_instance.process_article_with_links = AsyncMock(return_value=True)
        mock_lp_instance.get_processing_summary = MagicMock(return_value={'linked_pages': 0, 'total_pages': 1})
        MockLP.return_value = mock_lp_instance

        await mock_converter.process_single_item_parallel(
            content_data, 1, force_reprocess=True, effective_mode='website'
        )

    # LinkProcessor should have been instantiated (link following ran)
    MockLP.assert_called_once()
    mock_lp_instance.process_article_with_links.assert_called_once()
```

- [ ] **Step 7: Run new tests to confirm they fail (expected — main.py not updated yet)**

```bash
cd /Users/micahcooper/PycharmProjects/dispatchweb
source .venv/bin/activate && python -m pytest tests/test_main.py -v 2>&1 | tail -20
```
Expected: `TestSlugToTitle` passes (4 tests), the 3 new async tests FAIL with errors like `TypeError: process_single_item_parallel() got an unexpected keyword argument 'force_reprocess'`.

- [ ] **Step 8: Commit test infrastructure**

```bash
cd /Users/micahcooper/PycharmProjects/dispatchweb
git add requirements.txt tests/
git commit -m "test: add pytest infrastructure and failing tests for race fix + CLI feature"
```

---

## Task 2: Fix the LinkProcessor race condition

**Files:**
- Modify: `main.py`

The fix is two targeted edits:

1. Remove `self.link_processor = LinkProcessor(self.browser_manager)` from `DispatchConverter.__init__` (around line 41)
2. In `process_single_item_parallel`, add `force_reprocess: bool = False` and `effective_mode: str = None` parameters, replace the tracking check with a guard on `force_reprocess`, replace `self.processing_mode` reads with `mode = effective_mode or self.processing_mode`, and instantiate a local `link_processor` just before the link-following block

- [ ] **Step 1: Remove self.link_processor from __init__**

In `main.py`, find and remove this line (around line 41):
```python
        self.link_processor = LinkProcessor(self.browser_manager)
```

Also remove the `LinkProcessor` import from the `from modules import (...)` line at the top if it's only used there — but keep it if it's used elsewhere. Actually, `LinkProcessor` will still be imported and used as a local variable inside `process_single_item_parallel`, so keep the import.

- [ ] **Step 2: Update process_single_item_parallel signature**

Find the method signature (around line 282):
```python
    async def process_single_item_parallel(self, content_data, index):
```
Replace with:
```python
    async def process_single_item_parallel(self, content_data, index, force_reprocess: bool = False, effective_mode: str = None):
```

- [ ] **Step 3: Add mode local variable at top of process_single_item_parallel**

Immediately after the `try:` and the first `print` line inside `process_single_item_parallel` (around line 286-287), add:
```python
            mode = effective_mode or self.processing_mode
            item_type = "email" if mode == 'email' else "article"
```
And remove the existing `item_type = ...` line that was there.

Also update the `except` clause near the bottom of `process_single_item_parallel` (around line 381), which has its own hardcoded copy:
```python
        except Exception as e:
            item_type = "email" if self.processing_mode == 'email' else "article"
```
Replace the `item_type` line in that `except` block with:
```python
            item_type = "email" if (effective_mode or self.processing_mode) == 'email' else "article"
```

- [ ] **Step 4: Guard the tracking check with force_reprocess**

Find (around line 290):
```python
            # Check if already processed
            if self.tracking_manager.is_email_processed(content_data):
                processed_info = self.tracking_manager.get_processed_info(content_data)
                print(f"⏭️  [{index}] SKIPPED - Already processed on {processed_info.get('processed_date', 'unknown date')}")
                self.stats['skipped_duplicates'] += 1
                return True
```
Replace with:
```python
            # Check if already processed (skip check when force_reprocess=True)
            if not force_reprocess and self.tracking_manager.is_email_processed(content_data):
                processed_info = self.tracking_manager.get_processed_info(content_data)
                print(f"⏭️  [{index}] SKIPPED - Already processed on {processed_info.get('processed_date', 'unknown date')}")
                self.stats['skipped_duplicates'] += 1
                return True
```

- [ ] **Step 5: Update filename prefix to use mode**

Find (around line 310):
```python
                prefix=f"dispatch_{self.processing_mode}"
```
Replace with:
```python
                prefix=f"dispatch_{mode}"
```

- [ ] **Step 6: Replace shared self.link_processor with a local instance and update the mode guard**

Find (around line 317):
```python
            if FOLLOW_ARTICLE_LINKS and self.processing_mode == 'website':
                # Pass the dedicated page to link processor for parallel-safe operation
                success = await self.link_processor.process_article_with_links(
```
Replace with:
```python
            if FOLLOW_ARTICLE_LINKS and mode == 'website':
                # Create a fresh LinkProcessor per article to avoid shared state in parallel runs
                link_processor = LinkProcessor(self.browser_manager)
                success = await link_processor.process_article_with_links(
```

- [ ] **Step 7: Update get_processing_summary call to use local link_processor**

Find the line immediately after (around line 324):
```python
                link_summary = self.link_processor.get_processing_summary()
```
Replace with:
```python
                link_summary = link_processor.get_processing_summary()
```

- [ ] **Step 8: Run tests to verify they now pass**

```bash
cd /Users/micahcooper/PycharmProjects/dispatchweb
source .venv/bin/activate && python -m pytest tests/test_main.py -v
```
Expected: All 7 tests PASS (4 slug tests + 3 async tests).

- [ ] **Step 9: Commit the race fix**

```bash
cd /Users/micahcooper/PycharmProjects/dispatchweb
git add main.py
git commit -m "fix: create LinkProcessor per article to eliminate parallel processing race condition

Previously all parallel conversions shared one LinkProcessor instance, causing
_active_page and other state to be overwritten mid-processing. Each article now
gets an isolated instance. Also adds force_reprocess and effective_mode params
to process_single_item_parallel for use by the upcoming CLI --url feature."
```

---

## Task 3: Add process_single_url method and argparse

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Write failing test for process_single_url**

Append to `tests/test_main.py`:

```python
# ---------------------------------------------------------------------------
# Tests for process_single_url
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_single_url_builds_correct_content_data(mock_converter):
    """process_single_url builds content_data from URL and calls process_single_item_parallel."""
    url = "https://thedispatch.com/article/neon-genesis-evangelion-american-millennials/"

    with patch.object(mock_converter, 'process_single_item_parallel', new_callable=AsyncMock) as mock_psi:
        mock_psi.return_value = True
        await mock_converter.process_single_url(url)

    mock_psi.assert_called_once()
    call_args = mock_psi.call_args
    content_data = call_args[0][0]  # first positional arg

    assert content_data['subject'] == 'Neon Genesis Evangelion American Millennials'
    assert content_data['read_online_url'] == url
    assert content_data['source'] == 'cli'
    assert content_data['sender'] == 'CLI'
    assert content_data['is_html'] is True
    # force_reprocess=True must be passed
    assert call_args[1].get('force_reprocess') is True or call_args[0][2] is True
    # effective_mode='website' must be passed
    assert call_args[1].get('effective_mode') == 'website' or call_args[0][3] == 'website'
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd /Users/micahcooper/PycharmProjects/dispatchweb
source .venv/bin/activate && python -m pytest tests/test_main.py::test_process_single_url_builds_correct_content_data -v
```
Expected: FAIL with `AttributeError: 'DispatchConverter' object has no attribute 'process_single_url'`.

- [ ] **Step 3: Add process_single_url method to DispatchConverter**

Add this method to `DispatchConverter` in `main.py`, after `print_final_summary` and before `cleanup` (around line 520):

```python
    async def process_single_url(self, url):
        """Process a single URL directly, bypassing scanning. Always force-reprocesses."""
        from urllib.parse import urlparse
        # Derive a human-readable title from the URL slug
        path = urlparse(url).path
        segments = [s for s in path.split('/') if s]
        slug = segments[-1] if segments else 'article'
        subject = slug.replace('-', ' ').title()

        content_data = {
            'subject': subject,
            'read_online_url': url,
            'message_id': f"url_{hash(url)}",
            'sender': 'CLI',
            'date': datetime.now().isoformat(),
            'body': '',
            'raw_body': f"<a href='{url}'>{subject}</a>",
            'is_html': True,
            'source': 'cli',
        }

        print(f"\n🔗 Processing URL: {url}")
        print(f"📄 Derived title: {subject}")
        await self.process_single_item_parallel(
            content_data, 1,
            force_reprocess=True,
            effective_mode='website'
        )
```

Make sure `datetime` is already imported at the top of `main.py` — it is (line 8: `import time`, and `from datetime import datetime` if not already present). Check the imports section: if `datetime` isn't imported, add `from datetime import datetime` to the imports block.

- [ ] **Step 4: Run test to confirm it passes**

```bash
cd /Users/micahcooper/PycharmProjects/dispatchweb
source .venv/bin/activate && python -m pytest tests/test_main.py -v
```
Expected: All 8 tests PASS.

- [ ] **Step 5: Add argparse to main() and wire up --url**

In `main()` (around line 525), replace:
```python
async def main():
    """Main function"""
    try:
        # Create converter instance
        converter = DispatchConverter()
```
With:
```python
async def main():
    """Main function"""
    import argparse
    parser = argparse.ArgumentParser(description='The Dispatch PDF Converter')
    parser.add_argument('--url', type=str, default=None,
                        help='Convert a specific URL to PDF directly (skips scanning)')
    args = parser.parse_args()

    try:
        # Create converter instance
        converter = DispatchConverter()
```

Then find (around line 535):
```python
        # Print startup banner
        converter.print_startup_banner()

        # Process content based on mode from .env file
        if converter.processing_mode == 'email':
```
And replace the block from `# Process content based on mode` through the end of the `try` block with:
```python
        # Print startup banner
        converter.print_startup_banner()

        # --url mode: process a single URL directly
        if args.url:
            if not await converter.initialize():
                return
            await converter.process_single_url(args.url)
            converter.print_final_summary()
        # Normal mode: scan and process
        elif converter.processing_mode == 'email':
            await converter.process_content(
                max_items=5,
                force_reprocess=False,
                upload_to_remarkable=True
            )
        else:  # website mode
            await converter.process_content(
                max_items=10,
                force_reprocess=False,
                upload_to_remarkable=True
            )
```

- [ ] **Step 6: Verify main.py syntax is valid**

```bash
cd /Users/micahcooper/PycharmProjects/dispatchweb
source .venv/bin/activate && python -c "import main; print('OK')"
```
Expected: `OK` (plus any config print output). No SyntaxError or ImportError.

- [ ] **Step 7: Run full test suite**

```bash
cd /Users/micahcooper/PycharmProjects/dispatchweb
source .venv/bin/activate && python -m pytest tests/test_main.py -v
```
Expected: All 8 tests PASS.

- [ ] **Step 8: Commit**

```bash
cd /Users/micahcooper/PycharmProjects/dispatchweb
git add main.py tests/test_main.py
git commit -m "feat: add --url CLI argument to convert a specific article URL to PDF

Usage: python main.py --url https://thedispatch.com/article/my-article/
Bypasses website/email scanning, derives title from URL slug, always skips
duplicate check (force_reprocess=True), respects all other .env settings."
```

---

## Task 4: Manual smoke test

The browser-dependent conversion path can't be unit tested without a live browser session. Verify the full flow manually.

- [ ] **Step 1: Verify --help works**

```bash
cd /Users/micahcooper/PycharmProjects/dispatchweb
source .venv/bin/activate && python main.py --help
```
Expected output includes:
```
usage: main.py [-h] [--url URL]
...
  --url URL  Convert a specific URL to PDF directly (skips scanning)
```

- [ ] **Step 2: Verify --url flag is accepted without crashing at parse time**

```bash
cd /Users/micahcooper/PycharmProjects/dispatchweb
source .venv/bin/activate && python -c "
import asyncio, sys
sys.argv = ['main.py', '--url', 'https://thedispatch.com/article/neon-genesis-evangelion-american-millennials/']
import argparse
parser = argparse.ArgumentParser()
parser.add_argument('--url', type=str, default=None)
args = parser.parse_args()
print('Parsed URL:', args.url)
"
```
Expected: `Parsed URL: https://thedispatch.com/article/neon-genesis-evangelion-american-millennials/`

- [ ] **Step 3: (Optional, requires browser) Run against the Neon Genesis article**

```bash
cd /Users/micahcooper/PycharmProjects/dispatchweb
source .venv/bin/activate && python main.py --url "https://thedispatch.com/article/neon-genesis-evangelion-american-millennials/"
```
Expected: PDF created in `dispatch_pdfs/` with name starting `dispatch_website_001_Neon_Genesis...`. Open the PDF and verify the content matches the article.

---

## Completion Checklist

- [ ] All 8 unit tests pass: `python -m pytest tests/test_main.py -v`
- [ ] `python main.py --help` shows `--url` option
- [ ] No `self.link_processor` attribute on `DispatchConverter` (race condition removed)
- [ ] `process_single_item_parallel` has `force_reprocess` and `effective_mode` parameters
- [ ] `process_single_url` method exists on `DispatchConverter`
