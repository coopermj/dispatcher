# Design: Fix LinkProcessor Race Condition + CLI URL Argument

**Date:** 2026-03-21

---

## Problem

Two issues:

1. **Wrong article in PDF** — When `FOLLOW_ARTICLE_LINKS=true` and `MAX_CONCURRENT_CONVERSIONS>1`, all parallel article conversions share one `LinkProcessor` instance. Each call to `process_article_with_links` resets and overwrites `self._active_page`, `self.processed_links`, and `self.link_to_page_map`. The PDF for one article gets generated using the browser page navigated to by a different concurrent article — correct filename, wrong content.

2. **No way to target a specific URL** — The only way to run the converter is via email mode (scans Gmail) or website mode (scans thedispatch.com). There is no way to pass a specific URL on the command line and generate a PDF directly.

---

## Fix 1: LinkProcessor Race Condition

**Root cause:** `self.link_processor = LinkProcessor(self.browser_manager)` in `DispatchConverter.__init__` creates one instance shared across all parallel `process_single_item_parallel` coroutines.

**Fix:** Remove `self.link_processor` from `__init__`. In `process_single_item_parallel`, instantiate `LinkProcessor(self.browser_manager)` as a local variable immediately before calling `process_article_with_links`. Each article gets its own isolated instance — no shared mutable state across any of the five overwritten fields: `_active_page`, `_owns_page`, `processed_links`, `link_to_page_map`, and `current_page_number`.

The existing call to `link_processor.get_processing_summary()` immediately after `process_article_with_links` (line ~324 in `main.py`) remains valid — the local `link_processor` variable is still in scope at that point, so stats are captured correctly before the instance is discarded.

The existing `page=page` argument passed to `process_article_with_links` is already handled correctly inside `LinkProcessor` — no changes to `LinkProcessor` internals are needed.

**Files changed:** `main.py` only (remove instance var assignment in `__init__`, add local instantiation in `process_single_item_parallel`).

---

## Fix 2: CLI URL Argument

**Behavior:** `python main.py --url https://thedispatch.com/article/...`

- Authenticates and starts browser normally (same as any other run)
- Respects all `.env` settings (`FOLLOW_ARTICLE_LINKS`, `UPLOAD_TO_REMARKABLE`, etc.)
- Always skips the "already processed" duplicate check (`force_reprocess=True` for this path)
- Derives the PDF filename from the URL slug (e.g. `neon-genesis-evangelion-american-millennials` → `Neon Genesis Evangelion American Millennials`)
- Calls the existing `process_single_item_parallel` logic — no new conversion code

**Implementation:**
- At the top of `main()` (before `DispatchConverter()` is instantiated), parse `sys.argv` with `argparse`. The `--url` flag is optional. If present, its value is passed down into the converter call. Do not put argument parsing in the `if __name__ == "__main__"` block — it must be inside `main()` so the parsed value is reachable.
- Add `process_single_url(url)` method on `DispatchConverter` that:
  - Builds a `content_data` dict with at minimum: `subject` (human-readable slug derived from URL path, e.g. `"Neon Genesis Evangelion American Millennials"`), `read_online_url` (the URL as-is), `message_id` (`f"url_{hash(url)}"`), `sender` (`"CLI"`), `date` (current datetime ISO string), `body` (`""`), `raw_body` (`f"<a href='{url}'>{subject}</a>"`), `is_html` (`True`), `source` (`"cli"`)
  - Calls `process_single_item_parallel(content_data, index=1, force_reprocess=True, effective_mode='website')` to bypass the duplicate check and ensure link following runs
- Add two optional parameters to `process_single_item_parallel`:
  - `force_reprocess: bool = False` — when `True`, skip the `tracking_manager.is_email_processed()` check entirely
  - `effective_mode: str = None` — when set, use this value instead of `self.processing_mode` for both the filename prefix (`dispatch_website`) and the `FOLLOW_ARTICLE_LINKS` guard. This avoids mutating `self.processing_mode` and the exception-safety issues that would create.
  - Existing callers (`process_items_parallel`) pass neither argument, so they default to current behavior — no change.
- CLI-processed articles will have filename prefix `dispatch_website` (consistent with website-mode articles since `effective_mode='website'`). This is intentional and acceptable.
- Cross-mode deduplication gap: CLI uses `message_id: f"url_{hash(url)}"` while website mode uses `f"website_{hash(url)}"`. Because `force_reprocess=True` is always set for CLI mode, this gap only matters in the reverse direction (website mode may re-process a CLI-processed article). This is acceptable by design — the user explicitly targeted the URL via CLI, and normal website-mode deduplication is based on the website-mode tracking data.
- When `--url` is given, call `process_single_url` instead of `process_content`

**Files changed:** `main.py` only.

---

## Non-goals

- No changes to `LinkProcessor` internals
- No changes to `BrowserManager`, `WebsiteScanner`, or `TrackingManager`
- No new config settings
