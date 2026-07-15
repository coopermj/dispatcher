# Multi-Level Link Following — Design

**Date:** 2026-05-29
**Status:** Approved

## Goal

When converting an article (e.g. today's TMD → `tmd.pdf`), follow HTTP links in the
content to a configurable depth (default 1), convert each linked page to PDF pages,
append them at the end of the main PDF, and rewrite the in-text links as internal
PDF jumps to those appended sections.

Much of this exists in `modules/link_processor.py` for depth 1. This design makes
depth configurable (actually honoring `LINK_FOLLOW_DEPTH`), hardens blocked-page
handling, and flips link-following on by default.

## Approach

**BFS worklist with a global budget.** A single queue of
`(url, depth, link_text, parent_url)` entries seeded from the main article's links.
Each processed page contributes its own (filtered) links to the queue at `depth+1`.
Processing stops when the queue is empty, entries exceed `LINK_FOLLOW_DEPTH`, or the
global `MAX_LINKED_PAGES` budget is spent.

Rejected alternatives:
- *Recursive per-page processing* — nests PDF merges, complicates page numbering.
- *Crawl-then-convert two-pass* — loads every page twice (once to scrape links,
  once to print), doubling wall-clock time and blocking risk.

## Configuration (`config/settings.py` / `.env`)

| Setting | Old | New |
|---|---|---|
| `FOLLOW_ARTICLE_LINKS` | `False` | **`True`** (default on) |
| `LINK_FOLLOW_DEPTH` | `1` (ignored) | `1` (**honored**; 2+ enables deeper crawl) |
| `MAX_LINKED_PAGES` | `3` (per article, single level) | `10`, **global cap across all levels** |
| `LINKED_PAGE_TIMEOUT` | `15` (partially ignored; 30s hardcoded in `page.goto`) | `15`, **wired into all linked-page navigation** |

Level-1 links are enqueued before any level-2 link is discovered (BFS order), so
shallow links always win budget slots over deeper ones.

## Crawl loop (`LinkProcessor`)

- **Dedup by normalized URL** (strip query, fragment, trailing slash). A page linked
  from multiple places is captured once; every in-text link to it jumps to that one
  section. The main article's URL and already-captured URLs are never re-enqueued.
- While on each linked page (before printing it to PDF), extract its links using the
  same filters as the main article: excluded regions (related/recommended/comments/
  nav), `SKIP_DOMAINS`, `SKIP_LINK_PATTERNS`, Dispatch-specific exclusions, and the
  article-likeness heuristics. Enqueue survivors at `depth+1` if depth allows.
- Existing parallelism (`MAX_CONCURRENT_LINKS` semaphore, per-conversion temp dirs)
  is preserved; the BFS proceeds level by level so ordering stays deterministic.

## Blocked-page handling

Detection (any of):
- Navigation timeout (`LINKED_PAGE_TIMEOUT` seconds)
- HTTP status ≥ 400 on the main navigation response
- Captcha/challenge markers in title or body: "verify you are human",
  "just a moment", "attention required", Cloudflare/Turnstile markers,
  "access denied"
- Resulting PDF < 10 KB (existing heuristic, kept)

On detection: generate a **placeholder page** — "⚠️ Could not capture:
*title or URL*" with the reason and the original URL printed — merged into the slot
where the page would have gone. In-text links to that URL jump to the placeholder,
so the reader knows the source was attempted. Placeholders count against the
`MAX_LINKED_PAGES` budget (they occupy a slot they were granted).

## Link rewriting

The existing `_add_internal_links` pass already scans **every** page of the merged
PDF and rewrites URI annotations to GoTo destinations when the target URL is in the
URL→page map. Change: feed it the complete map including all depths and placeholder
sections. Result: links inside appended pages that point to other included pages
become internal jumps too. Links to non-included pages keep their web URLs.

## PDF structure

- Main article first; appended sections strictly at the end, in capture (BFS) order:
  all level-1 pages, then level-2, etc.
- Every appended section gets a bookmark. Deeper-level bookmarks are nested under
  their parent page's bookmark in the outline.

## Error handling

- Any failure in the multi-page path falls back to the existing single-page
  conversion (unchanged behavior).
- A placeholder that itself fails to generate is dropped silently (log only).

## Testing

- Unit: BFS budget/depth/dedup logic (pure worklist function, no browser).
- Unit: captcha-marker detection against canned HTML snippets.
- Integration: run one real TMD conversion with depth 1 and depth 2, verify page
  counts, bookmarks, and that in-text links resolve to internal destinations
  (inspect annotations with PyPDF2).
