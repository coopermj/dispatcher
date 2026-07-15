# Multi-Level Link Following Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Follow HTTP links in converted articles to a configurable depth (default 1), append linked pages (or placeholders for blocked pages) at the end of the PDF, and rewrite in-text links as internal PDF jumps.

**Architecture:** A pure BFS worklist (`LinkQueue`) enforces dedup, depth, and a global page budget. `LinkProcessor` drains the queue level by level, capturing each page on its own Playwright page (extracting that page's links for the next level while the DOM is loaded). Blocked/timed-out pages get a generated placeholder PDF page. A rewritten merge pass computes per-section start pages, builds a nested bookmark outline, and converts URI link annotations to internal GoTo destinations.

**Tech Stack:** Python 3.14 (venv at `.venv/`), Playwright (async), BeautifulSoup4, PyPDF2, pytest.

**Spec:** `docs/superpowers/specs/2026-05-29-multi-level-link-following-design.md`

## Global Constraints

- All commands run from repo root `/Users/micahcooper/PycharmProjects/dispatchweb` using the venv: `.venv/bin/python`, `.venv/bin/python -m pytest`.
- Config defaults (from spec): `FOLLOW_ARTICLE_LINKS=True`, `LINK_FOLLOW_DEPTH=1`, `MAX_LINKED_PAGES=10` (global cap across all levels, placeholders count), `LINKED_PAGE_TIMEOUT=15` seconds (wired into linked-page navigation).
- Public surface that must keep working: `LinkProcessor.process_article_with_links(article_url, output_filename, page=None)` and `LinkProcessor.get_processing_summary()` (called from `main.py:340-346`).
- Existing tests must stay green: `.venv/bin/python -m pytest tests/ -v` (note: `tests/test_main.py` may require Google modules — venv already has them).
- PDF sections order: main article first, then captured sections in BFS order (all level-1, then level-2, …).

---

### Task 1: `LinkQueue` — pure BFS worklist

**Files:**
- Create: `modules/link_queue.py`
- Test: `tests/test_link_queue.py`

**Interfaces:**
- Consumes: nothing (stdlib only).
- Produces:
  - `normalize_url(url: str) -> str` — strips query, fragment, trailing slash.
  - `LinkTarget` dataclass: fields `url: str`, `depth: int`, `link_text: str = ''`, `parent_url: str | None = None`.
  - `LinkQueue(max_depth: int, budget: int, exclude_urls: iterable = ())` with:
    - `.add(url, depth, link_text='', parent_url=None) -> bool` — False if duplicate/over-depth/over-budget.
    - `.pending() -> bool`
    - `.pop_level() -> list[LinkTarget]` — drains all entries at the shallowest pending depth.
    - `.accepted: int` — count of accepted entries (budget spent).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_link_queue.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_link_queue.py -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'modules.link_queue'`

- [ ] **Step 3: Write the implementation**

```python
# modules/link_queue.py
#!/usr/bin/env python3
"""BFS worklist for multi-level link following: dedup, depth, and budget."""

from dataclasses import dataclass
from typing import Optional


def normalize_url(url):
    """Strip query string, fragment, and trailing slash for comparison."""
    return url.split('?')[0].split('#')[0].rstrip('/')


@dataclass
class LinkTarget:
    url: str
    depth: int
    link_text: str = ''
    parent_url: Optional[str] = None


class LinkQueue:
    """FIFO worklist for BFS link crawling.

    add() enforces dedup (by normalized URL), the max depth, and a global
    budget on total accepted entries — the budget is what guarantees the
    final PDF can't explode at depth 2+. pop_level() drains all pending
    entries at the shallowest depth, so level-1 links are always processed
    (and consume budget) before any level-2 link.
    """

    def __init__(self, max_depth, budget, exclude_urls=()):
        self.max_depth = max_depth
        self.budget = budget
        self.accepted = 0
        self._seen = {normalize_url(u) for u in exclude_urls}
        self._pending = []

    def add(self, url, depth, link_text='', parent_url=None):
        norm = normalize_url(url)
        if depth > self.max_depth:
            return False
        if self.accepted >= self.budget:
            return False
        if norm in self._seen:
            return False
        self._seen.add(norm)
        self.accepted += 1
        self._pending.append(LinkTarget(url, depth, link_text, parent_url))
        return True

    def pending(self):
        return bool(self._pending)

    def pop_level(self):
        if not self._pending:
            return []
        depth = min(t.depth for t in self._pending)
        level = [t for t in self._pending if t.depth == depth]
        self._pending = [t for t in self._pending if t.depth != depth]
        return level
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_link_queue.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add modules/link_queue.py tests/test_link_queue.py
git commit -m "feat: add LinkQueue BFS worklist (dedup, depth, global budget)"
```

---

### Task 2: Blocked-page detection and placeholder HTML

**Files:**
- Create: `modules/blocked_detection.py`
- Test: `tests/test_blocked_detection.py`

**Interfaces:**
- Consumes: nothing (stdlib only).
- Produces:
  - `detect_block(title: str, body_text: str, status: int | None = None) -> str | None` — human-readable reason if the page looks blocked (HTTP ≥400 or challenge marker), else `None`.
  - `placeholder_html(url: str, title: str, reason: str) -> str` — full HTML document for the placeholder page.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_blocked_detection.py
"""Captcha/blocked-page detection against canned inputs (spec: Blocked-page handling)."""
from modules.blocked_detection import detect_block, placeholder_html


def test_http_error_status_is_blocked():
    assert detect_block("Any Title", "any body", status=403) == "HTTP 403"
    assert detect_block("Any Title", "any body", status=429) == "HTTP 429"


def test_cloudflare_challenge_title_detected():
    reason = detect_block("Just a moment...", "Checking your browser", status=200)
    assert reason is not None and "just a moment" in reason


def test_captcha_in_body_detected():
    reason = detect_block("News Site", "Please complete the CAPTCHA to continue")
    assert reason is not None and "captcha" in reason


def test_verify_human_detected():
    assert detect_block("Site", "Verify you are human by completing the action") is not None


def test_clean_page_not_blocked():
    assert detect_block("Tariffs Analysis - The Dispatch",
                        "President's new tariff policy takes effect...",
                        status=200) is None


def test_none_inputs_do_not_crash():
    assert detect_block(None, None) is None


def test_placeholder_contains_url_title_reason_escaped():
    out = placeholder_html("https://x.com/a?b=1&c=2", "A <Title>", "HTTP 403")
    assert "HTTP 403" in out
    assert "A &lt;Title&gt;" in out
    assert "b=1&amp;c=2" in out
    assert "Could not capture" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_blocked_detection.py -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'modules.blocked_detection'`

- [ ] **Step 3: Write the implementation**

```python
# modules/blocked_detection.py
#!/usr/bin/env python3
"""Detect captcha/challenge/blocked pages and build placeholder pages for them."""

import html

# Lowercase substrings that mark bot-challenge / blocked pages
# (Cloudflare, Turnstile, generic captchas, WAF denials).
BLOCK_MARKERS = [
    "verify you are human",
    "just a moment",
    "attention required",
    "access denied",
    "cf-challenge",
    "turnstile",
    "captcha",
    "enable javascript and cookies to continue",
]


def detect_block(title, body_text, status=None):
    """Return a human-readable reason if the page looks blocked, else None."""
    if status is not None and status >= 400:
        return f"HTTP {status}"
    haystack = f"{title or ''} {(body_text or '')[:3000]}".lower()
    for marker in BLOCK_MARKERS:
        if marker in haystack:
            return f"page shows a challenge/blocked marker ('{marker}')"
    return None


def placeholder_html(url, title, reason):
    """One-page notice inserted where a blocked/unreachable page would have gone."""
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Could not capture</title></head>
<body style="font-family: Georgia, serif; margin: 3em;">
  <h1 style="color: #b00;">⚠️ Could not capture linked page</h1>
  <h2>{html.escape(title or url)}</h2>
  <p><strong>Reason:</strong> {html.escape(reason)}</p>
  <p><strong>Original URL:</strong><br>{html.escape(url)}</p>
  <p style="color: #666;">This page was linked from the article but could not be
     converted to PDF (blocked, timed out, or rejected automation).</p>
</body></html>"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_blocked_detection.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add modules/blocked_detection.py tests/test_blocked_detection.py
git commit -m "feat: add blocked-page detection and placeholder HTML builder"
```

---

### Task 3: Config defaults — link following on by default, global budget of 10

**Files:**
- Modify: `config/settings.py:95-96`
- Modify: `.env:32-38` (values that override the defaults)

**Interfaces:**
- Consumes: nothing.
- Produces: `FOLLOW_ARTICLE_LINKS` default `True`; `MAX_LINKED_PAGES` default `10`. (Names/locations otherwise unchanged; all existing imports keep working.)

- [ ] **Step 1: Change the defaults in `config/settings.py`**

Replace lines 95-96:

```python
FOLLOW_ARTICLE_LINKS = get_bool_env('FOLLOW_ARTICLE_LINKS', False)
MAX_LINKED_PAGES = get_int_env('MAX_LINKED_PAGES', 3)
```

with:

```python
FOLLOW_ARTICLE_LINKS = get_bool_env('FOLLOW_ARTICLE_LINKS', True)
# Global cap on captured link sections across ALL depth levels
# (placeholder pages for blocked links count against it too)
MAX_LINKED_PAGES = get_int_env('MAX_LINKED_PAGES', 10)
```

- [ ] **Step 2: Update `.env` so the local value matches the new global-cap meaning**

In `.env`, change the line `MAX_LINKED_PAGES=3` to:

```
MAX_LINKED_PAGES=10
```

(`.env` line 32 already has `FOLLOW_ARTICLE_LINKS=true` and line 38 `LINK_FOLLOW_DEPTH=1` — leave those.)

- [ ] **Step 3: Verify the loaded values**

Run: `.venv/bin/python -c "from config.settings import FOLLOW_ARTICLE_LINKS, MAX_LINKED_PAGES, LINK_FOLLOW_DEPTH, LINKED_PAGE_TIMEOUT; print(FOLLOW_ARTICLE_LINKS, MAX_LINKED_PAGES, LINK_FOLLOW_DEPTH, LINKED_PAGE_TIMEOUT)"`
Expected output: `True 10 1 15`

- [ ] **Step 4: Run the full test suite to confirm nothing depended on the old defaults**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: all tests pass (same set that passed before this task).

- [ ] **Step 5: Commit**

```bash
git add config/settings.py .env
git commit -m "feat: enable link following by default; MAX_LINKED_PAGES becomes global cap of 10"
```

---

### Task 4: BFS capture loop in `LinkProcessor` + nested bookmarks + full-map link rewrite

This task rewires `modules/link_processor.py` to use `LinkQueue` and `blocked_detection`, replacing the single-level `follow_links` flow. The public methods `process_article_with_links()` and `get_processing_summary()` keep their signatures.

**Files:**
- Modify: `modules/link_processor.py`
- Test: existing `tests/test_link_filtering.py`, `tests/test_link_processor_race.py` must stay green (they cover `extract_links` and `_temp_dir_for`, which are kept).

**Interfaces:**
- Consumes (from Tasks 1-3):
  - `from modules.link_queue import LinkQueue, LinkTarget, normalize_url`
  - `from modules.blocked_detection import detect_block, placeholder_html`
  - Settings: `LINK_FOLLOW_DEPTH`, `MAX_LINKED_PAGES` (global cap), `LINKED_PAGE_TIMEOUT`, `MAX_CONCURRENT_LINKS`.
  - `browser_manager.create_new_page()`, `browser_manager.close_page(page)`, `browser_manager.remove_header_elements_from_page(page)` (existing, `modules/browser_manager.py:321,333,446`).
- Produces (internal to `LinkProcessor`):
  - `async capture_linked_pages(seed_links: list[dict], main_url: str, temp_dir: Path) -> list[dict]` — section dicts: `{'url', 'title', 'pdf', 'depth', 'parent_url', 'blocked', 'child_links'}` in BFS order.
  - `async _capture_one(target: LinkTarget, index: int, temp_dir: Path) -> dict | None`
  - `async merge_sections(sections: list[dict], output_filename: str) -> bool` — replaces `merge_pdfs`; annotates each merged section with `'start_page'`.
  - `_add_internal_links(pdf_path, sections)` — now also builds the nested outline.

- [ ] **Step 1: Update imports and delete superseded methods**

In `modules/link_processor.py`:

1. Remove `import json` (only used by the dead `replace_links_with_page_refs`).
2. Add after the existing `from pathlib import Path`:

```python
from modules.link_queue import LinkQueue, normalize_url
from modules.blocked_detection import detect_block, placeholder_html
```

3. Delete these methods entirely (superseded by the BFS loop; nothing outside this file references them — verified via grep):
   - `load_and_analyze_page` (lines ~309-342)
   - `follow_links` (lines ~662-698)
   - `_follow_single_link` (lines ~700-739)
   - `replace_links_with_page_refs` (lines ~741-834)
   - `generate_multi_page_pdf` (lines ~836-885)
   - `generate_single_page_pdf` (lines ~887-917)
   - `merge_pdfs` (lines ~952-1070) — replaced by `merge_sections` in Step 3
   - the `_normalize_url` staticmethod (lines ~1072-1075) — use `normalize_url` from `modules.link_queue`
4. Remove the now-unused `REPLACE_LINKS_WITH_PDF_REFS` name from the `config.settings` import list at the top.

- [ ] **Step 2: Add the BFS capture methods**

Add to `LinkProcessor` (after `should_follow_link`):

```python
    async def capture_linked_pages(self, seed_links, main_url, temp_dir):
        """BFS-capture linked pages up to LINK_FOLLOW_DEPTH, bounded by the
        global MAX_LINKED_PAGES budget. Returns section dicts in BFS order."""
        queue = LinkQueue(max_depth=LINK_FOLLOW_DEPTH, budget=MAX_LINKED_PAGES,
                          exclude_urls=[main_url])
        for link in seed_links:
            queue.add(link['url'], 1, link.get('text', ''), parent_url=main_url)

        sections = []
        counter = 0
        while queue.pending():
            level = queue.pop_level()
            print(f"🔗 Capturing {len(level)} pages at depth {level[0].depth} "
                  f"(budget: {queue.accepted}/{queue.budget})")
            semaphore = asyncio.Semaphore(MAX_CONCURRENT_LINKS)

            async def capture(target, index):
                async with semaphore:
                    return await self._capture_one(target, index, temp_dir)

            tasks = [capture(t, counter + i) for i, t in enumerate(level)]
            counter += len(level)
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for target, result in zip(level, results):
                if isinstance(result, Exception) or result is None:
                    print(f"  ❌ Dropped {target.url}: {result}")
                    continue
                sections.append(result)
                # Feed this page's links into the next BFS level
                for child in result.pop('child_links'):
                    queue.add(child['url'], target.depth + 1,
                              child.get('text', ''), parent_url=target.url)
        return sections

    async def _capture_one(self, target, index, temp_dir):
        """Capture one linked page to PDF; insert a placeholder page if the
        page is blocked (captcha/HTTP error/timeout/too-small render)."""
        page = await self.browser_manager.create_new_page()
        if not page:
            return None
        reason = None
        title = target.link_text or target.url
        child_links = []
        pdf_path = temp_dir / f"linked_{index:03d}_{self.sanitize_filename(title)}.pdf"
        try:
            try:
                response = await page.goto(target.url,
                                           timeout=LINKED_PAGE_TIMEOUT * 1000,
                                           wait_until='domcontentloaded')
                await asyncio.sleep(2)
                status = response.status if response else None
                page_title = await page.title()
                if page_title:
                    title = page_title
                body_text = await page.evaluate(
                    "() => document.body ? document.body.innerText.slice(0, 3000) : ''")
                reason = detect_block(title, body_text, status)
            except Exception as e:
                reason = f"navigation failed ({type(e).__name__})"

            if reason is None:
                # Extract child links for the next BFS level while the DOM is live
                content = await page.content()
                soup = BeautifulSoup(content, 'html.parser')
                child_links = self.extract_links(soup, target.url)

                await self.browser_manager.remove_header_elements_from_page(page)
                await page.pdf(
                    path=str(pdf_path),
                    format='A4',
                    margin={'top': '0.75in', 'right': '0.75in',
                            'bottom': '0.75in', 'left': '0.75in'},
                    print_background=True,
                    prefer_css_page_size=False
                )
                if not pdf_path.exists() or pdf_path.stat().st_size < 10000:
                    reason = "rendered PDF too small (likely an error page)"
                    child_links = []

            if reason is not None:
                print(f"  ⚠️ {target.url}: {reason} — inserting placeholder page")
                await page.set_content(placeholder_html(target.url, title, reason))
                await page.pdf(path=str(pdf_path), format='A4',
                               print_background=True)

            print(f"  ✅ [{index}] depth {target.depth}: {title[:50]}"
                  f"{' (placeholder)' if reason else ''}")
            return {
                'url': target.url,
                'title': title,
                'pdf': str(pdf_path),
                'depth': target.depth,
                'parent_url': target.parent_url,
                'blocked': reason,
                'child_links': child_links,
            }
        except Exception as e:
            print(f"  ❌ Error capturing {target.url}: {e}")
            return None
        finally:
            await self.browser_manager.close_page(page)
```

- [ ] **Step 3: Add `merge_sections` and rewrite `_add_internal_links` (nested outline + full URL map)**

Add `merge_sections` where `merge_pdfs` used to be, and replace `_add_internal_links` entirely:

```python
    async def merge_sections(self, sections, output_filename):
        """Merge section PDFs in order, record each section's start page,
        then add the nested outline and internal links."""
        try:
            from PyPDF2 import PdfMerger, PdfReader
        except ImportError:
            print("❌ PyPDF2 not available — cannot merge")
            return False

        merger = PdfMerger()
        start_page = 0
        merged = []
        for section in sections:
            pdf_file = section['pdf']
            if not Path(pdf_file).exists():
                print(f"  ⚠️ Skipping missing file: {pdf_file}")
                continue
            try:
                num_pages = len(PdfReader(pdf_file).pages)
                merger.append(pdf_file)
                section['start_page'] = start_page
                start_page += num_pages
                merged.append(section)
                print(f"  ✅ Added {Path(pdf_file).name} ({num_pages} pages)")
            except Exception as e:
                print(f"  ❌ Failed to add {Path(pdf_file).name}: {e}")

        if not merged:
            print("❌ No PDFs could be added to merger")
            return False

        with open(output_filename, 'wb') as f:
            merger.write(f)
        merger.close()

        await self._add_internal_links(output_filename, merged)
        return True

    async def _add_internal_links(self, pdf_path, sections):
        """Add a nested bookmark outline and rewrite URI link annotations
        pointing at included sections as internal GoTo destinations."""
        try:
            from PyPDF2 import PdfReader, PdfWriter
            from PyPDF2.generic import ArrayObject, NameObject, FloatObject

            url_to_page = {normalize_url(s['url']): s['start_page']
                           for s in sections if s.get('url')}

            print(f"🔗 Building outline and internal links "
                  f"({len(sections)} sections, {len(url_to_page)} target URLs)...")

            reader = PdfReader(pdf_path)
            writer = PdfWriter()
            for page in reader.pages:
                writer.add_page(page)

            # Nested outline: each section's bookmark hangs under its parent's
            # (level-1 pages under Main Article, level-2 under their level-1 page).
            outline_items = {}
            for s in sections:
                parent = outline_items.get(normalize_url(s.get('parent_url') or ''))
                item = writer.add_outline_item(s['title'], s['start_page'],
                                               parent=parent)
                outline_items[normalize_url(s['url'])] = item

            rewrites = 0
            for page in writer.pages:
                if '/Annots' not in page:
                    continue
                for annot_ref in page['/Annots']:
                    try:
                        annot = annot_ref.get_object()
                        if annot.get('/Subtype') != '/Link':
                            continue
                        action = annot.get('/A')
                        if action is None:
                            continue
                        action = action.get_object()
                        if action.get('/S') != '/URI':
                            continue
                        uri = str(action.get('/URI', ''))
                        norm = normalize_url(uri)
                        if norm not in url_to_page:
                            continue
                        target_idx = url_to_page[norm]
                        if target_idx >= len(writer.pages):
                            continue
                        target_page = writer.pages[target_idx]
                        target_height = float(target_page.mediabox.height)
                        del annot[NameObject('/A')]
                        annot[NameObject('/Dest')] = ArrayObject([
                            target_page.indirect_reference,
                            NameObject('/FitH'),
                            FloatObject(target_height)
                        ])
                        rewrites += 1
                    except Exception:
                        continue

            with open(pdf_path, 'wb') as f:
                writer.write(f)

            print(f"✅ Rewrote {rewrites} in-text links as internal navigation; "
                  f"{len(sections)} bookmarked sections")

        except Exception as e:
            print(f"⚠️ Could not add outline/internal links: {e}")
            import traceback
            traceback.print_exc()
```

- [ ] **Step 4: Rewire `process_article_with_links` Steps 3-5**

In `process_article_with_links`, replace everything from the comment `# Step 3: Follow links and collect page info` down to (and including) the `elif len(pdf_pages) == 1:` / `else:` merge block (currently lines ~177-249) with:

```python
            # Step 3: BFS-capture linked pages (extracts deeper links as it goes)
            linked_sections = await self.capture_linked_pages(links, article_url, temp_dir)

            if not linked_sections:
                print("📄 No accessible linked pages, using main article PDF only")
                import shutil
                shutil.move(str(main_pdf), output_filename)
                return True

            placeholders = sum(1 for s in linked_sections if s['blocked'])
            print(f"📄 Captured {len(linked_sections)} linked sections "
                  f"({placeholders} placeholders), merging...")

            # Step 4: Merge main + linked sections with nested bookmarks and
            # internal link rewriting
            sections = [{'url': article_url, 'title': 'Main Article',
                         'pdf': str(main_pdf), 'depth': 0,
                         'parent_url': None, 'blocked': None}]
            sections.extend(linked_sections)

            success = await self.merge_sections(sections, output_filename)

            if success and Path(output_filename).exists():
                final_size = Path(output_filename).stat().st_size
                print(f"✅ Final merged PDF: {final_size} bytes")
                # Keep the summary map for get_processing_summary()
                self.link_to_page_map = {s['url']: s['start_page'] + 1
                                         for s in sections if 'start_page' in s}
            else:
                print(f"❌ Merge failed, using main article only")
                import shutil
                shutil.copy(str(main_pdf), output_filename)
                success = True

            pdf_pages = [s['pdf'] for s in sections]
```

The replaced range includes the old `link_to_page_map`/`page_titles`/`page_urls` bookkeeping — it all belonged to the single-level flow and must not survive. Leave the existing cleanup block (`# Cleanup temp files...` through the `finally:`) untouched — `pdf_pages` is re-set above so the cleanup loop still removes every intermediate PDF. Keep the `if not links:` early-exit block (lines ~170-175) as is.

- [ ] **Step 5: Run the existing test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: all tests pass, including `test_link_filtering.py` (uses `extract_links` — unchanged) and `test_link_processor_race.py` (uses `_temp_dir_for` — unchanged). Also verify the module imports cleanly:

Run: `.venv/bin/python -c "from modules.link_processor import LinkProcessor; lp = LinkProcessor(None); print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add modules/link_processor.py
git commit -m "feat: multi-level BFS link following with placeholders and nested bookmarks"
```

---

### Task 5: End-to-end verification (depth 1 and depth 2)

**Files:**
- Create: `scratch_e2e_links.py` (repo root, temporary — deleted before commit)

**Interfaces:**
- Consumes: `BrowserManager.start_browser_session()` / `.close_browser_session()` (`modules/browser_manager.py:29,63`), `LinkProcessor.process_article_with_links()`.
- Produces: nothing committed (verification only).

- [ ] **Step 1: Write the E2E script**

```python
# scratch_e2e_links.py
"""E2E check: convert one real article with link following, then inspect the PDF."""
import asyncio
import sys
from modules.browser_manager import BrowserManager
from modules.link_processor import LinkProcessor

URL = sys.argv[1] if len(sys.argv) > 1 else "https://thedispatch.com/newsletter/morning/"
OUT = sys.argv[2] if len(sys.argv) > 2 else "e2e_test.pdf"


async def run():
    bm = BrowserManager()
    if not await bm.start_browser_session():
        print("FATAL: browser session failed")
        return 1
    try:
        lp = LinkProcessor(bm)
        ok = await lp.process_article_with_links(URL, OUT)
        print(f"\nRESULT: {'SUCCESS' if ok else 'FAILURE'}")
        print(f"SUMMARY: {lp.get_processing_summary()}")
        return 0 if ok else 1
    finally:
        await bm.close_browser_session()

sys.exit(asyncio.run(run()))
```

- [ ] **Step 2: Run at depth 1 (default config)**

Run: `.venv/bin/python scratch_e2e_links.py "https://thedispatch.com/newsletter/morning/" e2e_d1.pdf`
Expected: `RESULT: SUCCESS`; console shows `Capturing N pages at depth 1` and a merged multi-section PDF. (Pick today's TMD URL if the newsletter index page doesn't convert well — any recent article URL from `processed_articles.json`/tracking output works.)

- [ ] **Step 3: Run at depth 2**

Run: `LINK_FOLLOW_DEPTH=2 .venv/bin/python scratch_e2e_links.py "https://thedispatch.com/newsletter/morning/" e2e_d2.pdf`
Expected: `RESULT: SUCCESS`; console shows captures at depth 1 AND depth 2 (if level-1 pages contain qualifying links), total sections ≤ 10 (`MAX_LINKED_PAGES`).

- [ ] **Step 4: Inspect the PDF structure programmatically**

```python
# scratch_inspect_pdf.py
"""Verify outline nesting and internal GoTo links in the merged PDF."""
import sys
from PyPDF2 import PdfReader

r = PdfReader(sys.argv[1] if len(sys.argv) > 1 else "e2e_d1.pdf")
print(f"pages: {len(r.pages)}")


def walk(outline, depth=0):
    for item in outline:
        if isinstance(item, list):
            walk(item, depth + 1)
        else:
            print("  " * depth + f"- {item.title} -> page {r.get_page_number(item.page)}")


print("outline:")
walk(r.outline)

internal = external = 0
for page in r.pages:
    for a in (page.get("/Annots") or []):
        a = a.get_object()
        if a.get("/Subtype") != "/Link":
            continue
        if "/Dest" in a:
            internal += 1
        elif a.get("/A") and a["/A"].get_object().get("/S") == "/URI":
            external += 1
print(f"internal GoTo links: {internal}, external URI links: {external}")
```

Run: `.venv/bin/python scratch_inspect_pdf.py e2e_d1.pdf` and `.venv/bin/python scratch_inspect_pdf.py e2e_d2.pdf`
Expected: outline lists Main Article with linked sections nested beneath it (depth-2 sections indented one level further); `internal GoTo links` > 0 when linked sections were captured.

- [ ] **Step 5: Clean up scratch files, run full suite, commit any fixes**

```bash
rm -f scratch_e2e_links.py scratch_inspect_pdf.py e2e_d1.pdf e2e_d2.pdf
.venv/bin/python -m pytest tests/ -v
```

Expected: all tests pass. If E2E surfaced bug fixes in Task 4 code, commit them:

```bash
git add modules/link_processor.py
git commit -m "fix: address issues found in link-following E2E verification"
```
