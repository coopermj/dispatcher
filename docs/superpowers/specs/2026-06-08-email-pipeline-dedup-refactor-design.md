# Email pipeline de-duplication refactor + live reMarkable inventory dedup

**Date:** 2026-06-08
**Status:** Approved (brainstorming)

## Problem

`email_converter.py`'s `DispatchPersistentConverter` re-implements logic that already
exists in `modules/`: Google OAuth + Dispatch login (`AuthManager`), browser session /
cookies / header removal / PDF conversion (`BrowserManager`), Gmail search + body /
read-online-URL extraction (`EmailHandler`), and tracking (`TrackingManager`). ~900 lines
of duplication. Fixes therefore land in one pipeline but not the other (e.g. the login
indicator fix, the `\b` regex fix).

Separately, duplicate prevention relies entirely on local JSON tracking files, which can
drift from the actual reMarkable device state.

## Goals

1. `email_converter.py` reuses the shared `modules/` components instead of duplicating them.
2. Only gather content from the **last 7 days** (both pipelines).
3. Prevent duplicate uploads by checking the **live reMarkable News inventory** before
   uploading, in addition to local tracking.

Non-goals: collapsing the email pipeline into the modular email mode (rejected — keep the
two pipelines and their two tracking files / output dirs). No unrelated refactoring.

## Design

### 1. `email_converter.py` becomes a thin orchestrator

`DispatchPersistentConverter` keeps only email-pipeline orchestration:
- the `process_emails` loop,
- cross-dedup against the web tracking file,
- output dir `dispatch_persistent_pdfs/`,
- `run_email_converter()` entry point.

It delegates everything else:
- Google OAuth + Dispatch login → `AuthManager`
- browser session, cookies, header removal, `convert_url_to_pdf` → `BrowserManager`
- Gmail search / body / read-online-URL → `EmailHandler`
- tracking → `TrackingManager`
- reMarkable → `ReMarkableManager` (already done)

Deleted duplicated methods: `authenticate`, `authenticate_with_dispatch`,
`start_browser_session`, `close_browser_session`, `save_cookies`, `load_cookies`,
`test_authentication`, `remove_header_elements`, `convert_url_to_pdf`,
`save_html_snapshot`, `sanitize_filename`, and the inline tracking methods
(`load/save_tracking_data`, `get_email_fingerprint`, `is_email_processed`,
`mark_email_processed`, `cleanup_tracking_data`, `print_tracking_summary`, etc.),
plus `extract_body` / `extract_email_data` / `extract_read_online_url` /
`search_dispatch_emails` / `get_message_content`.

### 2. `TrackingManager` configurable tracking-file path

`TrackingManager.__init__(self, tracking_file=None)` — defaults to the existing
`TRACKING_FILE`. The email pipeline instantiates
`TrackingManager(tracking_file=DISPATCH_EMAIL_TRACKING_FILE)`. All other callers
unchanged. `save_tracking_data` / `load_tracking_data` use `self.tracking_file`.

New setting in `config/settings.py`:
`DISPATCH_EMAIL_TRACKING_FILE = BASE_DIR / 'dispatch_email_tracking.json'`
(absolute, like the other tracking paths).

### 3. One-time `success` backfill

Existing `dispatch_email_tracking.json` entries lack the `success` field that
`TrackingManager.is_email_processed` requires. On load, any entry without `success`
gets `success: True` backfilled (every entry represents a completed conversion).
Idempotent. With the inventory check (below) this is an optimization to avoid wasted
re-conversion, not a safety-critical guard.

### 4. 7-day gather window

- **Email:** `GMAIL_SEARCH_QUERY` default becomes `from:@thedispatch.com newer_than:7d`.
  Gmail filters at the source on reliable message dates.
- **Website:** `ARTICLE_AGE_LIMIT_DAYS` default changes `30 → 7`.
  Existing behavior retained: articles whose date can't be parsed from the listing page
  still pass the age filter (dating every link would require opening each article). The
  inventory check is the backstop for those.

### 5. Live reMarkable inventory dedup (`ReMarkableManager`)

- `refresh_inventory(folder_name=None)` — runs `rmapi -json ls /<folder>` once, parses
  `DocumentType` entries, stores a set of normalized document names. Falls back to plain
  `rmapi ls` if `-json` unavailable; on any failure the inventory is empty (fail-open:
  never block uploads because the check broke).
- `document_exists(title)` — normalizes `title` (lowercase, strip non-alphanumerics —
  same `normalize()` as `prune_news.py`) and checks membership. Matching on normalized
  title (not filename) is robust to the per-run `dispatch_NNN_` index prefix and the
  `.pdf` extension `rmapi` strips on upload.
- Both pipelines, before uploading: if `document_exists(subject)`, skip the upload, log
  it, and mark the tracking entry `remarkable_uploaded: True` (self-healing). Inventory
  is fetched once per run and cached.

### 6. `remarkable_expired` retained

The `remarkable_expired` flag (set by `prune_news.py`) still blocks re-gather/re-upload
of pruned content. The device inventory can't express "had it, deleted on purpose," so
this stays. Within the 7-day window a pruned-but-recent item must not be re-uploaded.

### 7. Behavior changes (intentional)

- Email pipeline now respects `BROWSER_HEADLESS` from `.env` (was hardcoded headed) and
  gains the off-screen-window behavior — unifying with the website pipeline.

### Behavior preserved

Two tracking files, `dispatch_persistent_pdfs/` output, cross-dedup against the web
tracking file, `remarkable_expired` semantics, `--retry-uploads` and `prune_news.py`.

## Error handling

- Inventory fetch fails → empty inventory, uploads proceed (fail-open). Logged.
- `-json` flag unsupported → fall back to plain `ls` name parsing.
- Backfill only adds the field; never deletes or rewrites other fields.

## Testing

- `TrackingManager(tracking_file=...)` reads/writes the given path; default unchanged.
- Backfill: entry without `success` → `is_email_processed` returns True after load.
- `ReMarkableManager.document_exists`: normalized match across index prefix / `.pdf` /
  case / punctuation; absent title → False; inventory-fetch failure → False (fail-open).
- Existing 8 tests stay green.
- Manual dry-run against a copy of the real `dispatch_email_tracking.json` to confirm no
  duplicate re-uploads.
